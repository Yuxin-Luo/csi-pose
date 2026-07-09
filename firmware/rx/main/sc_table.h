/* PROVISIONAL until M0 dump (§16-6) — update and reflash after confirmed by csi_dump.py measurement.
 * word = 2-byte index in CSI buf, device order [imag, real].
 * SC order is low→high frequency: -28..-1, +1..+28 (§4.3). first_word_invalid (words 0~1) are
 * in both tables' unused regions (LLTF leading/guard) thanks to lltf_en=true.
 */
#pragma once
#include <stdint.h>

/* len >= 256: LLTF(words 0..63) + HT-LTF(words 64..127) — use HT-LTF region.
 * FFT order assumption: word 64+k → SC k (k=0..31), word 64+32+k → SC -32+k (k=0..31)
 * → SC -28..-1 = words 100..127, SC +1..+28 = words 65..92 */
static const uint8_t SC_WORD_HTLTF[56] = {
    100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113,
    114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127,
     65,  66,  67,  68,  69,  70,  71,  72,  73,  74,  75,  76,  77,  78,
     79,  80,  81,  82,  83,  84,  85,  86,  87,  88,  89,  90,  91,  92,
};

/* len == 128: single LTF region fallback.
 * SC -28..-1 = words 36..63, SC +1..+28 = words 1..28 */
static const uint8_t SC_WORD_SINGLE[56] = {
     36,  37,  38,  39,  40,  41,  42,  43,  44,  45,  46,  47,  48,  49,
     50,  51,  52,  53,  54,  55,  56,  57,  58,  59,  60,  61,  62,  63,
      1,   2,   3,   4,   5,   6,   7,   8,   9,  10,  11,  12,  13,  14,
     15,  16,  17,  18,  19,  20,  21,  22,  23,  24,  25,  26,  27,  28,
};
