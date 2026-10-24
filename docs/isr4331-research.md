# ISR 4331 research and FractalCR recovery contract

This is an engineering reference, not the first-boot guide. Use the
[ROMMON guide](../README.md) for operational steps.

Research date: 2026-10-20. This is a behavioral study and an independently
implemented monitor, not Cisco firmware or a binary-compatible emulator.
The implementation target is an x86-64 QEMU/KVM appliance with serial console.

## Evidence and scope

The strongest hardware-specific source is Cisco's [password-recovery procedure](https://www.cisco.com/c/en/us/support/docs/ios-nx-os-software/ios-xe-16/217045-troubleshoot-password-recovery-in-cisco.html).
It names an ISR4331 running IOS XE 16.12.4 and includes its console transcript.
The observed sequence is hardware initialization, PCIe checks, ROM integrity,
bootstrap identification, ROM selection/reset information, platform and memory
identification, locating the configured image, package verification, then IOS XE.
BREAK is recognized in the transition between POST and the operating system.
The recovery procedure temporarily ignores startup configuration, restores access
inside IOS, then restores normal boot behavior. This is configuration recovery,
not permission to execute an unauthenticated image.

The [ISR4000 hardware guide's ROM Monitor chapter](https://www.cisco.com/c/en/us/td/docs/routers/access/4400/hardware/installation/guide4400-4300/C4400_isr/rommon.html)
is the family-level command reference. Its examples include 4351 and 4451, so
they must not be presented as an exact 4331 transcript. ROMMON is a separate
program; its management-port configuration is independent of IOS. It uses a
numbered prompt, lowercase case-sensitive commands and uppercase environment
assignments. Environment changes require explicit persistence. Its help includes
storage discovery/listing, manual boot, configuration-register control, environment
inspection, history and monitor identification. Ctrl-C interrupts operations.
The larger command set also contains board/debugger operations that are outside
our appliance's recovery needs.

The [IOS XE 17 configuration-register chapter](https://www.cisco.com/c/en/us/td/docs/routers/access/isr4400/software/configuration/xe-17/isr4400-sw-config-xe-17/m_configuration-register.html)
describes a 16-bit persistent register, not just a Boolean boot switch. The
low four bits select boot behavior; a separate bit ignores saved configuration.
Some historical bits are deprecated on this family. The documented factory
serial speed is 9600. The boot field distinguishes manual monitor entry from
automatic selection, including ordered configured images and a flash fallback.
Consequently, accepting every hexadecimal value while implementing only two
states would be misleading.

The [ISR4000 IOS XE 16.11 installation chapter](https://www.cisco.com/c/en/us/td/docs/routers/access/4400/software/configuration/xe-16-11/isr4400swcfg-xe-16-11-book/install.html)
distinguishes consolidated `.bin` images from expanded installations selected by
`packages.conf`. It documents TFTP boot settings, transfer progress, reception
completion, package validation and the handoff to IOS XE. The examples expose
adapter selection and transfer parameters as monitor environment values.
USB media are numbered; operators must inspect the actual enumeration.
The familiar interactive copy workflow also appears at the IOS privileged
prompt. This does **not** establish that every ISR ROMMON version supports an
IOS-style copy command. Our recovery copy command is an intentional extension.
This chapter contains historical transcripts and later amendments; its page
title is not a guarantee that every sample describes the same release.

Cisco's [incorrect-platform-image recovery note](https://www.cisco.com/c/en/us/support/docs/routers/4000-series-integrated-services-routers/200678-Troubleshoot-Cisco-4000-Series-ISR-Stuck.html)
documents a persistent boot loop caused by using the wrong ISR family image.
Recovery separates stopping automatic boot, returning to the monitor, manually
selecting a correct image, and repairing the saved boot selection inside the OS.
It includes internal-flash and USB paths. This demonstrates why image
authenticity, platform compatibility, and the persistence of boot selection are
three distinct concerns. A successful one-time USB boot does not itself repair
the next power-on.

The [Trustworthy Systems boot-integrity chapter](https://www.cisco.com/c/en/us/td/docs/switches/lan/c9000/sec-crypto/tws/trustworthy-systems-guide/boot-integrity-visibility.html)
explains embedded public release keys, signed packages, and platform/CPU checks.
It explicitly distinguishes a corruption-detection checksum from cryptographic
authentication. This is broader IOS XE/Catalyst documentation, **not evidence
that every algorithm, attestation facility, or key-rollover behavior exists on
every 4331 release**. We use it to interpret the architecture, not to claim
algorithm-level compatibility.

The [ISR4000 programmable-hardware upgrade guide](https://www.cisco.com/c/en/us/td/docs/routers/access/4400/cpld/isr4400_hwfp.html)
treats ROMMON and other programmable components as versioned firmware with
platform-specific compatibility requirements. ROMMON maintenance is a separate
operation from copying an ordinary OS image. Our monitor likewise must not
silently replace its own trust anchor as a side effect of installing a system
image. Redundant Cisco ROM identities and board-component status have no
equivalent in the current QEMU prototype and are not fabricated in its banner.

## What the appliance should feel like

The operator should know which layer is speaking. The firmware verifies and
launches the monitor. The monitor identifies itself, discovers recovery devices,
loads its small environment, and offers a short interruption window. It then
attempts the configured image. A failure names the failed stage and leaves an
actionable recovery prompt. A successful handoff ends the monitor's role.

The monitor is a finite recovery command language. It does not launch a Unix
shell, interpret shell metacharacters, require a Linux device path, or load the
OS's JSON startup configuration to bring up a recovery port.

Our implemented console vocabulary:

| Operator intent | FractalCR command / behavior |
|---|---|
| Identify bootstrap and verification policy | `showmon` |
| Discover storage and select a recovery adapter | `dev`; `ETHER_PORT=0` |
| Inspect recovery media | `dir bootflash:`; `dir usb0:` |
| Check an image without booting | `verify usb0:system.bin` |
| Start one image | `boot usb0:system.bin` |
| Try the saved selection now | `boot` |
| Set ordered alternatives | `BOOT=bootflash:system.bin;usb0:rescue.bin` |
| Persist boot/network settings | `sync` |
| Enter the monitor on next start | `confreg 0x0`, then `sync` |
| Restore automatic boot | `confreg 0x2102`, then `sync` |
| Install recovery image | `copy usb0:system.bin bootflash:recovered.bin` |
| Network recovery | `boot tftp:` or `boot tftp://192.0.2.10/system.bin` |
| Test network reachability | `ping 192.0.2.10` |
| Reboot | `reset`; confirm if unsaved changes exist |

The prompt is `rommon N > `. Empty lines do not advance it. Backspace edits,
Ctrl-U clears the input, and Ctrl-C/BREAK abort input or interrupt autoboot.
Transfer subprocesses have cancellation and deadlines. `history` displays a
bounded recent history; cursor-key history navigation is not implemented.
`help`, `?`, and the `-?` help suffix expose the available subset.

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

A filename, successful download or printed checksum is not authorization.
Only the embedded release key authenticates the `.bin` envelope; the kernel
independently enforces the PE signature before handoff. Environment data cannot
set a command line, install a key, disable signatures, or request legacy kexec.
The exact authenticated payload is handed to the kernel via a sealed descriptor,
so boot does not reopen a mutable flash path after validation.

The current envelope is specified in [FORMAT.md](../FORMAT.md). It uses our own
domain-separated RSA-2048/SHA-256 scheme, not Cisco's package format or algorithms.
`verify` and recovery copy check the release envelope. PE acceptance and kernel
format/architecture checks happen at boot. A release-signed but incompatible
payload can therefore pass `verify` and still fail the handoff; the monitor
reports the kernel's refusal. A signed platform/ABI manifest and early
compatibility diagnostics remain work for the OS image format.

The initial keys are development keys. Firmware signing and release signing
use separate private keys, retained in the build volume. Firmware enrollment is
performed only in disposable OVMF variables by the tests. The public release
key is compiled into the monitor. No checksum file on writable media acts as a
trust root. The [release key workflow](release-key-management.md) adds public-only
builds, offline signing, authenticated firmware-key rotation and signer revocation.
Per-release antirollback counters, TPM attestation, unattended production
provisioning and self-updating monitor slots are not implemented.

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
retain another working image and external recovery media. This implementation
does not claim a tested A/B power-failure protocol.

### Network recovery

Set `ETHER_PORT`, `IP_ADDRESS`, `IP_SUBNET_MASK`, and, when needed,
`DEFAULT_GATEWAY`. Set `TFTP_SERVER` and `TFTP_FILE` for the short `tftp:` form,
or supply an IPv4 URL. Use ping to diagnose reachability, then boot or copy.
There is no dependency on DHCP, DNS, a cloud service, a management web server,
or the appliance's container network.

TFTP is only transport. Downloaded bytes remain untrusted until signature
verification succeeds. Downloads and recovery copies have a 512 MiB cap and
network transfers have a 120-second deadline. Local verification uses the same
512 MiB ceiling. These are implementation limits, not Cisco platform limits.

### Missing or damaged saved environment

Two environment files alternate generations. Each contains a version, a
generation, a SHA-256 checksum of its body, and a strictly validated set of
values. A malformed slot is ignored; the newest valid slot wins. If neither
is usable, compiled defaults select `bootflash:system.bin` with an interruption
window. The checksum detects corruption, not malicious edits. None of these
values can disable image authentication. The explicit 0x2142 recovery setting
does bypass OS startup credentials for the physical operator, as described below.

These are ROMMON settings, separate from the JSON OS `startup-config`.
They live on bootflash so that array failure cannot prevent recovery.

## Hardware scope and intentional differences

Built-in Ethernet drivers: virtio-net, Intel e1000/e1000e/igb/igc and Realtek
r8169 with its PHY driver. Storage: virtio block, AHCI/SATA, NVMe; USB
xHCI/EHCI/OHCI/UHCI, bulk mass storage and UAS. No loadable modules or graphics
console are included. Driver inclusion is not proof that every physical model
works: hardware variants needing external firmware have not been qualified.
The automated recovery suite targets virtio-net, e1000 and QEMU xHCI mass
storage. Physical hardware qualification is a later milestone.

FractalCR fixes the console at 115200 8N1. The accepted register values select
boot modes only; they do not emulate Cisco baud-rate bits. `0x2142` now supplies
a fixed recovery flag to the authenticated OS. It ignores startup configuration
without deleting it, disables initial remote access, and permits physical-console
credential repair. Restore 0x2102 for normal authenticated startup. See the
[implemented recovery procedure](https://github.com/highenergymagic/fractalcr/blob/main/os/docs/aaa-and-management.md).
The monitor does not support expanded packages, arbitrary environment variables,
an interactive register questionnaire, IOS routing commands, debugger memory
writes, USB Ethernet, exFAT/NTFS, IPv6 or DHCP.

Boot-chain tests retain a small signed witness payload. The separate OE system
image now contains Docker, Kata, array configuration and the appliance CLI; its
runtime qualification is recorded separately from the ROMMON witness tests.

## Evidence to retain with every build

Keep image SHA-256 values, the resolved kernel configuration, serial transcripts,
and machine-readable test outcomes together. The suite must show both successful
recovery and explicit refusal: unsigned ROMMON, disabled Secure Boot, tampered
system image, an unsigned inner kernel, malformed saved configuration, failed
replacement, and exhausted boot alternatives. A timeout alone is never evidence
that authentication worked. See [the test harness](../tests/qemu-recovery.py)
and `out/boot/result-*.json` after building.
