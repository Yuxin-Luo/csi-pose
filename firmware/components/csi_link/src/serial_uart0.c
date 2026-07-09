#include "sdkconfig.h"
#if CONFIG_CSI_LINK_SERIAL_UART0

#include "csi_link/serial_io.h"
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "freertos/FreeRTOS.h"

#define PORT UART_NUM_0

void csil_serial_init(void)
{
    const uart_config_t cfg = {
        .baud_rate = CONFIG_CSI_LINK_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    /* TX ring 131072: same rationale as usb_jtag backend (host congestion tolerance ~3.3s) */
    ESP_ERROR_CHECK(uart_driver_install(PORT, 4096, 131072, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(PORT, &cfg));
    /* Also route logs(stdout) through driver — prevents byte-level interleaving with frame writes */
    uart_vfs_dev_use_driver(PORT);
}

void csil_serial_write(const void *buf, size_t len)
{
    uart_write_bytes(PORT, buf, len);
}

int csil_serial_read(uint8_t *buf, size_t max, uint32_t timeout_ms)
{
    int n = uart_read_bytes(PORT, buf, max, pdMS_TO_TICKS(timeout_ms));
    return n < 0 ? 0 : n;
}

#endif /* CONFIG_CSI_LINK_SERIAL_UART0 */
