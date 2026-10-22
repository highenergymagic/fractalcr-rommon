/* SPDX-License-Identifier: MIT */
#ifndef FRACTALCR_MONITOR_H
#define FRACTALCR_MONITOR_H
#include <stddef.h>
int secure_boot(void);
int secure_boot_required(void);
int verified_image(const char *path);
void boot_image(int fd, int ignore_startup);
int write_all(int fd, const void *data, size_t n);
int monitor_main(void);
int recovery_worker(const char *name);
int monitor_cancelled(void);
#endif
