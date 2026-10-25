# System `.bin` v2

```
complete PE-signed x86-64 bzImage | uint64 security epoch (big endian) | 256-byte RSA signature | FCRBIN02
```

The bzImage contains its own initramfs through `CONFIG_INITRAMFS_SOURCE`.
ROMMON does not accept a separate initrd. The fixed eight-byte trailer identifies
the format. The signature is RSA-2048 PKCS#1 v1.5 with SHA-256 over:

```
ASCII("FractalCR system image v2\n") || complete bzImage || uint64 security epoch
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
legacy unsigned-kexec fallback. ROMMON requires the firmware `SecureBoot`
variable by default; explicitly configured development policy permits handoff
without firmware authentication. Both image signatures remain mandatory.

ROMMON streams the payload to an anonymous memory file while verifying it.
After successful verification it seals that file against writes, shrink, and
growth. The exact descriptor is passed to `kexec_file_load`. There is no
path-based verify/reopen race.

After signature verification, ROMMON rejects epochs below its compiled minimum.
`image-policy.json` selects the development signing epoch and ROMMON floor.
For external builds, the public trust profile supplies the embedded minimum.
V1 envelopes are rejected, including correctly signed legacy images. Changing
the epoch bytes without resigning invalidates the envelope.

This is an explicit security-epoch policy, not an automatic per-boot counter.
Multiple releases in the same epoch can be installed interchangeably. To retire
an epoch, build ROMMON with a higher floor under new firmware/release keys,
qualify recovery, and complete authenticated dbx/KEK/PK rotation to reject old
ROMMONs. A writable flash counter would be replayable, so it is not a trust
anchor. Firmware resets or disabled Secure Boot defeat this chain's root of
trust. Automatic A/B recovery after interrupted installation is unsupported.

`bootflash:` (also accepted as `flash:`) and USB FAT volumes are mounted
read-only by default. Recovery copy temporarily remounts bootflash writable,
stages and authenticates the complete envelope, and renames it into place only
after validation. TFTP downloads use a bounded RAM filesystem. Neither source
changes the boot verification path.

`verify` and copy authenticate the outer release envelope. The independent
kernel PE check runs on handoff, so successful verification alone does not
promise that an image is compatible or bootable. Local verification accepts up
to 512 MiB; recovery downloads/copies have the same 512 MiB cap.
