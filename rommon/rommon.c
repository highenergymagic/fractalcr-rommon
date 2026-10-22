/*
 * FractalCR ROM Monitor verifier. No login shell and no unsigned boot path.
 * The release key is a generated C header inside the signed ROMMON image.
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/kexec.h>
#include <linux/reboot.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "release-key.h"
#include "monitor.h"

#define MAX_IMAGE (512UL * 1024 * 1024)
#define SIG_LEN 256
#define TRAILER_LEN (8 + SIG_LEN + 8)
#ifndef FCR_MIN_SECURITY_EPOCH
#define FCR_MIN_SECURITY_EPOCH 1ULL
#endif
static const unsigned char magic[8] = "FCRBIN02";
static const char domain[] = "FractalCR system image v2\n";
static const char command_line[] = "console=ttyS0,115200 rdinit=/init panic=-1 quiet loglevel=0";
static const char secure_var[] = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c";

int secure_boot(void)
{
    unsigned char value[5];
    int fd = open(secure_var, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    ssize_t n = read(fd, value, sizeof(value));
    close(fd);
    return n == sizeof(value) ? value[4] == 1 : -1;
}

int write_all(int fd, const void *data, size_t n)
{
    const unsigned char *p = data;
    while (n) {
        ssize_t done = write(fd, p, n);
        if (done < 0 && errno == EINTR) continue;
        if (done <= 0) return -1;
        p += done;
        n -= (size_t)done;
    }
    return 0;
}

/*
 * Verify and copy the exact bytes into a sealed memfd. kexec receives that
 * descriptor, never reopens the path: changing flash after verification cannot
 * substitute the boot payload. Signature is RSA-2048 / PKCS#1 v1.5 / SHA-256,
 * domain separated. The payload is the entire PE-signed bzImage, containing
 * CONFIG_INITRAMFS_SOURCE. No external initrd or user-supplied cmdline exists.
 */
int verified_image(const char *path)
{
    struct stat st;
    unsigned char buf[65536], trailer[TRAILER_LEN];
    int in = -1, out = -1, ok = 0, progress = 0;
    BIO *bio = NULL;
    EVP_PKEY *key = NULL;
    EVP_MD_CTX *md = NULL;

    in = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (in < 0 || fstat(in, &st) || !S_ISREG(st.st_mode) ||
        st.st_size <= TRAILER_LEN || (uint64_t)st.st_size > MAX_IMAGE) {
        puts("% Image missing, not regular, or outside size limits.");
        goto done;
    }
    bio = BIO_new_mem_buf(release_key_pem, -1);
    if (!bio || !(key = PEM_read_bio_PUBKEY(bio, NULL, NULL, NULL)) ||
        EVP_PKEY_get_bits(key) != 2048 || EVP_PKEY_base_id(key) != EVP_PKEY_RSA ||
        !(md = EVP_MD_CTX_new()) ||
        EVP_DigestVerifyInit(md, NULL, EVP_sha256(), NULL, key) != 1 ||
        EVP_DigestVerifyUpdate(md, domain, sizeof(domain) - 1) != 1) {
        puts("% Embedded verification key failure.");
        goto done;
    }
    out = memfd_create("verified-system", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (out < 0) { perror("memfd_create"); goto done; }
    off_t remaining = st.st_size - TRAILER_LEN;
    off_t total = remaining;
    unsigned marks = 0;
    fputs("Loading and verifying image: ", stdout); fflush(stdout); progress = 1;
    while (remaining) {
        if (monitor_cancelled()) { puts("% Verification aborted."); goto done; }
        size_t count = remaining < (off_t)sizeof(buf) ? (size_t)remaining : sizeof(buf);
        ssize_t n = read(in, buf, count);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0 || EVP_DigestVerifyUpdate(md, buf, (size_t)n) != 1 ||
            write_all(out, buf, (size_t)n)) goto done;
        remaining -= n;
        unsigned completed = (unsigned)((uint64_t)(total-remaining)*50/(uint64_t)total);
        while (marks<completed) { putchar('#'); marks++; }
        fflush(stdout);
    }
    size_t have = 0;
    while (have < sizeof(trailer)) {
        ssize_t n = read(in, trailer + have, sizeof(trailer) - have);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) goto done;
        have += (size_t)n;
    }
    if (read(in, buf, 1) != 0 ||
        memcmp(trailer + 8 + SIG_LEN, magic, sizeof(magic)) ||
        EVP_DigestVerifyUpdate(md, trailer, 8) != 1 ||
        EVP_DigestVerifyFinal(md, trailer + 8, SIG_LEN) != 1) {
        puts("% AUTHENTICATION FAILED: image rejected.");
        goto done;
    }
    uint64_t epoch = 0;
    for (unsigned i = 0; i < 8; i++) epoch = (epoch << 8) | trailer[i];
    if (epoch < FCR_MIN_SECURITY_EPOCH) {
        puts("% SECURITY POLICY: image security epoch is below this ROMMON's minimum.");
        goto done;
    }
    if (fcntl(out, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) ||
        lseek(out, 0, SEEK_SET) < 0) goto done;
    /*
     * kernel_read_file() denies write access even for a sealed memfd. Reopen
     * this exact sealed inode read-only through procfs, then drop its writable
     * descriptor. This is not a second lookup of the flash filename.
     */
    char fd_path[64];
    snprintf(fd_path, sizeof(fd_path), "/proc/self/fd/%d", out);
    int readonly = open(fd_path, O_RDONLY | O_CLOEXEC);
    if (readonly < 0) goto done;
    close(out);
    out = readonly;
    puts(" done"); progress = 0;
    puts("Image signature verified.");
    ok = 1;
done:
    if (progress) puts(" failed");
    EVP_MD_CTX_free(md);
    EVP_PKEY_free(key);
    BIO_free(bio);
    if (in >= 0) close(in);
    if (!ok && out >= 0) close(out);
    return ok ? out : -1;
}

void boot_image(int fd, int ignore_startup)
{
    if (secure_boot() != 1 && secure_boot_required()) {
        puts("% Secure Boot is not enabled; boot policy refuses handoff.");
        return;
    }
    if (secure_boot() != 1)
        puts("% Firmware trust is unavailable. Image signatures remain mandatory.");
    /* Kernel independently requires a trusted PE signature. No legacy fallback. */
    char selected_command_line[256];
    snprintf(selected_command_line, sizeof(selected_command_line), "%s%s", command_line,
             ignore_startup ? " fcr.ignore_startup=1" : "");
    if (ignore_startup) puts("% Configuration register 0x2142: startup configuration will be ignored.");
    if (syscall(SYS_kexec_file_load, fd, -1, strlen(selected_command_line)+1, selected_command_line,
                KEXEC_FILE_NO_INITRAMFS) < 0) {
        printf("%% kexec_file_load refused image: %s\n", strerror(errno));
        return;
    }
    puts("Booting authenticated system image...");
    fflush(stdout);
    sync();
    if (reboot(LINUX_REBOOT_CMD_KEXEC) < 0) perror("kexec reboot");
}

int main(int argc, char **argv)
{
    /* Host test mode exercises the identical verifier with a compiled test key. */
#ifdef ROMMON_HOST_TEST
    if (argc == 3 && !strcmp(argv[1], "--verify")) {
        int fd = verified_image(argv[2]);
        if (fd < 0) return 1;
        int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
        if ((fcntl(fd, F_GETFL) & O_ACCMODE) != O_RDONLY ||
            (fcntl(fd, F_GET_SEALS) & seals) != seals) {
            close(fd);
            return 1;
        }
        close(fd);
        return 0;
    }
    return 2;
#else
    return argc == 2 ? recovery_worker(argv[1]) : 2;
#endif
}
