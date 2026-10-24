/* Host tests for untrusted recovery configuration; no mounts or network calls. */
#include "../rommon/monitor.c"
#include <assert.h>
int secure_boot(void) { return 1; }
int verified_image(const char *path) { (void)path; return -1; }
void boot_image(int fd, int ignore_startup, const char *image) { (void)fd; (void)ignore_startup; (void)image; }
int write_all(int fd, const void *p, size_t n) { (void)fd; (void)p; (void)n; return -1; }
int main(void)
{
    assert(reference_ok("bootflash:system.bin"));
    assert(reference_ok("flash:system.bin"));
    assert(reference_ok("usb0:recovery.bin"));
    assert(reference_ok("tftp://10.0.2.2/releases/system.bin"));
    const char *invalid[] = {"bootflash:../x", "bootflash:.x", "bootflash:/EFI/x",
        "usb4:x", "tftp://evil/x", "tftp://1.2.3.4/../x", "tftp://1.2.3.4/a;reset",
        "tftp://1.2.3.4/", "bootflash:foo\nreset", "/dev/sda", "bootflash:a/b"};
    for (unsigned i = 0; i < sizeof(invalid)/sizeof(invalid[0]); i++) assert(!reference_ok(invalid[i]));
    int boot = setting_index("BOOT"), reg = setting_index("CONFREG");
    assert(valid_setting(boot, "bootflash:bad.bin;usb0:good.bin;tftp:"));
    assert(!valid_setting(boot, "bootflash:a;;usb0:b"));
    assert(!valid_setting(boot, "bootflash:a;"));
    assert(valid_setting(reg, "0x0") && valid_setting(reg, "0x2102"));
    assert(valid_setting(reg, "0x2142") && !valid_setting(reg, "0xdead"));
    int policy = setting_index("SECURE_BOOT");
    assert(valid_setting(policy, "required") && valid_setting(policy, "optional"));
    assert(!valid_setting(policy, "") && !valid_setting(policy, "off"));
    assert(secure_boot_required());
    int mask = setting_index("IP_SUBNET_MASK");
    assert(valid_setting(mask, "255.255.255.0"));
    assert(!valid_setting(mask, "255.0.255.0"));
    assert(!valid_setting(mask, "0.0.0.0"));
    assert(!valid_setting(setting_index("ETHER_PORT"), "-1"));
    assert(!valid_setting(setting_index("TFTP_FILE"), "foo;reboot"));
    char inject[] = "KERNEL_CMDLINE=init=/bin/sh";
    assert(assignment(inject, 0) == -1);
    /* A checksum-valid but schema-invalid environment must be rejected as a
     * whole; otherwise a bad later line could leave earlier changes active. */
    char body[4096], doc[8192], hash[65];
    size_t used = 0;
    for (unsigned i = 0; i < NSET; i++)
        used += (size_t)snprintf(body+used, sizeof(body)-used, "%s=%s\n", settings[i].name,
            i == (unsigned)boot ? "usb0:changed.bin" : settings[i].value);
    used += (size_t)snprintf(body+used, sizeof(body)-used, "VERIFY=off\n");
    unsigned char digest[32]; SHA256((unsigned char *)body, used, digest);
    for (unsigned i = 0; i < 32; i++) sprintf(hash+i*2, "%02x", digest[i]);
    int size = snprintf(doc, sizeof(doc), ENV_VERSION " 8 %s\n%s", hash, body);
    uint64_t generation;
    assert(parse_environment(doc, (size_t)size, &generation) == -1);
    assert(!strcmp(value("BOOT"), "bootflash:system.bin"));
    puts("MONITOR_INPUT_TESTS_OK (paths, network values, policy, atomic environment parse)");
    return 0;
}
