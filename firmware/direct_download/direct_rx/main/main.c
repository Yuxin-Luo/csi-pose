/* RX 板直烧版——上电即进入流模式, 不再需要串口敲 START. 模板代码, 烧不同编号的板时:
 *
 *   (1) 修改下面的 BOARD_IDX  (rx0=0, rx1=1, rx2=2)
 *   (2) 修改 项目级 CMakeLists.txt 里的 project() 名字 (csi_rx0 / csi_rx1 / csi_rx2)
 *   (3) idf.py set-target esp32s3 && idf.py build && idf.py -p COMx flash
 *
 * 核心代码取自 firmware/rx/main/main.c (csi-pose 项目).
 * 附带的 csi_rx.c / csi_rx.h / sc_table.h 直接从原 firmware/rx/main/ 复制, 不改.
 */
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_mac.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "csi_link/cfg.h"
#include "csi_link/console.h"
#include "csi_link/serial_io.h"
#include "csi_link/wifi.h"
#include "csi_link/wire.h"

#include "csi_rx.h"
#include "sc_table.h"

#define FW_VER "m0.1-d"          /* d = direct_download 变体 */
#define BOARD_IDX 2              /* 【改这里】rx0=0, rx1=1, rx2=2  */
#define BOARD_CH  6              /* 【改这里】必须与 direct_tx 的 BOARD_CH 相同 */

#define QUEUE_DEPTH 64

typedef enum { M_IDLE, M_STREAM, M_RAW } run_mode_t;

static QueueHandle_t s_q;
static volatile run_mode_t s_mode = M_IDLE;
static volatile uint32_t s_raw_left;
static uint8_t s_rx_id, s_ch, s_boot_id;
static uint32_t s_framed, s_not_ht, s_short_buf;

static void framer_task(void *arg)
{
    csi_item_t it;
    for (;;) {
        if (xQueueReceive(s_q, &it, pdMS_TO_TICKS(100)) != pdTRUE) {
            csi_rx_persist_macs(); /* 空闲时把 MAC pin 写回 NVS */
            continue;
        }
        if (s_mode == M_STREAM) {
            if (((it.flags >> 1) & 3u) != 1u) { /* 非 HT — 11b/g CSI 不支持 56SC */
                s_not_ht++;
                continue;
            }
            const uint8_t *tab;
            if (it.buf_len >= 256)
                tab = SC_WORD_HTLTF;
            else if (it.buf_len >= 128)
                tab = SC_WORD_SINGLE;
            else {
                s_short_buf++;
                continue;
            }
            csil_frame_t f = {
                .magic = CSIL_FRAME_MAGIC,
                .rx_id = s_rx_id,
                .tx_idx = it.tx_idx,
                .seq = it.seq,
                .esp_timer_us = it.t_us,
                .rssi = it.rssi,
                .noise_floor = it.noise,
                .len = CSIL_NUM_SC,
                .boot_id = s_boot_id,
            };
            for (int k = 0; k < CSIL_NUM_SC; k++) {
                int w = tab[k];
                f.iq[2 * k]     = it.buf[2 * w + 1]; /* I = real — 设备 [imag,real] 反序 */
                f.iq[2 * k + 1] = it.buf[2 * w];     /* Q = imag */
            }
            f.crc = csil_crc16((const uint8_t *)&f, 128);
            csil_serial_write(&f, sizeof f);
            s_framed++;
        }
        /* M_RAW 不参与 RX 直烧路径 —— 仅调试时通过 RAW n 命令触发 */
    }
}

static void banner(void)
{
    uint8_t m[6];
    esp_read_mac(m, ESP_MAC_WIFI_STA);
    csil_reply("BOOT role=rx idx=%d mac=%02X:%02X:%02X:%02X:%02X:%02X ch=%u boot_id=%u fw=%s\n",
               s_rx_id, m[0], m[1], m[2], m[3], m[4], m[5],
               (unsigned)s_ch, (unsigned)s_boot_id, FW_VER);
}

static void reply_status(void)
{
    csi_rx_stats_t st;
    csi_rx_get_stats(&st);
    char macs[3][18];
    for (int i = 0; i < 3; i++) {
        uint8_t m[6];
        if (csi_rx_get_pinned(i, m))
            snprintf(macs[i], sizeof macs[i], "%02X:%02X:%02X:%02X:%02X:%02X",
                     m[0], m[1], m[2], m[3], m[4], m[5]);
        else
            snprintf(macs[i], sizeof macs[i], "-");
    }
    csil_reply("STATUS role=rx idx=%d ch=%u mode=%d framed=%" PRIu32 " cb=%" PRIu32
               " magic_rej=%" PRIu32 " mac_rej=%" PRIu32 " qdrop=%" PRIu32
               " not_ht=%" PRIu32 " short=%" PRIu32 " per_tx=%" PRIu32 "/%" PRIu32 "/%" PRIu32
               " boot_id=%u\n",
               (int)s_rx_id, (unsigned)s_ch, (int)s_mode,
               s_framed, st.cb_total, st.magic_reject, st.mac_reject, st.q_drop,
               s_not_ht, s_short_buf, st.per_tx[0], st.per_tx[1], st.per_tx[2],
               (unsigned)s_boot_id);
}

static void handle(const char *line)
{
    /* 直烧版保留 console —— 但 stream 模式下串口已被设置成二进制, 主机读到的是
     * CSI 帧而非 ASCII. 调试时需要从串口 monitor 切断 main 链路 (例如拔 USB
     * 或在 monitor 用 sendRepl 工具) 才能让 console 回来. */
    if (!strcmp(line, "HELLO")) {
        banner();
        return;
    }
    if (!strcmp(line, "STATUS")) {
        reply_status();
        return;
    }
    if (!strcmp(line, "RAW")) {
        int n = 100;
        if (s_mode == M_STREAM)
            return;          /* 已 binary, 不重复切 */
        csil_reply("OK RAW n=%d (debug only — 直烧版默认 stream)\n", n);
        return;
    }
    /* STOP / START 在直烧版无意义 —— 串口 binary 模式已锁, 切回需要断电重启 */
    csil_reply("(直烧版) commands: HELLO / STATUS\n");
}

void app_main(void)
{
    ESP_ERROR_CHECK(csil_cfg_init());
    s_boot_id = csil_cfg_next_boot_id();

    /* 直烧: 不再从 NVS 读 idx/ch, 用编译时常量. 同时写回 NVS 保持一致性 */
    s_rx_id = BOARD_IDX;
    s_ch = BOARD_CH;
    csil_cfg_set_u8("idx", s_rx_id);
    csil_cfg_set_u8("ch", s_ch);

    csil_serial_init();
    banner();

    ESP_ERROR_CHECK(csil_wifi_start(s_ch));

    s_q = xQueueCreate(QUEUE_DEPTH, sizeof(csi_item_t));
    configASSERT(s_q);
    ESP_ERROR_CHECK(csi_rx_init(s_q));

    BaseType_t ok = xTaskCreate(framer_task, "framer", 4096, NULL, 5, NULL);
    configASSERT(ok == pdPASS);

    /* 【直烧关键】上电立刻进 stream 模式 —— 上电即开始接收 CSI 帧 */
    s_mode = M_STREAM;
    csil_set_binary(true);

    csil_console_run(handle);  /* 不返回 */
}
