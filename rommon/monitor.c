#include "release.h"
/*
 * Serial recovery monitor. All external input is data, never shell syntax.
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include "monitor.h"
#include "control.h"
#include "platform-info.h"
#include <mntent.h>
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <net/if.h>
#include <net/route.h>
#include <sys/ioctl.h>
#include <openssl/sha.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/sysinfo.h>
#include <sys/syscall.h>
#include <sys/statvfs.h>
#include <strings.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define VALUE 512
#define MAX_TRANSFER (512UL * 1024 * 1024)
#define MFLAGS (MS_NOSUID | MS_NODEV | MS_NOEXEC)
#define ENV_VERSION "FCR-ENV-1"
struct setting { const char *name; char value[VALUE]; };
static struct setting settings[] = {
    {"BOOT", "bootflash:system.bin"}, {"CONFREG", "0x2102"},
    {"IP_ADDRESS", ""}, {"IP_SUBNET_MASK", ""}, {"DEFAULT_GATEWAY", ""},
    {"TFTP_SERVER", ""}, {"TFTP_FILE", ""}, {"ETHER_PORT", "0"},
    {"SECURE_BOOT", "required"},
};
#define NSET (sizeof(settings) / sizeof(settings[0]))
struct volume { char name[16], device[128], root[64]; };
static struct volume volumes[5];
static unsigned nvol;
static uint64_t env_generation;
static int console_ready, dirty, operation_aborted;
static unsigned command_number = 1;
static char history[16][VALUE];
static unsigned history_count;

static long long milliseconds(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int character(int timeout)
{
    struct pollfd p = { .fd = STDIN_FILENO, .events = POLLIN };
    unsigned char c;
    if (poll(&p, 1, timeout) > 0 && read(0, &c, 1) == 1) return c;
    return -1;
}

int monitor_cancelled(void)
{
    if (!console_ready) return 0;
    if (operation_aborted) return 1;
    int c = character(0);
    if (c == 3 || c == 0) operation_aborted = 1; /* serial BREAK maps to NUL */
    return operation_aborted;
}

static void console_init(void)
{
    struct termios t;
    if (!tcgetattr(0, &t)) {
        cfmakeraw(&t);
        t.c_oflag = OPOST | ONLCR;
        t.c_cflag |= CLOCAL | CREAD;
        cfsetispeed(&t, B115200); cfsetospeed(&t, B115200);
        tcsetattr(0, TCSANOW, &t);
    }
    console_ready = 1;
}

static int line_read(char *line, size_t size)
{
    size_t used = 0;
    int overflow = 0;
    for (;;) {
        int c = character(-1);
        if (c < 0) { usleep(100000); continue; }
        if (c == 3 || c == 0) { puts("^C"); line[0] = 0; return -1; }
        if (c == '\r' || c == '\n') {
            putchar('\n'); line[used] = 0;
            if (overflow) { puts("% Command too long; discarded."); return -1; }
            return 0;
        }
        if (c == 8 || c == 127) {
            if (used) { --used; fputs("\b \b", stdout); }
        } else if (c == 21) {
            while (used) { --used; fputs("\b \b", stdout); }
            overflow = 0;
        } else if (c >= 32 && c <= 126) {
            if (used + 1 < size) { line[used++] = (char)c; putchar(c); }
            else overflow = 1;
        }
    }
}

static int confirm(const char *message)
{
    char answer[16];
    printf("%s [y/N]: ", message);
    return !line_read(answer, sizeof(answer)) && !strcmp(answer, "y");
}

static int setting_index(const char *name)
{
    for (unsigned i = 0; i < NSET; i++) if (!strcmp(settings[i].name, name)) return (int)i;
    return -1;
}

static const char *value(const char *name)
{
    int i = setting_index(name);
    return i < 0 ? "" : settings[i].value;
}

int secure_boot_required(void) { return strcmp(value("SECURE_BOOT"), "optional") != 0; }

static int filename_ok(const char *name)
{
    size_t n = strlen(name);
    if (!n || n > 100 || !strcmp(name, ".") || !strcmp(name, "..") || name[0] == '.') return 0;
    for (; *name; name++)
        if (!isalnum((unsigned char)*name) && !strchr("._-", *name)) return 0;
    return 1;
}

static int ipv4(const char *text)
{
    struct in_addr address;
    return inet_pton(AF_INET, text, &address) == 1;
}

static int remote_file_ok(const char *text)
{
    if (!*text || strlen(text) > 200 || strstr(text, "..")) return 0;
    for (; *text; text++)
        if (!isalnum((unsigned char)*text) && !strchr("/._-", *text)) return 0;
    return 1;
}

static int reference_ok(const char *text)
{
    if (!strcmp(text, "tftp:")) return 1;
    if (!strncmp(text, "tftp://", 7)) {
        char ip[32];
        const char *slash = strchr(text + 7, '/');
        if (!slash || slash - (text + 7) >= (int)sizeof(ip)) return 0;
        snprintf(ip, sizeof(ip), "%.*s", (int)(slash - (text + 7)), text + 7);
        return ipv4(ip) && remote_file_ok(slash + 1);
    }
    const char *colon = strchr(text, ':');
    if (!colon) return 0;
    size_t prefix = (size_t)(colon - text);
    if (!((prefix == 9 && !strncmp(text, "bootflash", 9)) ||
          (prefix == 5 && !strncmp(text, "flash", 5)) ||
          (prefix == 4 && !strncmp(text, "usb", 3) && text[3] >= '0' && text[3] <= '3'))) return 0;
    return filename_ok(colon + 1);
}

static int valid_setting(unsigned i, const char *text)
{
    if (strlen(text) >= VALUE) return 0;
    const char *name = settings[i].name;
    if (!strcmp(name, "SECURE_BOOT")) return !strcmp(text, "required") || !strcmp(text, "optional");
    if (!strcmp(name, "CONFREG")) return !strcmp(text, "0x0") || !strcmp(text, "0x2102") || !strcmp(text, "0x2142");
    if (!strcmp(name, "ETHER_PORT")) return strlen(text) == 1 && *text >= '0' && *text <= '7';
    if (!*text) return 1;
    if (!strcmp(name, "BOOT")) {
        char copy[VALUE], *save;
        strcpy(copy, text);
        if (*text == ';' || text[strlen(text)-1] == ';' || strstr(text, ";;")) return 0;
        for (char *p = strtok_r(copy, ";", &save); p; p = strtok_r(NULL, ";", &save))
            if (!reference_ok(p)) return 0;
        return 1;
    }
    if (!strcmp(name, "TFTP_FILE")) return remote_file_ok(text);
    if (!ipv4(text)) return 0;
    if (!strcmp(name, "IP_SUBNET_MASK")) {
        struct in_addr mask;
        inet_pton(AF_INET, text, &mask);
        uint32_t inverse = ~ntohl(mask.s_addr);
        return inverse != UINT32_MAX && !(inverse & (inverse + 1));
    }
    return 1;
}

static int assignment(char *text, int announce)
{
    char *equals = strchr(text, '=');
    if (!equals) return -1;
    *equals++ = 0;
    int i = setting_index(text);
    if (i < 0 || !valid_setting((unsigned)i, equals)) {
        if (announce) puts("% Unknown variable or invalid value. Use help.");
        return -1;
    }
    strcpy(settings[i].value, equals);
    if (announce) { dirty = 1; puts("Environment changed; use sync to save."); }
    return 0;
}

static struct volume *volume_named(const char *name)
{
    if (!strcmp(name, "flash")) name = "bootflash";
    for (unsigned i = 0; i < nvol; i++) if (!strcmp(volumes[i].name, name)) return &volumes[i];
    return NULL;
}

static int local_path(const char *ref, char *path, size_t size, struct volume **result)
{
    char device[16];
    const char *colon = strchr(ref, ':');
    if (!colon || colon - ref >= (int)sizeof(device) || !filename_ok(colon + 1)) return -1;
    snprintf(device, sizeof(device), "%.*s", (int)(colon - ref), ref);
    struct volume *v = volume_named(device);
    if (!v) return -1;
    if (result) *result = v;
    return snprintf(path, size, "%s/%s", v->root, colon + 1) < (int)size ? 0 : -1;
}

static int remount(struct volume *v, int writable)
{
    if (mount(NULL, v->root, NULL, MS_REMOUNT | MFLAGS | (writable ? 0 : MS_RDONLY), NULL)) {
        printf("%% Cannot remount %s: %s\n", v->name, strerror(errno)); return -1;
    }
    return 0;
}

/* Enumerate sorted block names, identify internal flash by a FAT volume label,
 * USB media by its sysfs ancestry. Never guess that an arbitrary data disk is
 * bootflash, and never format anything. Each mount has its own private directory. */
static void scan_devices(void)
{
    for (unsigned i = 0; i < nvol; ) {
        if (access(volumes[i].device, F_OK)) {
            umount2(volumes[i].root, MNT_DETACH);
            volumes[i] = volumes[--nvol];
        } else i++;
    }
    struct dirent **entries;
    int count = scandir("/sys/class/block", &entries, NULL, alphasort);
    if (count < 0) return;
    /* Prefer internal FRFLASH when present, then allow a bootable USB image
     * to supply bootflash on machines with blank internal storage. */
    for (int pass = 0; pass < 2; pass++) for (int i = 0; i < count; i++) {
        char sys[256], real[PATH_MAX], device[128];
        const char *name = entries[i]->d_name;
        if (name[0] == '.' || strlen(name) > 90 || nvol == 5) goto next;
        snprintf(sys, sizeof(sys), "/sys/class/block/%.90s", name);
        if (!realpath(sys, real)) goto next;
        int usb = strstr(real, "/usb") != NULL;
        if (usb != pass) goto next;
        snprintf(device, sizeof(device), "/dev/%.90s", name);
        int known = 0;
        for (unsigned j = 0; j < nvol; j++) if (!strcmp(volumes[j].device, device)) known = 1;
        if (known) goto next;
        unsigned char sector[512];
        int fd = open(device, O_RDONLY | O_CLOEXEC);
        ssize_t bytes = fd < 0 ? -1 : read(fd, sector, sizeof(sector));
        if (fd >= 0) close(fd);
        if (bytes != 512 || sector[510] != 0x55 || sector[511] != 0xaa) goto next;
        int fat32 = !memcmp(sector + 82, "FAT32   ", 8);
        int fat16 = !memcmp(sector + 54, "FAT16   ", 8);
        if (!fat32 && !fat16) goto next;
        struct volume v = {0};
        int flash_label = !memcmp(sector + (fat32 ? 71 : 43), "FRFLASH    ", 11);
        if (usb && (!flash_label || volume_named("bootflash"))) {
            unsigned index;
            for (index = 0; index < 4; index++) {
                snprintf(v.name, sizeof(v.name), "usb%u", index);
                if (!volume_named(v.name)) break;
            }
            if (index == 4) goto next;
        } else {
            if (volume_named("bootflash") ||
                memcmp(sector + (fat32 ? 71 : 43), "FRFLASH    ", 11)) goto next;
            strcpy(v.name, "bootflash");
        }
        snprintf(v.root, sizeof(v.root), "/media/%s", v.name);
        strcpy(v.device, device);
        mkdir(v.root, 0700);
        FILE *mounts = setmntent("/proc/mounts", "r");
        struct mntent *entry; int existing = 0;
        if (mounts) {
            while ((entry = getmntent(mounts)))
                if (!strcmp(entry->mnt_dir, v.root) && !strcmp(entry->mnt_fsname, device)) existing = 1;
            endmntent(mounts);
        }
        if (existing || !mount(device, v.root, "vfat", MS_RDONLY | MFLAGS, "utf8=0"))
            volumes[nvol++] = v;
next:
        if (pass == 1) free(entries[i]);
    }
    free(entries);
}

/* Only the storage service mounts devices. Console consumes its atomic inventory. */
static void scan_storage(void)
{
    int fd = open("/run/recovery-volumes", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    nvol = 0;
    if (fd < 0) return;
    ssize_t n = read(fd, volumes, sizeof(volumes)); close(fd);
    if (n >= 0 && n % sizeof(volumes[0]) == 0) nvol = (unsigned)n / sizeof(volumes[0]);
}

static int directory_sync(const char *path)
{
    int fd = open(path, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    int result = fsync(fd); close(fd); return result;
}

/* Environment integrity is for torn writes, not authorization. Both generations
 * are treated as untrusted input. They cannot change keys, command lines or
 * image verification policy. */
static int parse_environment(char *data, size_t size, uint64_t *generation)
{
    char *body = strchr(data, '\n'), expected[65], actual[65];
    unsigned long long gen;
    int consumed = 0;
    if (!body || sscanf(data, ENV_VERSION " %llu %64[0123456789abcdef]%n",
                        &gen, expected, &consumed) != 2 ||
        data + consumed != body || strlen(expected) != 64) return -1;
    body++;
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256((unsigned char *)body, size - (size_t)(body - data), digest);
    for (unsigned i = 0; i < sizeof(digest); i++) sprintf(actual + i*2, "%02x", digest[i]);
    if (strcmp(expected, actual)) return -1;
    struct setting saved[NSET];
    memcpy(saved, settings, sizeof(saved));
    unsigned seen = 0;
    char *save;
    for (char *line = strtok_r(body, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *eq = strchr(line, '=');
        if (!eq) goto invalid;
        *eq = 0; int i = setting_index(line); *eq = '=';
        if (i < 0 || (seen & (1U << i)) || assignment(line, 0)) goto invalid;
        seen |= 1U << i;
    }
    /* Older environments predate SECURE_BOOT. Missing policy always defaults
     * to required, never inherits a previous unsaved optional selection. */
    if (seen == (1U << (NSET-1)) - 1) strcpy(settings[NSET-1].value, "required");
    else if (seen != (1U << NSET) - 1) goto invalid;
    *generation = gen;
    return 0;
invalid:
    memcpy(settings, saved, sizeof(saved));
    return -1;
}

static void load_environment(void)
{
    struct volume *v = volume_named("bootflash");
    if (!v) return;
    struct setting best[NSET];
    memcpy(best, settings, sizeof(best));
    int found = 0;
    for (int slot = 0; slot < 2; slot++) {
        char path[128], data[8192];
        snprintf(path, sizeof(path), "%s/rommon.env%d", v->root, slot);
        int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        struct stat st;
        ssize_t n = -1;
        if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size < (off_t)sizeof(data))
            n = read(fd, data, (size_t)st.st_size);
        close(fd);
        if (n <= 0 || n != st.st_size) { puts("% Invalid saved environment slot; ignored."); continue; }
        data[n] = 0;
        uint64_t generation;
        if (memchr(data, 0, (size_t)n) || parse_environment(data, (size_t)n, &generation)) {
            puts("% Invalid saved environment slot; ignored."); continue;
        }
        if (!found || generation > env_generation) {
            env_generation = generation;
            memcpy(best, settings, sizeof(best)); found = 1;
        }
    }
    memcpy(settings, best, sizeof(best));
    if (found) printf("Environment generation %llu loaded.\n", (unsigned long long)env_generation);
}

static int save_environment(void)
{
    struct volume *v = volume_named("bootflash");
    if (!v) { puts("% bootflash: unavailable; environment not saved."); return -1; }
    if (env_generation == UINT64_MAX) { puts("% Environment generation exhausted."); return -1; }
    char body[8192], header[128], digest_text[65], target[128], stage[128];
    size_t used = 0;
    for (unsigned i = 0; i < NSET; i++)
        used += (size_t)snprintf(body + used, sizeof(body) - used, "%s=%s\n", settings[i].name, settings[i].value);
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256((unsigned char *)body, used, digest);
    for (unsigned i = 0; i < sizeof(digest); i++) sprintf(digest_text + i*2, "%02x", digest[i]);
    uint64_t next = env_generation + 1;
    snprintf(header, sizeof(header), ENV_VERSION " %llu %s\n", (unsigned long long)next, digest_text);
    snprintf(target, sizeof(target), "%s/rommon.env%u", v->root, (unsigned)(next % 2));
    snprintf(stage, sizeof(stage), "%s/rommon.env.tmp", v->root);
    if (remount(v, 1)) return -1;
    unlink(stage);
    int fd = open(stage, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    int ok = fd >= 0 && !write_all(fd, header, strlen(header)) && !write_all(fd, body, used) && !fsync(fd);
    if (fd >= 0 && close(fd)) ok = 0;
    if (ok) ok = !rename(stage, target) && !directory_sync(v->root);
    if (!ok) unlink(stage);
    if (remount(v, 0)) ok = 0;
    if (!ok) { puts("% Environment save failed; inspect media."); return -1; }
    env_generation = next; dirty = 0;
    puts("Monitor environment saved.");
    return 0;
}

static unsigned nic_names(char names[8][IF_NAMESIZE])
{
    struct dirent **entries;
    int n = scandir("/sys/class/net", &entries, NULL, alphasort);
    unsigned found = 0;
    for (int i = 0; i < n; i++) {
        const char *name = entries[i]->d_name;
        if (name[0] != '.' && strcmp(name, "lo") && strlen(name) < IF_NAMESIZE && found < 8)
            strcpy(names[found++], name);
        free(entries[i]);
    }
    if (n >= 0) free(entries);
    return found;
}

/* Native recovery protocols: no executable helpers or parsed command output. */
static int recovery_tftp(const char *server, const char *file, const char *path)
{
    int sock = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (sock < 0) return -1;
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) { close(sock); return -1; }
    struct sockaddr_in peer = {.sin_family = AF_INET, .sin_port = htons(69)};
    inet_pton(AF_INET, server, &peer.sin_addr);
    unsigned char tx[520] = {0, 1}, rx[517];
    size_t name = strlen(file), txlen = name + 9;
    memcpy(tx + 2, file, name + 1);
    memcpy(tx + 3 + name, "octet", 6);
    uint16_t expected = 1;
    size_t total = 0;
    int pinned = 0, result = -1;
    long long deadline = milliseconds() + 120000, retry = 0;
    while (!monitor_cancelled() && milliseconds() < deadline) {
        long long now = milliseconds();
        if (now >= retry) {
            if (sendto(sock, tx, txlen, 0, (struct sockaddr *)&peer, sizeof(peer)) != (ssize_t)txlen) break;
            retry = now + 1000;
        }
        struct pollfd wait = {.fd = sock, .events = POLLIN};
        int available = poll(&wait, 1, 100);
        if (available < 0 && errno != EINTR) break;
        if (available <= 0 || !(wait.revents & POLLIN)) continue;
        struct sockaddr_in from;
        socklen_t size = sizeof(from);
        ssize_t n = recvfrom(sock, rx, sizeof(rx), 0, (struct sockaddr *)&from, &size);
        if (n < 4 || n > 516 || size != sizeof(from) || from.sin_family != AF_INET ||
            from.sin_addr.s_addr != peer.sin_addr.s_addr ||
            (pinned && from.sin_port != peer.sin_port)) continue;
        unsigned op = ((unsigned)rx[0] << 8) | rx[1];
        uint16_t block = ((unsigned)rx[2] << 8) | rx[3];
        if (op == 5) break; /* Never print untrusted server error strings. */
        if (op != 3) continue;
        if (block != expected) {
            if (pinned && block == (uint16_t)(expected - 1))
                sendto(sock, tx, txlen, 0, (struct sockaddr *)&peer, sizeof(peer));
            continue;
        }
        if (!pinned) { peer.sin_port = from.sin_port; pinned = 1; }
        size_t bytes = (size_t)n - 4;
        if (total > MAX_TRANSFER - bytes || write_all(fd, rx + 4, bytes)) break;
        total += bytes;
        tx[0] = 0; tx[1] = 4; tx[2] = rx[2]; tx[3] = rx[3]; txlen = 4;
        if (sendto(sock, tx, txlen, 0, (struct sockaddr *)&peer, sizeof(peer)) != (ssize_t)txlen) break;
        expected++; retry = milliseconds() + 1000;
        if (bytes < 512) {
            result = fsync(fd);
            /* Dally for retransmission if the final ACK was lost. */
            long long dally = milliseconds() + 2000;
            while (milliseconds() < dally && !monitor_cancelled()) {
                if (poll(&wait, 1, 100) <= 0) continue;
                size = sizeof(from);
                n = recvfrom(sock, rx, sizeof(rx), 0, (struct sockaddr *)&from, &size);
                if (n >= 4 && n <= 516 && from.sin_addr.s_addr == peer.sin_addr.s_addr &&
                    from.sin_port == peer.sin_port && rx[0] == 0 && rx[1] == 3 &&
                    rx[2] == tx[2] && rx[3] == tx[3])
                    sendto(sock, tx, txlen, 0, (struct sockaddr *)&peer, sizeof(peer));
            }
            break;
        }
    }
    if (close(fd)) result = -1;
    close(sock);
    if (result) unlink(path);
    return result;
}

static uint16_t internet_checksum(const unsigned char *bytes, size_t size)
{
    uint32_t sum = 0;
    while (size > 1) { sum += ((unsigned)bytes[0] << 8) | bytes[1]; bytes += 2; size -= 2; }
    if (size) sum += (unsigned)bytes[0] << 8;
    while (sum >> 16) sum = (sum & 65535) + (sum >> 16);
    return (uint16_t)~sum;
}

static int recovery_ping(const char *host)
{
    int sock = socket(AF_INET, SOCK_RAW | SOCK_CLOEXEC, IPPROTO_ICMP);
    if (sock < 0) return -1;
    struct sockaddr_in peer = {.sin_family = AF_INET};
    inet_pton(AF_INET, host, &peer.sin_addr);
    unsigned char packet[80] = {8}, received[2048];
    if (getrandom(packet + 4, sizeof(packet) - 4, 0) != sizeof(packet) - 4) { close(sock); return -1; }
    unsigned success = 0;
    printf("Sending 3, 100-byte ICMP Echos to %s, timeout is 2 seconds:\n", host);
    for (unsigned sequence = 0; sequence < 3 && !monitor_cancelled(); sequence++) {
        packet[2] = packet[3] = packet[6] = 0; packet[7] = sequence;
        uint16_t sum = internet_checksum(packet, sizeof(packet));
        packet[2] = sum >> 8; packet[3] = sum;
        if (sendto(sock, packet, sizeof(packet), 0, (struct sockaddr *)&peer, sizeof(peer)) != sizeof(packet)) break;
        long long deadline = milliseconds() + 2000;
        int matched = 0;
        while (!monitor_cancelled() && milliseconds() < deadline) {
            struct pollfd wait = {.fd = sock, .events = POLLIN};
            if (poll(&wait, 1, 100) <= 0) continue;
            struct sockaddr_in from; socklen_t size = sizeof(from);
            ssize_t n = recvfrom(sock, received, sizeof(received), 0, (struct sockaddr *)&from, &size);
            if (n < 20 || from.sin_addr.s_addr != peer.sin_addr.s_addr) continue;
            size_t offset = (received[0] & 15) * 4;
            if (offset < 20 || (size_t)n != offset + sizeof(packet)) continue;
            const unsigned char *icmp = received + offset;
            if (icmp[0] || icmp[1] || internet_checksum(icmp, sizeof(packet)) ||
                memcmp(icmp + 4, packet + 4, sizeof(packet) - 4)) continue;
            matched = 1; break;
        }
        putchar(matched ? '!' : '.'); fflush(stdout); success += matched;
    }
    printf("\nSuccess rate is %u percent (%u/3)\n", success * 100 / 3, success);
    close(sock);
    return success ? 0 : -1;
}

static int network_setup(void)
{
    FILE *f = fopen("/run/recovery-network.new", "w");
    if (!f) return -1;
    for (unsigned i = 0; i < NSET; i++) fprintf(f, "%s=%s\n", settings[i].name, settings[i].value);
    if (fclose(f) || rename("/run/recovery-network.new", "/run/recovery-network.conf")) return -1;
    struct control_reply reply;
    if (control_call(CT_LIST, NULL, &reply)) return -1;
    const struct service_status *service = control_find(&reply, "recovery-network");
    if (!service) return -1;
    unsigned starts = service->starts;
    if (control_call(CT_RESTART, "recovery-network", &reply)) return -1;
    long long end = milliseconds() + 25000;
    while (milliseconds() < end && !monitor_cancelled()) {
        usleep(100000);
        if (control_call(CT_LIST, NULL, &reply)) return -1;
        service = control_find(&reply, "recovery-network");
        if (!service || service->state == SV_FAILED) break;
        if (service->starts > starts && service->state == SV_RUNNING) return 0;
    }
    puts("% Recovery networking is unavailable. Use show logging recovery-network.");
    return -1;
}

/* The recovery service owns one IPv4 address and its default route. */
static int network_address(const char *name, const char *address, const char *mask)
{
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct ifreq req = {0};
    snprintf(req.ifr_name, sizeof(req.ifr_name), "%s", name);
    int result = ioctl(fd, SIOCGIFFLAGS, &req);
    if (!result) {
        req.ifr_flags |= IFF_UP;
        result = ioctl(fd, SIOCSIFFLAGS, &req);
    }
    struct sockaddr_in *ip = (struct sockaddr_in *)&req.ifr_addr;
    memset(ip, 0, sizeof(*ip)); ip->sin_family = AF_INET;
    if (!result && inet_pton(AF_INET, address, &ip->sin_addr) != 1) result = -1;
    if (!result) result = ioctl(fd, SIOCSIFADDR, &req);
    if (!result && mask) {
        memset(ip, 0, sizeof(*ip)); ip->sin_family = AF_INET;
        if (inet_pton(AF_INET, mask, &ip->sin_addr) != 1) result = -1;
        else result = ioctl(fd, SIOCSIFNETMASK, &req);
    }
    close(fd);
    return result;
}

static int network_gateway(const char *name, const char *gateway, int add)
{
    if (!*gateway) return 0;
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct rtentry route = {0};
    route.rt_dst.sa_family = AF_INET;
    route.rt_genmask.sa_family = AF_INET;
    struct sockaddr_in *ip = (struct sockaddr_in *)&route.rt_gateway;
    ip->sin_family = AF_INET;
    if (inet_pton(AF_INET, gateway, &ip->sin_addr) != 1) { close(fd); return -1; }
    route.rt_flags = RTF_UP | RTF_GATEWAY;
    route.rt_dev = (char *)name;
    int result = ioctl(fd, add ? SIOCADDRT : SIOCDELRT, &route);
    if (!add && errno == ESRCH) result = 0;
    close(fd);
    return result;
}

static int network_apply(void)
{
    char names[8][IF_NAMESIZE];
    unsigned count = nic_names(names), index = (unsigned)atoi(value("ETHER_PORT"));
    if (index >= count) { puts("% ETHER_PORT does not name an available adapter; use dev."); return -1; }
    if (!*value("IP_ADDRESS") || !*value("IP_SUBNET_MASK")) {
        puts("% Set IP_ADDRESS and IP_SUBNET_MASK first."); return -1;
    }
    if (network_address(names[index], value("IP_ADDRESS"), value("IP_SUBNET_MASK")) ||
        network_gateway(names[index], value("DEFAULT_GATEWAY"), 1)) {
        network_address(names[index], "0.0.0.0", NULL);
        puts("% Cannot configure recovery interface."); return -1;
    }
    printf("Using ETHER_PORT=%u, address %s\n", index, value("IP_ADDRESS"));
    return 0;
}

static int obtain(const char *ref, char *path, size_t size)
{
    if (strncmp(ref, "tftp:", 5)) return local_path(ref, path, size, NULL);
    char server[32], file[256];
    if (!reference_ok(ref)) { puts("% Invalid TFTP reference."); return -1; }
    if (!strcmp(ref, "tftp:")) {
        snprintf(server, sizeof(server), "%s", value("TFTP_SERVER"));
        snprintf(file, sizeof(file), "%s", value("TFTP_FILE"));
    } else {
        const char *slash = strchr(ref + 7, '/');
        snprintf(server, sizeof(server), "%.*s", (int)(slash - (ref + 7)), ref + 7);
        snprintf(file, sizeof(file), "%s", slash + 1);
    }
    if (!ipv4(server) || !remote_file_ok(file)) {
        puts("% Set TFTP_SERVER and TFTP_FILE first."); return -1;
    }
    if (network_setup()) return -1;
    snprintf(path, size, "/run/download.bin");
    unlink(path);
    printf("Receiving %s from %s (512 MiB limit, 120 second deadline)...\n", file, server);
    if (recovery_tftp(server, file, path)) { unlink(path); puts("% TFTP reception failed."); return -1; }
    puts("Reception complete; authenticating image.");
    return 0;
}

static void image_command(const char *ref, int boot)
{
    char path[256];
    if (!reference_ok(ref) || obtain(ref, path, sizeof(path))) {
        puts("% Image source unavailable or invalid."); return;
    }
    printf("Loading %s\n", ref);
    int fd = verified_image(path);
    if (!strncmp(ref, "tftp:", 5)) unlink(path);
    if (fd >= 0) {
        if (boot) boot_image(fd, !strcmp(value("CONFREG"), "0x2142"), ref);
        close(fd);
    }
}

static void copy_image(const char *source, const char *destination)
{
    char input[256], output[256], stage[128];
    struct volume *v = NULL;
    size_t n = strlen(destination);
    if (!reference_ok(source) || n < 4 || strcmp(destination + n - 4, ".bin") ||
        local_path(destination, output, sizeof(output), &v) || strcmp(v->name, "bootflash")) {
        puts("% Usage: copy SOURCE bootflash:NAME.bin"); return;
    }
    if (!access(output, F_OK) && !confirm("Destination exists. Replace it?")) {
        puts("Copy cancelled."); return;
    }
    if (obtain(source, input, sizeof(input))) { puts("% Source unavailable."); return; }
    int in = open(input, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    struct stat st;
    if (in < 0 || fstat(in, &st) || !S_ISREG(st.st_mode) || st.st_size <= 272 ||
        st.st_size > (off_t)MAX_TRANSFER) {
        if (in >= 0) close(in);
        puts("% Invalid source or exceeds 512 MiB recovery-copy limit.");
        if (!strncmp(source, "tftp:", 5)) unlink(input);
        return;
    }
    snprintf(stage, sizeof(stage), "%s/recovery.tmp", v->root);
    if (remount(v, 1)) { close(in); goto clean; }
    unlink(stage);
    int out = open(stage, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    int ok = out >= 0;
    off_t remaining = st.st_size;
    unsigned char buffer[65536];
    while (ok && remaining) {
        if (monitor_cancelled()) { ok = 0; break; }
        size_t chunk = remaining < (off_t)sizeof(buffer) ? (size_t)remaining : sizeof(buffer);
        ssize_t got = read(in, buffer, chunk);
        if (got <= 0 || write_all(out, buffer, (size_t)got)) { ok = 0; break; }
        remaining -= got;
    }
    close(in);
    if (out >= 0) { if (fsync(out)) ok = 0; if (close(out)) ok = 0; }
    if (ok) {
        int verified = verified_image(stage);
        ok = verified >= 0;
        if (verified >= 0) close(verified);
    }
    if (ok) ok = !rename(stage, output) && !directory_sync(v->root);
    if (!ok) unlink(stage);
    if (remount(v, 0)) ok = 0;
    if (ok) printf("Copy complete: %s (%lld bytes).\n", destination, (long long)st.st_size);
    else puts("% Copy failed; no unverified image was installed.");
clean:
    if (!strncmp(source, "tftp:", 5)) unlink(input);
}

static void devices(void)
{
    scan_storage();
    puts("Devices (FAT16/FAT32; USB numbering follows discovery):");
    for (unsigned i = 0; i < nvol; i++) printf("  %-12s %s\n", volumes[i].name, volumes[i].device);
    char names[8][IF_NAMESIZE];
    unsigned count = nic_names(names);
    for (unsigned i = 0; i < count; i++) printf("  ETHER_PORT=%u %s\n", i, names[i]);
}

static void directory(const char *ref)
{
    char name[16];
    size_t n = strlen(ref);
    if (!n || n >= sizeof(name) || ref[n-1] != ':') { puts("% Usage: dir DEVICE:"); return; }
    memcpy(name, ref, n-1); name[n-1] = 0;
    struct volume *v = volume_named(name);
    DIR *d = v ? opendir(v->root) : NULL;
    if (!d) { puts("% Device unavailable; use dev to rescan."); return; }
    struct dirent *entry;
    printf("Directory of %s:\n", name);
    while ((entry = readdir(d))) {
        if (entry->d_name[0] == '.') continue;
        struct stat st;
        if (!fstatat(dirfd(d), entry->d_name, &st, AT_SYMLINK_NOFOLLOW))
            printf("%10lld  %s%s\n", (long long)st.st_size, entry->d_name, S_ISDIR(st.st_mode) ? "/" : "");
    }
    closedir(d);
    struct statvfs space;
    if (!statvfs(v->root, &space))
        printf("\n%llu bytes total (%llu bytes free)\n",
            (unsigned long long)space.f_blocks * space.f_frsize,
            (unsigned long long)space.f_bavail * space.f_frsize);
}

static void file_change(const char *source, const char *destination)
{
    char input[256], output[256]; struct volume *v=NULL, *target=NULL;
    if (local_path(source,input,sizeof(input),&v)) { puts("% Source unavailable."); return; }
    const char *name=strchr(source,':')+1;
    if (!strncasecmp(name,"rommon",6) || !strcasecmp(name,"EFI")) { puts("% Protected system object."); return; }
    struct stat st;
    if (lstat(input,&st) || !S_ISREG(st.st_mode)) { puts("% Source must be a regular file."); return; }
    if (destination) {
        if (local_path(destination,output,sizeof(output),&target) || target!=v) {
            puts("% Source and destination must be on the same device."); return;
        }
        name=strchr(destination,':')+1;
        if (!strncasecmp(name,"rommon",6) || !strcasecmp(name,"EFI")) { puts("% Protected system object."); return; }
    } else if (!confirm("Delete file?")) { puts("Delete cancelled."); return; }
    if (remount(v,1)) return;
    int result=destination ? (int)syscall(SYS_renameat2,AT_FDCWD,input,AT_FDCWD,output,1) : unlink(input);
    if (!result) result=directory_sync(v->root);
    if (remount(v,0)) result=-1;
    if (result) puts("% File operation failed; verify source and destination with dir.");
    else printf("%s completed.\n",destination?"Move":"Delete");
}

static void boot_list(void)
{
    char list[VALUE], *save;
    strcpy(list, value("BOOT"));
    if (!*list) { puts("% BOOT is empty; remaining in ROM Monitor."); return; }
    for (char *ref = strtok_r(list, ";", &save); ref; ref = strtok_r(NULL, ";", &save)) {
        image_command(ref, 1);
        if (monitor_cancelled()) break;
    }
    puts("% Boot sequence failed; remaining in ROM Monitor.");
}

static void help(void)
{
    puts("boot [SOURCE]                 Boot image, or try BOOT entries in order\n"
         "verify SOURCE                 Check the embedded release signature\n"
         "copy SOURCE bootflash:FILE.bin Receive, verify, then install an image\n"
         "delete DEVICE:FILE            Delete a file after confirmation\n"
         "move SOURCE DESTINATION       Rename a file on the same device\n"
         "dev                           Rescan storage; list storage and adapters\n"
         "dir DEVICE:                   List files\n"
         "set                           Display monitor environment\n"
         "NAME=value                    Change an environment variable\n"
         "unset NAME                    Clear an optional environment variable\n"
         "sync                          Save environment to bootflash\n"
         "confreg [0x0|0x2102|0x2142]    Manual, normal, or ignore startup-config\n"
         "ping IPv4                     Test the recovery network\n"
         "showmon                       Display bootstrap version and policy\n"
         "show secure-boot              Display firmware Secure Boot state\n"
         "SECURE_BOOT=required|optional  Require firmware trust, or allow signature-only boot\n"
         "meminfo                       Display available memory\n"
         "history                       Display recent commands\n"
         "show services                 Display supervised services\n"
         "service NAME start|stop|restart Control a recovery service\n"
         "show logging NAME             Display a service or kernel log\n"
         "reset                         Reboot (unsaved environment is lost)\n"
         "help | ?                      Display this help\n\n"
         "SOURCE: bootflash:FILE.bin, usb0:FILE.bin, tftp:, tftp://IPv4/path\n"
         "BOOT entries are separated by semicolons. flash: aliases bootflash:.\n"
         "Network: IP_ADDRESS, IP_SUBNET_MASK, DEFAULT_GATEWAY, ETHER_PORT (0-7),\n"
         "         TFTP_SERVER, TFTP_FILE. Static IPv4 only.\n"
         "Console: 115200 8N1; Ctrl-C/BREAK abort; backspace; Ctrl-U clears line.\n"
         "No setting disables image authentication.");
}

static void showmon(void)
{
    platform_inventory(0);
    puts("FractalCR System Bootstrap, Version " FCR_VERSION " (" FCR_RELEASE_KIND ")\n" FCR_COPYRIGHT "\nTechnical Support: " FCR_SUPPORT "\n"
         "x86-64 UEFI platform\n"
         "Recovery console: 115200 8N1\n"
         "Image policy: embedded release key + kernel PE signature; no bypass");
    printf("Secure Boot: %s\n", secure_boot() == 1 ? "enabled" : "DISABLED / unavailable");
    printf("Firmware policy: %s; image authentication: mandatory\n", value("SECURE_BOOT"));
}

static void command(char *line)
{
    char *args[5], *save;
    if (strchr(line, '=') && !strchr(line, ' ')) { assignment(line, 1); return; }
    unsigned argc = 0;
    for (char *p = strtok_r(line, " ", &save); p && argc < 5; p = strtok_r(NULL, " ", &save))
        args[argc++] = p;
    if (!argc) return;
    if (argc == 2 && !strcmp(args[1], "-?")) { help(); return; }
    const char *cmd = args[0];
    if (argc == 1 && (!strcmp(cmd, "help") || !strcmp(cmd, "?"))) help();
    else if (argc == 1 && !strcmp(cmd, "dev")) devices();
    else if (argc <= 2 && !strcmp(cmd, "dir")) { scan_storage(); directory(argc==1?"bootflash:":args[1]); }
    else if (argc == 2 && !strcmp(cmd,"delete")) { scan_storage(); file_change(args[1],NULL); }
    else if (argc == 3 && (!strcmp(cmd,"move") || !strcmp(cmd,"rename"))) { scan_storage(); file_change(args[1],args[2]); }
    else if (argc <= 2 && (!strcmp(cmd, "boot") || !strcmp(cmd, "verify"))) {
        scan_storage();
        if (argc == 2) image_command(args[1], !strcmp(cmd, "boot"));
        else if (!strcmp(cmd, "boot")) boot_list();
        else puts("% Usage: verify SOURCE");
    } else if (argc == 3 && !strcmp(cmd, "copy")) { scan_storage(); copy_image(args[1], args[2]); }
    else if (argc == 1 && !strcmp(cmd, "set")) {
        for (unsigned i = 0; i < NSET; i++) printf("%s=%s\n", settings[i].name, settings[i].value);
        printf("Environment: %s\n", dirty ? "modified (not saved)" : "saved/default");
    } else if (argc == 1 && !strcmp(cmd, "sync")) save_environment();
    else if (argc == 2 && !strcmp(cmd, "unset")) {
        int i = setting_index(args[1]);
        if (i >= 0 && valid_setting((unsigned)i, "")) { settings[i].value[0] = 0; dirty = 1; }
        else puts("% Cannot unset this variable.");
    } else if (argc <= 2 && !strcmp(cmd, "confreg")) {
        if (argc == 2) {
            int i = setting_index("CONFREG");
            if (valid_setting((unsigned)i, args[1])) {
                strcpy(settings[i].value, args[1]); dirty = 1;
                puts("Configuration register changed; use sync to save.");
            } else puts("% Supported: 0x0 (manual), 0x2102 (autoboot), 0x2142 (ignore startup-config).");
        }
        printf("Virtual configuration register: %s\nConsole baud: 115200\nBoot: %s\n",
               value("CONFREG"), !strcmp(value("CONFREG"), "0x0") ? "ROM Monitor" : "BOOT entries in order");
    } else if ((argc == 1 && !strcmp(cmd, "showmon")) ||
               (argc == 2 && !strcmp(cmd, "show") && !strcmp(args[1], "version"))) showmon();
    else if (argc == 2 && !strcmp(cmd, "show") && !strcmp(args[1], "secure-boot"))
        printf("Secure Boot: %s\n", secure_boot() == 1 ? "enabled" : "DISABLED / unavailable");
    else if (argc == 1 && !strcmp(cmd, "meminfo")) {
        struct sysinfo info;
        if (!sysinfo(&info)) printf("Memory: %llu KiB total, %llu KiB free\n",
            (unsigned long long)info.totalram * info.mem_unit / 1024,
            (unsigned long long)info.freeram * info.mem_unit / 1024);
    } else if (argc == 1 && !strcmp(cmd, "history")) {
        unsigned first = history_count > 16 ? history_count - 16 : 0;
        for (unsigned i = first; i < history_count; i++) printf("%u  %s\n", i+1, history[i%16]);
    } else if (argc == 2 && !strcmp(cmd, "ping")) {
        if (!ipv4(args[1])) puts("% Expected an IPv4 address.");
        else if (!network_setup()) {
            if (recovery_ping(args[1])) puts("% Ping failed.");
        }
    } else if (argc == 1 && !strcmp(cmd, "reset")) {
        if (!dirty || confirm("Unsaved environment will be lost. Reset?")) {
            struct control_reply reply;
            if (control_call(CT_RELOAD, NULL, &reply)) puts("% Supervisor unavailable.");
        }
    } else if (argc == 2 && !strcmp(cmd, "show") && !strcmp(args[1], "services")) {
        struct control_reply reply;
        if (!control_call(CT_LIST, NULL, &reply)) control_display(&reply, stdout);
    } else if (argc == 3 && !strcmp(cmd, "service")) {
        enum control_op op;
        if (!strcmp(args[2], "start")) op = CT_START;
        else if (!strcmp(args[2], "stop")) op = CT_STOP;
        else if (!strcmp(args[2], "restart")) op = CT_RESTART;
        else { puts("% Expected start, stop, or restart."); return; }
        struct control_reply reply;
        if (control_call(op, args[1], &reply)) puts("% Service request refused.");
    } else if (argc == 3 && !strcmp(cmd, "show") && !strcmp(args[1], "logging")) {
        struct control_reply reply;
        if (control_call(CT_LIST, NULL, &reply)) return;
        if (strcmp(args[2], "kernel") && !control_find(&reply, args[2])) { puts("% Unknown service."); return; }
        char path[96], buffer[1024]; snprintf(path, sizeof(path), "/run/log/%.31s.log", args[2]);
        FILE *f = fopen(path, "r");
        if (f) { while (fgets(buffer, sizeof(buffer), f)) fputs(buffer, stdout); fclose(f); }
    } else puts("% Unknown command or invalid arguments. Type help.");
}

int monitor_main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    console_init();
    puts("\nInitializing recovery devices...");
    /* USB enumeration is asynchronous. Scan for a bounded initial interval;
     * dev and storage commands rescan for media inserted later. */
    for (int i = 0; i < 30; i++) { scan_storage(); usleep(100000); }
    load_environment();
    showmon();
    service_notify();
    int first = access("/run/rommon-console-started", F_OK);
    int marker = open("/run/rommon-console-started", O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
    if (marker >= 0) close(marker);
    if (first && strcmp(value("CONFREG"), "0x0")) {
        puts("Autoboot in 5 seconds. Press Ctrl-C or send BREAK to enter ROM Monitor.");
        long long deadline = milliseconds() + 5000;
        int aborted = 0;
        while (milliseconds() < deadline) {
            int c = character(100);
            if (c == 3 || c == 0) { aborted = 1; puts("Autoboot interrupted."); break; }
        }
        if (!aborted) boot_list();
    }
    puts("Type help or ? for recovery commands.");
    for (;;) {
        char line[VALUE];
        printf("rommon %u > ", command_number);
        if (line_read(line, sizeof(line)) || !*line) continue;
        snprintf(history[history_count++ % 16], VALUE, "%s", line);
        operation_aborted = 0;
        command(line);
        command_number++;
    }
}

static volatile sig_atomic_t worker_stop;
static void worker_signal(int sig) { (void)sig; worker_stop = 1; }
int recovery_worker(const char *name)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    if (!strcmp(name, "console")) return monitor_main();
    if (!strcmp(name, "logging")) return kernel_logger();
    signal(SIGTERM, worker_signal); signal(SIGINT, worker_signal);
    int storage = !strcmp(name, "storage");
    if (!storage && strcmp(name, "network")) return 2;
    if (!storage) {
        FILE *f = fopen("/run/recovery-network.conf", "r");
        if (f) {
            char line[VALUE+64];
            while (fgets(line, sizeof(line), f)) {
                char *end = strchr(line, '\n');
                if (!end) { fclose(f); return 1; }
                *end = 0;
                if (assignment(line, 0)) { fclose(f); return 1; }
            }
            fclose(f);
            if (network_apply()) return 1;
        }
    }
    int ready = 0;
    while (!worker_stop) {
        if (storage) {
            scan_devices();
            int fd = open("/run/recovery-volumes.new", O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0600);
            if (fd < 0) return 1;
            int ok = !write_all(fd, volumes, nvol * sizeof(volumes[0]));
            close(fd);
            if (!ok || rename("/run/recovery-volumes.new", "/run/recovery-volumes")) return 1;
        }
        if (!ready) { service_notify(); ready = 1; }
        usleep(100000);
    }
    if (storage) {
        for (unsigned i = 0; i < nvol; i++) if (umount(volumes[i].root)) perror("recovery unmount");
        unlink("/run/recovery-volumes");
    } else if (*value("IP_ADDRESS")) {
        char names[8][IF_NAMESIZE];
        unsigned count = nic_names(names), index = (unsigned)atoi(value("ETHER_PORT"));
        if (index < count) {
            network_gateway(names[index], value("DEFAULT_GATEWAY"), 0);
            network_address(names[index], "0.0.0.0", NULL);
        }
    }
    return 0;
}
