/* NVS persistent configuration — namespace "csil".
 * Keys: idx (board index u8), ch (channel u8), bootcnt (u32), txm0..2 (MAC blob 6B).
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

esp_err_t csil_cfg_init(void);

uint8_t csil_cfg_get_u8(const char *key, uint8_t def);
void csil_cfg_set_u8(const char *key, uint8_t v);

// Increment boot counter, return lower 8 bits — frame boot_id (§4.3)
uint8_t csil_cfg_next_boot_id(void);

// TX MAC pin persistence for RX (idx 0..2). get returns true if exists
bool csil_cfg_get_mac(int idx, uint8_t mac[6]);
void csil_cfg_set_mac(int idx, const uint8_t mac[6]);
void csil_cfg_erase_macs(void);
