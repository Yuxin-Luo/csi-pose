/* CSI receive module — callback (WiFi task) only handles payload offset lock, MAC pin, queue copy.
 * Frame composition/serial TX/NVS write is framer task's job (callback minimization).
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "esp_err.h"

#include "csi_link/wire.h"

typedef struct {
    uint32_t seq;
    uint32_t t_us;      /* (u32)esp_timer_get_time() — for host unwrap */
    uint8_t  tx_idx;
    int8_t   rssi;
    int8_t   noise;
    uint8_t  flags;     /* b0=first_word_invalid, b1..2=sig_mode */
    uint16_t buf_len;
    int8_t   buf[CSIL_CSI_BUF_MAX];
} csi_item_t;

typedef struct {
    uint32_t cb_total, magic_reject, mac_reject, q_drop;
    uint32_t per_tx[3];
    uint32_t pay_scans;
    int16_t  pay_off;   /* -1 = not locked (magic scan) */
} csi_rx_stats_t;

esp_err_t csi_rx_init(QueueHandle_t q);
void csi_rx_get_stats(csi_rx_stats_t *out);
bool csi_rx_get_pinned(int idx, uint8_t mac[6]);
void csi_rx_clear_macs(void);   /* Delete RAM+NVS pins */
void csi_rx_persist_macs(void); /* Write dirty pins to NVS — call from framer task context */
