# System `.bin` v1

```
complete PE-signed x86-64 bzImage | 256-byte RSA signature | FCRBIN01
```

The bzImage contains its own initramfs through `CONFIG_INITRAMFS_SOURCE`.
ROMMON does not accept a separate initrd. The fixed eight-byte trailer identifies
the format. The signature is RSA-2048 PKCS#1 v1.5 with SHA-256 over:

```
ASCII("FractalCR system image v1\n") || complete bzImage
```

Maximum total file size is 512 MiB. The format has no unauthenticated JSON,
filename pointers, archive members, runtime trust-key lookup, or selectable boot
arguments. The x86-64 board and serial command line are compiled into ROMMON.
Changing those parameters requires a new signed ROMMON.

There are two distinct development keys:

* **Firmware key:** its certificate is enrolled into the disposable QEMU firmware
  variables. It signs ROMMON's UKI. Its certificate is also built into ROMMON's
  kernel trusted keyring, which checks the PE signature of the system bzImage.
* **Release key:** its public half is compiled into the C monitor in ROMMON's
  embedded initramfs. It verifies the complete `.bin` payload.

The Linux PE check allows `kexec_file_load` under kernel lockdown without a
legacy unsigned-kexec fallback. ROMMON also explicitly requires the firmware
`SecureBoot` variable to be enabled before handoff.

ROMMON streams the payload to an anonymous memory file while verifying it.
After successful verification it seals that file against writes, shrink, and
growth. The exact descriptor is passed to `kexec_file_load`. There is no
path-based verify/reopen race.

V1 intentionally does not implement rollback counters, revocation lists,
online key rotation, A/B boot selection, or a release version policy. A previous
image signed by a trusted key remains trusted. Those require an explicit
policy and authenticated state in a later milestone.

`bootflash:` (also accepted as `flash:`) and USB FAT volumes are mounted
read-only by default. Recovery copy temporarily remounts bootflash writable,
stages and authenticates the complete envelope, and renames it into place only
after validation. TFTP downloads use a bounded RAM filesystem. Neither source
changes the boot verification path.

`verify` and copy authenticate the outer release envelope. The independent
kernel PE check runs on handoff, so successful verification alone does not
promise that an image is compatible or bootable. Local verification accepts up
to 512 MiB; recovery downloads/copies currently have a separate 128 MiB cap.
