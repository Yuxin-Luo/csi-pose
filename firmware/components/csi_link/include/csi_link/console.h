/* Line console — boot = text mode, after START = binary mode (logs OFF).
 * Responses must go through csil_reply/csil_serial_write (no printf — prevents frame corruption).
 */
#pragma once
#include <stdbool.h>

typedef void (*csil_cmd_handler_t)(const char *line);

// Blocking command loop — called from app_main (does not return)
void csil_console_run(csil_cmd_handler_t handler);

/* "KEY=value" integer argument parsing (e.g., csil_cmd_arg_int("START rate=100", "rate", 100)) */
int csil_cmd_arg_int(const char *line, const char *key, int def);

// printf-style text response (single write — atomic). Include \n in fmt
void csil_reply(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

// true: suppress all ESP_LOG (binary mode), false: restore default level
void csil_set_binary(bool on);
