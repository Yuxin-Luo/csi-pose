/* TX 板直烧版——上电即开始广播, 不再需要串口命令. 模板代码, 烧不同编号的板时:
 *
 *   (1) 修改下面的 BOARD_IDX  (tx0=0, tx1=1, tx2=2)
 *   (2) 修改 项目级 CMakeLists.txt 里的 project() 名字 (csi_tx0 / csi_tx1 / csi_tx2)
 *   (3) idf.py set-target esp32s3 && idf.py build && idf.py -p COMx flash
 *
 * 核心代码取自 firmware/tx/main/main.c (csi-pose 项目).
 */
#include <inttypes.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "esp_mac.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "sdkconfig.h"

#include "csi_link/cfg.h"
#include "csi_link/console.h"
#include "csi_link/serial_io.h"
#include "csi_link/wifi.h"
#include "csi_link/wire.h"

#define FW_VER "m0.1-d"           /* d = direct_download 变体 */
#define BOARD_IDX 2               /* 【改这里】tx0=0, tx1=1, tx2=2  */
#define BOARD_CH  6               /* 【改这里】6/1/11 都行, 全部 TX 板必须一致 */

static esp_timer_handle_t s_tick;
static volatile bool s_running;
static uint32_t s_seq, s_sent_ok, s_sent_fail;
static uint32_t s_rate = 100;
static uint8_t s_idx, s_ch, s_boot_id;

/* 平均周期 ±15% 均匀抖动再预订 —— 阻断 3TX 等周期相位对齐造成的周期性碰撞风暴
 * (M0 实测: 相位对齐段链路损失 5~8%). 宿主以 100Hz 网格重采样, 抖动无害. */
static void schedule_next(void)
{
    uint32_t period = 1000000UL / s_rate;
    uint32_t span = period * 30 / 100;
    int32_t jit = (int32_t)(esp_random() % (span + 1)) - (int32_t)(span / 2);
    esp_timer_start_once(s_tick, (uint64_t)((int64_t)period + jit));
}

/* 广播无 ACK, send 回调状态无意义 —— 仅按 esp_now_send 返回值计数.
 * 损失计量的真相以 RX 侧 seq 缺口为准. */
static void tick_cb(void *arg)
{
    if (!s_running)
        return;
    csil_payload_t p = {
        .magic = CSIL_PAYLOAD_MAGIC,
        .tx_idx = s_idx,
        .seq = s_seq++,
    };
    if (esp_now_send(CSIL_BCAST, (const uint8_t *)&p, sizeof p) == ESP_OK)
        s_sent_ok++;
    else
        s_sent_fail++;
    schedule_next();
}

static void banner(void)
{
    uint8_t m[6];
    esp_read_mac(m, ESP_MAC_WIFI_STA);
    csil_reply("BOOT role=tx idx=%d mac=%02X:%02X:%02X:%02X:%02X:%02X ch=%u boot_id=%u fw=%s\n",
               s_idx, m[0], m[1], m[2], m[3], m[4], m[5],
               (unsigned)s_ch, (unsigned)s_boot_id, FW_VER);
}

static void handle(const char *line)
{
    /* 直烧版仍保留 console —— 让用户能敲 STATUS / STOP 临时调试.
     * 注: 上电后立刻 s_running=true, 不能用 START 重新初始化 (会重置 seq). */
    if (!strcmp(line, "HELLO")) {
        banner();
        return;
    }
    if (!strcmp(line, "MAC")) {
        uint8_t m[6];
        esp_read_mac(m, ESP_MAC_WIFI_STA);
        csil_reply("MAC %02X:%02X:%02X:%02X:%02X:%02X\n", m[0], m[1], m[2], m[3], m[4], m[5]);
        return;
    }
    if (!strcmp(line, "STATUS")) {
        csil_reply("STATUS role=tx idx=%d ch=%u running=%d rate=%" PRIu32
                   " seq=%" PRIu32 " ok=%" PRIu32 " fail=%" PRIu32 " boot_id=%u\n",
                   (int)s_idx, (unsigned)s_ch, (int)s_running,
                   s_rate, s_seq, s_sent_ok, s_sent_fail, (unsigned)s_boot_id);
        return;
    }
    if (!strcmp(line, "STOP")) {
        if (s_running) {
            s_running = false;
            esp_timer_stop(s_tick);
            csil_reply("STOPPED (重启或 START 才能恢复)\n");
        }
        return;
    }
    if (!strcmp(line, "START")) {
        /* 直烧版默认已启动, START 命令仅用于 STOP 后恢复 */
        if (s_running) {
            csil_reply("already running\n");
            return;
        }
        s_seq = s_sent_ok = s_sent_fail = 0;
        s_rate = 100;
        s_running = true;
        schedule_next();
        csil_reply("STARTED rate=100\n");
        return;
    }
    csil_reply("unknown cmd (HELLO/MAC/STATUS/STOP/START)\n");
}

void app_main(void)
{
    ESP_ERROR_CHECK(csil_cfg_init());
    s_boot_id = csil_cfg_next_boot_id();

    /* 直烧: 不再从 NVS 读 idx/ch, 直接用编译时常量. 但仍写入 NVS, 便于一致性 */
    s_idx = BOARD_IDX;
    s_ch = BOARD_CH;
    csil_cfg_set_u8("idx", s_idx);
    csil_cfg_set_u8("ch", s_ch);

    csil_serial_init();
    banner();

    ESP_ERROR_CHECK(csil_wifi_start(s_ch));
    ESP_ERROR_CHECK(csil_espnow_tx_init());

    const esp_timer_create_args_t targs = {.callback = tick_cb, .name = "txbeacon"};
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_tick));

    /* 【直烧关键】上电自动进入 running, 不需要外部敲 START */
    s_rate = 100;
    s_seq = 0;
    s_sent_ok = s_sent_fail = 0;
    s_running = true;
    schedule_next();

    csil_console_run(handle);  /* 不返回 */
}
