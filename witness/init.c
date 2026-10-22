/* Boot witness only. This is not the future appliance userland. */
#define _GNU_SOURCE
#include <stdio.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <unistd.h>

int main(void)
{
    struct utsname u;
    setvbuf(stdout, NULL, _IONBF, 0);
    mkdir("/proc", 0555);
    mount("proc", "/proc", "proc", 0, NULL);
    uname(&u);
    printf("\nFRACTALCR_SYSTEM_BOOT_OK\nCIP kernel: %s\n", u.release);
    printf("Embedded initramfs PID: %d\n", getpid());
    puts("ROMMON authenticated and kexec booted this image.");
    puts("This is the boot witness; the OE container OS comes next.");
    for (;;) pause();
}
