# Recovery architecture

This reference describes the FractalCR recovery environment, image trust, and
boot settings. For command procedures, see the [ROM Monitor guide](../README.md).
ROMMON settings are independent of system startup configuration.

## Boot and authentication flow

```text
UEFI Secure Boot policy
  -> verifies FractalCR ROMMON EFI signature
  -> signed UKI contains CIP kernel, recovery PID1, drivers and trusted keys
  -> discover FAT recovery volumes and Ethernet devices
  -> validate/load saved monitor environment
  -> manual mode OR interruptible 5-second autoboot window
  -> try BOOT entries in order
       -> local file / USB file / bounded TFTP receive into RAM
       -> verify whole-system release signature with embedded public key
       -> copy authenticated payload into sealed RAM file
       -> kernel verifies PE signature through kexec_file_load
       -> kexec into kernel with embedded userland
  -> if no entry boots, remain at recovery prompt
```

The embedded release key authenticates the `.bin` envelope; the kernel
independently enforces the PE signature before handoff. Environment data cannot
set a command line, install a key, disable signatures, or request legacy kexec.
The exact authenticated payload is handed to the kernel via a sealed descriptor,
so boot does not reopen a mutable flash path after validation.

The current envelope is specified in [FORMAT.md](../FORMAT.md). It uses a
domain-separated RSA-2048/SHA-256 scheme.
`verify` and recovery copy check the release envelope. PE acceptance and kernel
format/architecture checks happen at boot. A release-signed but incompatible
payload can therefore pass `verify` and still fail the handoff; the monitor
reports the kernel's refusal. Compatibility checks occur at this stage; the
envelope does not contain a separate platform/ABI manifest.

The initial keys are development keys. Firmware signing and release signing
use separate private keys, retained in the build volume. Firmware enrollment is
performed only in disposable OVMF variables by the tests. The public release
key is compiled into the monitor. No checksum file on writable media acts as a
trust root. The [release key workflow](release-key-management.md) adds public-only
builds, offline signing, authenticated firmware-key rotation and signer revocation.
Automatic rollback counters, TPM attestation, and ROMMON self-update are
unsupported. System provisioning is described in the
[deployment guide](https://github.com/highenergymagic/fractalcr/blob/main/os/docs/releases.md).

## Recovery workflows

### USB recovery

Insert FAT16/FAT32 media, run device discovery, inspect its files, then boot the
signed image directly or copy it to internal flash. USB numbers represent
discovered filesystems, not fixed physical port identities; check `dev` after
changing media. Storage commands rescan, so boot-time insertion is not required.
An internal FAT volume must carry the label `FRFLASH` to become `bootflash:`.
The monitor does not format or repurpose other disks.

For a persistent repair, copy to a new filename, set `BOOT` to the new image
with a known-good alternative, save, and reset. Installing an image does not
implicitly change `BOOT`. Copying to an existing filename requires confirmation.
The destination is staged, fsynced, authenticated, then renamed. Authentication
failure before rename leaves the previous image in place.

FAT metadata and commodity storage controllers do not provide a complete
power-loss transaction guarantee. Use a new destination filename for updates;
retain another working image and external recovery media. Updates do not
provide automatic A/B recovery.

### Network recovery

Set `ETHER_PORT`, `IP_ADDRESS`, `IP_SUBNET_MASK`, and, when needed,
`DEFAULT_GATEWAY`. Set `TFTP_SERVER` and `TFTP_FILE` for the short `tftp:` form,
or supply an IPv4 URL. Use `ping` to diagnose reachability, then boot or copy.
Recovery uses static IPv4 addresses independently of the system image's networking configuration.

TFTP is only transport. Downloaded bytes remain untrusted until signature
verification succeeds. Downloads and recovery copies have a 512 MiB cap and
network transfers have a 120-second deadline. Local verification uses the same
512 MiB ceiling. These limits apply to all recovery transports.

### Missing or damaged saved environment

Two environment files alternate generations. Each contains a version, a
generation, a SHA-256 checksum of its body, and a strictly validated set of
values. A malformed slot is ignored; the newest valid slot wins. If neither
is usable, compiled defaults select `bootflash:system.bin` with an interruption
window. The checksum detects corruption, not malicious edits. None of these
values can disable image authentication. The explicit 0x2142 recovery setting
enables physical-console password recovery without loading startup credentials.

These are ROMMON settings, separate from the JSON OS `startup-config`.
They live on bootflash so that array failure cannot prevent recovery.

## Supported hardware and restrictions

Built-in Ethernet drivers: virtio-net, Intel e1000/e1000e/igb/igc, Realtek
r8169, USB CDC Ethernet/NCM, RTL8152, and ASIX USB Ethernet. Storage: virtio block, AHCI/SATA, NVMe; USB
xHCI/EHCI/OHCI/UHCI, bulk mass storage and UAS. No loadable modules or graphics
console are included. Hardware variants requiring external firmware are
unqualified.
The automated recovery suite targets virtio-net, e1000 and QEMU xHCI mass
storage. Physical hardware compatibility requires qualification on the target model.

FractalCR fixes the console at 115200 8N1. The accepted register values select
boot modes only; they do not emulate Cisco baud-rate bits. `0x2142` supplies
a fixed recovery flag to the authenticated OS. It ignores startup configuration
without deleting it, disables initial remote access, and permits physical-console
credential repair. Restore 0x2102 for normal authenticated startup. See the
[password-recovery procedure](https://github.com/highenergymagic/fractalcr/blob/main/os/docs/aaa-and-management.md).
Recovery storage supports FAT16/FAT32; exFAT and NTFS are unsupported. Recovery
networking supports static IPv4; DHCP and IPv6 are unavailable in ROMMON.

Boot-chain tests retain a small signed witness payload. The separate system
image contains Docker, Kata, array configuration and the appliance CLI; its
runtime qualification is recorded separately from the ROMMON witness tests.
