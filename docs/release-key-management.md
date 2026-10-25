# Release key custody and rotation

Development and release builds use separate trust and Docker volumes. Release
builders receive public certificates and keys only. The offline signing station
holds encrypted PKCS#8 private keys; decrypted PE-signing material is passed to
the signer through a sealed memory file descriptor and is never written to the
builder volume. This procedure is for operators who manage their own signing
identity. Published development images use the development-key workflow instead.

## Prerequisites

Use a recursive integration checkout and a Linux workstation with Python
cryptography, OpenSSL, sbsigntool, efitools, and faketime. Prepare a separate
offline signing station and encrypted backup storage.

## Create and back up the signing keys

Create the vault on the offline station, outside the source checkout:

```
python3 tools/release.py init --vault /offline/fractal-2026 --name release-2026 --minimum-security-epoch 1
python3 tools/release.py verify-vault --vault /offline/fractal-2026
```

The passphrase is prompted without echo. Automation can supply a mode-0600
`--password-file`; passphrases are never command-line values. Keep the passphrase
separate from the encrypted vault. Keep two independently stored encrypted
backups and verify restoration with `verify-vault` before retiring any copy.
Only the `public/` subdirectory goes to the build workstation. Record and compare
the firmware-certificate and release-key fingerprints through a separate trusted
channel when transferring the bundle.

## Build with public trust material

Run public-only builds on your Linux SSH host (replace `YOUR_BUILD_HOST` and the
example `C:\release-public` path with your public trust directory):

```powershell
.\tools\remote-build.ps1 -BuildHost YOUR_BUILD_HOST -Stage boot -BuildAction Build -SigningMode external -PublicTrustDirectory C:\release-public
.\tools\remote-build.ps1 -BuildHost YOUR_BUILD_HOST -Stage boot -Action Export -SigningMode external -PublicTrustDirectory C:\release-public
.\tools\remote-build.ps1 -BuildHost YOUR_BUILD_HOST -Stage os -BuildAction Build -SigningMode external -PublicTrustDirectory C:\release-public
.\tools\remote-build.ps1 -BuildHost YOUR_BUILD_HOST -Stage os -Action Export -SigningMode external -PublicTrustDirectory C:\release-public
```

Wait for each remote build to finish before exporting or starting the next.
Volumes are named from the public trust manifest hash and are separate from
development volumes. External mode refuses private keys in its trust directory,
and development mode refuses to generate keys in a production trust volume.
`signing-request.json` explicitly labels outputs as pending offline signatures.
An unsigned build is not a bootable release or a completed qualification.

## Sign the reviewed artifacts

Review the build inputs and qualification evidence. Transfer the unsigned
`rommon.unsigned.efi` and `system.unsigned.efi` plus their reviewed SHA-256 values
to the offline station. Sign them separately:

```
python3 tools/release.py sign --vault /offline/fractal-2026 --kind rommon --input rommon.unsigned.efi --expected-sha256 REVIEWED_HASH --output rommon.efi
python3 tools/release.py sign --vault /offline/fractal-2026 --kind system --input system.unsigned.efi --expected-sha256 REVIEWED_HASH --output system.bin
```

The signer checks the input hash, verifies that PE signing preserved the reviewed
payload, verifies the PE signature, and writes an audit entry and receipt. System
signing also adds the independent release envelope required by ROMMON. Outputs
are created exclusively: existing signed artifacts are not overwritten. Verify
the returned artifacts using the following procedure.

## Verify and qualify the signed images

On the build/release workstation, use only public trust:

```
python3 tools/release.py verify --public /release-public --kind rommon --input rommon.efi --unsigned rommon.unsigned.efi
python3 tools/release.py verify --public /release-public --kind system --input system.bin --unsigned system.unsigned.efi
```

Generate VM flash with `boot/tools/make-flash.py` and qualify these exact signed
hashes before distribution. Release qualification is separate from the ordinary
development-image reports; signing with a different key changes the artifacts.

## Rotation and revocation

The dedicated appliance firmware profile enrolls the firmware certificate as
PK, KEK and db. This is the same ownership layout used by the QEMU fixtures.
Do not apply its replacement updates to a general-purpose machine with unrelated
platform-owner or vendor keys.

Generate a new vault, build and sign the new ROMMON/system with its public trust,
and retain new-key recovery media. On the old offline signing station:

```
python3 tools/release.py rotation --vault /offline/fractal-2026 --new-public /offline/fractal-next/public --output /offline/rotation-package
```

The package contains authenticated UEFI variable updates and an ordered manifest:

1. Append the new firmware certificate to db and KEK.
2. Install and boot the new ROMMON/system. Verify the new identity and recovery
   media before proceeding.
3. Add the old firmware certificate to dbx, replace KEK with the new owner, then
   replace PK. Confirm that new-key recovery boots and old-key ROMMON is refused.

These are firmware enrollment operations, not changes to a writable JSON trust
file inside the appliance. The generator packages updates; it never applies
them to the host. Use the target firmware's authenticated-variable enrollment
facility. The appliance profile requires those facilities to preserve update
attributes and timestamps. Follow `rotation.json`; do not reorder or partially
substitute its files. Retain the signed package and audit log with the release.

Rotating only the outer release key does not retire the old signed ROMMON.
Revoking its firmware signer in dbx closes that replay path; retiring the old
KEK and PK also removes its authority to update firmware trust. A same-key older
system release is acceptable only when its signed v2 security epoch meets the
ROMMON's compiled minimum. To retire vulnerable releases, advance the minimum
when creating a new vault, rebuild ROMMON, sign the fixed system at that epoch
(`sign --security-epoch N`), qualify recovery, and complete firmware revocation.
The signer defaults to the vault's floor and records the epoch in its receipt.
Rotation refuses to lower the floor. Releases in the same epoch remain
interchangeable; no writable automatic boot counter is claimed.

Legacy v1 trust profiles may be used only as the old identity for `rotation`.
They cannot sign or build new v2 releases. Generate a new v2 vault rather than
editing the old trust manifest to conceal its provenance. Until old ROMMON
signers are revoked, older signed ROMMONs can still boot older system images.

## Validate the signing and rotation tools

The [UEFI Secure Boot specification](https://uefi.org/specs/UEFI/2.10/32_Secure_Boot_and_Driver_Signing.html)
defines authenticated PK/KEK/db/dbx updates. `boot/tests/test-release.py` tests
encrypted custody, public-only preparation, exact-payload signing, signature
failures, real ROMMON key rejection and the update signatures. With
`--qemu-rotation` and gnu-efi installed, it applies the updates in disposable
OVMF and checks retirement of the old firmware signer.

On a Linux Docker host with passwordless `doas`, `bash tools/test-release.sh /path/to/new-evidence-directory` builds
the test helper image and runs this qualification without network access.
Disposable encrypted vaults and passphrases stay in tmpfs and disappear when
the container exits. The evidence directory contains only public trust, signed
test artifacts, firmware fixtures, transcripts and results. This does not
generate or enroll deployment keys.

`bash tools/test-public-release.sh /path/to/new-evidence-directory` additionally
tests the build/sign boundary end to end. It seeds a separate builder with public
sources and compiler caches, gives it only a new public trust bundle, builds a
new ROMMON and witness kernel, and sends their unsigned artifacts to a separate
offline signing container. OVMF then verifies the new ROMMON, whose embedded
release key and kernel trust certificate authenticate and boot that witness.
The signing container's private tmpfs is never mounted into the builder and is
destroyed at exit. This is boot-chain qualification; the actual OE release image
still requires the normal OS acceptance suite under its deployment keys.
