/* CSI 接收模块 — 回调 (WiFi 任务) 里做 payload 偏移锁定·MAC pin·队列拷贝.
 * 帧组装/串口发送/NVS 写入由 framer 任务负责 (回调最小化). */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "esp_err.h"

#include "csi_link/wire.h"

typedef struct {
    uint32_t seq;
    uint32_t t_us;      /* (u32)esp_timer_get_time() —— 宿主 unwrap */
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
    int16_t  pay_off;   /* -1 = 未锁定 (在 magic 扫描) */
} csi_rx_stats_t;

esp_err_t csi_rx_init(QueueHandle_t q);
void csi_rx_get_stats(csi_rx_stats_t *out);
bool csi_rx_get_pinned(int idx, uint8_t mac[6]);
void csi_rx_clear_macs(void);   /* RAM+NVS pin 删除 */
void csi_rx_persist_macs(void); /* dirty pin 写 NVS —— framer 任务上下文调用 */
