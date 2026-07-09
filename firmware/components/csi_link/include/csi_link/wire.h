/* Wire format (host csi_host.framing protocol — single convention).
 * Note: This header is compiled on host for gcc cross-validation testing — no ESP headers allowed.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

#define CSIL_PAYLOAD_MAGIC  0xC51Eu
#define CSIL_FRAME_MAGIC    0xC51Du
#define CSIL_RAW_MAGIC      0xC51Fu
// Category1+OUI3+Random4+EID1+Len1+OUI3+Type1+Ver1 — basic offset, locked by runtime magic scan
#define CSIL_ESPNOW_HDR_LEN 15
#define CSIL_NUM_SC         56
#define CSIL_FRAME_LEN      130
#define CSIL_CSI_BUF_MAX    384  /* Upper bound for LLTF128 + HT-LTF128 + STBC128 */

/* ESP-NOW payload 16B (§4.2) — CSI measured in preamble, payload minimized for identification */
typedef struct __attribute__((packed)) {
    uint16_t magic;     /* 0xC51E */
    uint8_t  tx_idx;    /* 0..2 */
    uint8_t  rsv0;
    uint32_t seq;       /* Time alignment/loss measurement basis */
    uint8_t  rsv[8];
} csil_payload_t;
_Static_assert(sizeof(csil_payload_t) == 16, "payload must be 16B");

/* Serial CSI frame 130B (§4.3 table) */
typedef struct __attribute__((packed)) {
    uint16_t magic;        /* 0xC51D */
    uint8_t  rx_id;
    uint8_t  tx_idx;
    uint32_t seq;          /* Parsed from payload */
    uint32_t esp_timer_us; /* u32 — wraps in 71.58min, host unwrap (§5.2-0, v1.3) */
    int8_t   rssi;
    int8_t   noise_floor;
    uint8_t  len;          /* = 56 */
    uint8_t  boot_id;      /* Increments each boot — identifies seq/esp_timer reset (§4.3) */
    int8_t   iq[112];      /* I,Q alternating 56 pairs — device buf is [imag,real] → firmware swaps */
    uint16_t crc;          /* CRC16-CCITT-FALSE, applied to bytes [0,128) */
} csil_frame_t;
_Static_assert(sizeof(csil_frame_t) == CSIL_FRAME_LEN, "frame must be 130B");

/* RAW dump frame header 18B (§16-6 SC index measurement support) — followed by buf[buf_len] + crc16(header+buf) */
typedef struct __attribute__((packed)) {
    uint16_t magic;        /* 0xC51F */
    uint8_t  rx_id;
    uint8_t  tx_idx;
    uint32_t seq;
    uint32_t esp_timer_us;
    int8_t   rssi;
    int8_t   noise_floor;
    uint8_t  flags;        /* b0=first_word_invalid, b1..2=sig_mode */
    uint8_t  boot_id;
    uint16_t buf_len;
} csil_raw_hdr_t;
_Static_assert(sizeof(csil_raw_hdr_t) == 18, "raw header must be 18B");

uint16_t csil_crc16(const uint8_t *data, size_t len);
