/* Serial I/O abstraction — UART0 / USB-Serial-JTAG backend (selected via Kconfig).
 * Binary frames and text responses share the same port, so writes are call-atomic.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

void csil_serial_init(void);
/* Fully blocking send (via driver TX ring buffer, call-atomic) */
void csil_serial_write(const void *buf, size_t len);
/* Receive up to max bytes within timeout_ms; returns byte count read (0 = timeout) */
int csil_serial_read(uint8_t *buf, size_t max, uint32_t timeout_ms);
