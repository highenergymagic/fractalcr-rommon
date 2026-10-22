# Boot and recovery milestone

The active implementation is a signed Buildroot ROM Monitor with a CIP kernel,
serial recovery CLI, USB storage and Ethernet/TFTP recovery. The signed
`system.bin` is still a tiny embedded-root boot witness, not the OE container OS.

The [ISR4331 research and recovery contract](docs/isr4331-research.md) explains
the Cisco source evidence, command wording, boot flow, signature checks,
recovery procedures and intentional differences. [FORMAT.md](FORMAT.md)
specifies our image envelope and trust chain.

Validated on 2026-10-20: eleven Linux QEMU/OVMF security and recovery scenarios,
native Windows QEMU autoboot, eleven verifier cases, and monitor input tests.

| Artifact | Size |
|---|---:|
| Signed `rommon.efi`, including drivers and recovery userland | 7,586,744 bytes (7.24 MiB) |
| Signed boot-witness `system.bin` | 3,353,432 bytes (3.20 MiB) |

## Build and run

From Windows, using Docker on `carbon` through SSH and passwordless `doas`:

```powershell
.\boot\build.ps1
.\tools\remote-build.ps1 -Action Status
# After the remote build and tests finish:
.\boot\build.ps1 -Action Export
.\boot\run.ps1
```

Build outputs, serial transcripts and hash-bound test results are exported to
`out/carbon/boot`; `validation.md` summarizes results only after their hashes match the
current images. The wrapper supports `-Action Build`, `-Action Test`, and
`-Action Export`. Build and Test start a detached remote job; inspect its status
and log through `tools/remote-build.ps1`. A lock prevents concurrent builds.
Pass `-BuildHost` to select another SSH alias with passwordless `doas`.

The named Docker volume `fractalcr-boot-work` retains sources, compiler outputs
and generated development signing keys. Private keys are never exported.
Tests enroll the development firmware certificate only in disposable OVMF
variables; they do not modify the host firmware.
The first build on carbon generates new development keys in that host's volume.
Old local artifacts remain in `out/boot`; to run them explicitly, use
`.\boot\run.ps1 -ArtifactDirectory .\out\boot`.

The interactive VM keeps its changes in `flash-interactive.img`. Reset works
inside the VM, and `sync` persists across subsequent launches. `-Fresh` replaces
that session disk and its firmware variables with the current tested fixtures.
After rebuilding, use `-Fresh` when you want the new monitor in that VM.

The first boot automatically starts the witness after five seconds. Press
Ctrl-C or send BREAK during the countdown to enter recovery. In QEMU's serial
multiplexer, Ctrl-A B sends BREAK and Ctrl-A X exits QEMU.

## Console workflow

```text
rommon 1 > showmon
rommon 2 > dev
rommon 3 > dir bootflash:
rommon 4 > verify bootflash:system.bin
rommon 5 > boot bootflash:tampered.bin
rommon 6 > boot bootflash:system.bin
```

The altered image must be rejected. The valid image must reach
`FRACTALCR_SYSTEM_BOOT_OK` and its embedded PID 1. `verify` alone checks the
release envelope; boot also requires the kernel's PE signature check.

To boot into the monitor on subsequent starts:

```text
confreg 0x0
sync
reset
```

`confreg 0x2102` restores normal autoboot. `confreg 0x2142` boots the OS while
ignoring startup configuration for physical-console password/configuration
recovery. Save with `sync` only when persistence is intended; otherwise the
change lasts for this monitor session. See the
[recovery procedure](../os/docs/aaa-and-management.md). Other register values
are unsupported.

## USB recovery

Attach a disposable recovery disk exported by the tests:

```powershell
.\boot\run.ps1 -UsbImage .\out\boot\usb-usb-boot.img
```

Then:

```text
dev
dir usb0:
copy usb0:system.bin bootflash:recovered.bin
BOOT=bootflash:recovered.bin;bootflash:system.bin
confreg 0x2102
sync
reset
```

You can also `boot usb0:system.bin` directly without writing internal storage.
USB volumes support FAT16/FAT32. Internal recovery flash is identified by its
`FRFLASH` FAT label. Other datastore disks are not mounted or formatted.

## Network recovery

QEMU's user network can serve the exported image directory without opening
host ports:

```powershell
.\boot\run.ps1 -TftpRoot .\out\boot
# An emulated Intel adapter:
.\boot\run.ps1 -TftpRoot .\out\boot -NetworkAdapter e1000
```

At the monitor:

```text
dev
ETHER_PORT=0
IP_ADDRESS=10.0.2.15
IP_SUBNET_MASK=255.255.255.0
DEFAULT_GATEWAY=10.0.2.2
TFTP_SERVER=10.0.2.2
TFTP_FILE=system.bin
ping 10.0.2.2
copy tftp: bootflash:network.bin
BOOT=bootflash:network.bin;bootflash:system.bin
sync
boot
```

`boot tftp:` boots directly from RAM; `tftp://IPv4/path` is also accepted.
Only static IPv4 is implemented. Downloads and recovery copies are capped at
512 MiB; network operations have a 120-second deadline and Ctrl-C cancellation.

## Validation

The automated suite exercises:

- Actual C verifier: valid image, altered payload/signature, wrong key,
  truncation, appended data, unsigned file, and symlink rejection.
- Input parser: path traversal, shell-like input, invalid network settings,
  unsupported register values, and all-or-nothing environment loading.
- Secure Boot: unsigned ROMMON rejection, altered system rejection, disabled
  Secure Boot handoff refusal under the default required policy, unsigned
  inner-kernel refusal, and authenticated kexec with either firmware policy.
- USB mass storage: discovery, hot-plug, direct boot, verified installation, rejected
  overwrite preserving the previous image.
- Persistence: saved manual boot, ordered autoboot after reset, recovery from
  a corrupt newest environment slot.
- Ethernet: ping, TFTP rejection, verified copy, and direct TFTP boot using
  both virtio-net and e1000.
- UART BREAK: interrupts autoboot; failed alternatives return to the monitor.

Native Windows QEMU can independently boot the exported files:

```powershell
python .\boot\tests\native-smoke.py
python .\boot\tools\report.py --artifacts .\out\boot --native
```

The Linux tests use TCG, so the carbon Docker host does not need `/dev/kvm`, privileged
containers or nested virtualization. Tests establish emulated hardware behavior,
not physical NIC qualification or KVM acceleration. Consult `result-*.json` and
the matching serial logs for the actual tested artifact hashes.

## Build inputs and remaining scope

Pinned core inputs: Buildroot 2025.02.12, CIP Linux 6.12.112-cip33 and the Bootlin
x86-64 musl toolchain selected by that Buildroot release. Source downloads have
SHA-256 pins. The Debian base image is digest-pinned; its package repositories
are not snapshot-pinned. Rebuilding the builder from its Dockerfile can therefore
change dependencies. The [reproducibility workflow](../service/README.md) records
and reuses the builder's immutable image ID and compares forced appliance builds
byte for byte. It does not claim independent clean-room dependency reproduction.

ROMMON includes built-in virtio-net, e1000/e1000e/igb/igc, r8169, USB host/mass
storage, AHCI and NVMe drivers. No graphics console, loadable modules or Unix
shell is included. The only BusyBox applets are the internal network helpers.
The host OS uses its own glibc-based OE userspace; ROMMON retains musl. Both
compile the same small service supervisor. Recovery storage, networking,
logging and console run as separate managed processes.

The [release key lifecycle](docs/release-key-management.md) provides encrypted
offline custody, public-only builds, signing, rotation and revocation.
`confreg 0x2142` supports physical-console password recovery in the OE image
while retaining mandatory image authentication.

Not implemented: per-release rollback protection, a power-failure qualified
A/B updater, ROMMON self-update and unattended OS installation.
The OE image provides the management CLI,
explicit datastore initialization, Docker and bundled Kata components.
