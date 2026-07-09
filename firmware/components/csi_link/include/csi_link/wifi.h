#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

extern const uint8_t CSIL_BCAST[6]; /* FF:FF:FF:FF:FF:FF */

/* STA mode bringup: PS off, 11b rate disabled (DSSS has no OFDM CSI), channel fixed */
esp_err_t csil_wifi_start(uint8_t ch);

/* Strict order: esp_now_init → broadcast peer add_peer pre-registration →
 * esp_now_set_peer_rate_config(HT20/MCS0_LGI). TX only. */
esp_err_t csil_espnow_tx_init(void);

/* Activate RX-side ESP-NOW receive path (for CSI payload parsing — recv callback not needed) */
esp_err_t csil_espnow_rx_init(void);

/* Idle-only: full-channel scan → write "SCAN ch=n ap=k best=r" lines, then restore channel */
esp_err_t csil_wifi_scan_report(char *out, size_t cap, uint8_t restore_ch);
