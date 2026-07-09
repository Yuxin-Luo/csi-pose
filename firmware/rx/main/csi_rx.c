#include "csi_rx.h"

#include <string.h>

#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"

#include "csi_link/cfg.h"
#include "csi_link/wifi.h"

static QueueHandle_t s_q;
static csi_rx_stats_t s_st = {.pay_off = -1};
static uint8_t s_mac[3][6];
static volatile bool s_mac_set[3];
static volatile bool s_mac_dirty[3];

static bool payload_at(const uint8_t *pl, uint16_t pln, int off, csil_payload_t *out)
{
    if (off < 0 || off + (int)sizeof *out > (int)pln)
        return false;
    memcpy(out, pl + off, sizeof *out);
    return out->magic == CSIL_PAYLOAD_MAGIC && out->tx_idx < 3;
}

/* WiFi task context — info/buf are freed on return, so copy to queue */
static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    s_st.cb_total++;
    if (!info || !info->buf || info->len == 0)
        return;
    const uint8_t *pl = info->payload;
    uint16_t pln = info->payload_len;
    csil_payload_t pay;
    if (!pl) {
        s_st.magic_reject++;
        return;
    }
    if (s_st.pay_off < 0) {
        /* Check default offset 15 → lock via magic scan if it fails */
        if (payload_at(pl, pln, CSIL_ESPNOW_HDR_LEN, &pay)) {
            s_st.pay_off = CSIL_ESPNOW_HDR_LEN;
        } else {
            s_st.pay_scans++;
            int lim = pln < 64 ? pln : 64;
            for (int i = 0; i + (int)sizeof pay <= lim; i++) {
                if (payload_at(pl, pln, i, &pay)) {
                    s_st.pay_off = (int16_t)i;
                    break;
                }
            }
            if (s_st.pay_off < 0) {
                s_st.magic_reject++;
                return;
            }
        }
    } else if (!payload_at(pl, pln, s_st.pay_off, &pay)) {
        s_st.magic_reject++; /* Other traffic's CSI — keep lock */
        return;
    }

    uint8_t t = pay.tx_idx;
    if (!s_mac_set[t]) {
        memcpy(s_mac[t], info->mac, 6);
        s_mac_set[t] = true;
        s_mac_dirty[t] = true; /* NVS write in framer task (avoid flash blocking) */
    } else if (memcmp(s_mac[t], info->mac, 6) != 0) {
        s_st.mac_reject++; /* tx_idx impersonation/duplicate board detected */
        return;
    }

    csi_item_t it;
    it.seq = pay.seq;
    it.t_us = (uint32_t)esp_timer_get_time();
    it.tx_idx = t;
    it.rssi = info->rx_ctrl.rssi;
    it.noise = info->rx_ctrl.noise_floor;
    it.flags = (info->first_word_invalid ? 1u : 0u) |
               (uint8_t)((info->rx_ctrl.sig_mode & 3u) << 1);
    uint16_t bl = info->len > CSIL_CSI_BUF_MAX ? CSIL_CSI_BUF_MAX : info->len;
    memcpy(it.buf, info->buf, bl);
    it.buf_len = bl;
    if (xQueueSend(s_q, &it, 0) == pdTRUE)
        s_st.per_tx[t]++;
    else
        s_st.q_drop++;
}

esp_err_t csi_rx_init(QueueHandle_t q)
{
    s_q = q;
    for (int i = 0; i < 3; i++)
        s_mac_set[i] = csil_cfg_get_mac(i, s_mac[i]); /* NVS pin restore */

    ESP_ERROR_CHECK(csil_espnow_rx_init());
    wifi_csi_config_t cc = {
        .lltf_en = true,        /* Isolate in LLTF region that discards first_word_invalid */
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,   /* Explicitly ON — ±27/28 behavior confirmed by M0 measurement dump */
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&cc));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    return ESP_OK;
}

void csi_rx_get_stats(csi_rx_stats_t *out)
{
    *out = s_st;
}

bool csi_rx_get_pinned(int idx, uint8_t mac[6])
{
    if (idx < 0 || idx > 2 || !s_mac_set[idx])
        return false;
    memcpy(mac, s_mac[idx], 6);
    return true;
}

void csi_rx_clear_macs(void)
{
    for (int i = 0; i < 3; i++) {
        s_mac_set[i] = false;
        s_mac_dirty[i] = false;
    }
    csil_cfg_erase_macs();
}

void csi_rx_persist_macs(void)
{
    for (int i = 0; i < 3; i++) {
        if (s_mac_dirty[i]) {
            csil_cfg_set_mac(i, s_mac[i]);
            s_mac_dirty[i] = false;
        }
    }
}
