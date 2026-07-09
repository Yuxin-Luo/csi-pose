#include "sdkconfig.h"
#if CONFIG_CSI_LINK_SERIAL_USB_JTAG

#include "csi_link/serial_io.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"

void csil_serial_init(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        /* 131072: ~3.3s tolerance at 39KB/s — prevents board-side byte loss even when host
         * periodic congestion (>0.4s, measured on 2026-06-11 with 3 boards simultaneous CRC in Sok) */
        .tx_buffer_size = 131072,
        .rx_buffer_size = 4096,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    usb_serial_jtag_vfs_use_driver();
}

void csil_serial_write(const void *buf, size_t len)
{
    const uint8_t *p = (const uint8_t *)buf;
    size_t off = 0;
    while (off < len) {
        int n = usb_serial_jtag_write_bytes(p + off, len - off, portMAX_DELAY);
        if (n <= 0) break;
        off += (size_t)n;
    }
}

int csil_serial_read(uint8_t *buf, size_t max, uint32_t timeout_ms)
{
    int n = usb_serial_jtag_read_bytes(buf, max, pdMS_TO_TICKS(timeout_ms));
    return n < 0 ? 0 : n;
}

#endif /* CONFIG_CSI_LINK_SERIAL_USB_JTAG */
