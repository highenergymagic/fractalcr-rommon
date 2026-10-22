#!/bin/bash
set -euo pipefail
cd /work
mkdir -p artifacts
# The ROMMON kernel requires this PE signature at kexec_file_load.
if [[ "${FCR_SIGNING_MODE:-development}" != external ]]; then
bash /src/boot/tools/sign-pe.sh --key build/keys/firmware.key --cert build/keys/firmware.crt \
    --output artifacts/witness.efi build/kernel-witness/arch/x86/boot/bzImage
python3 boot/tools/sign-image.py --key build/keys/release.key \
    --kernel artifacts/witness.efi --output artifacts/system.bin
else
    cp build/kernel-witness/arch/x86/boot/bzImage artifacts/witness.unsigned.efi
fi

# The .linux section already contains initramfs. There is no external initrd.
printf 'console=ttyS0,115200 rdinit=/init panic=-1 quiet loglevel=0\0' > build/rommon.cmdline
printf 'ID=fractalcr-rommon\nNAME="FractalCR ROM Monitor"\nVERSION_ID=0.5\n' > build/rommon.osrel
objcopy \
    --add-section .osrel=build/rommon.osrel --change-section-vma .osrel=0x20000 \
    --add-section .cmdline=build/rommon.cmdline --change-section-vma .cmdline=0x30000 \
    --add-section .linux=build/kernel-rommon/arch/x86/boot/bzImage --change-section-vma .linux=0x2000000 \
    /usr/lib/systemd/boot/efi/linuxx64.efi.stub artifacts/rommon.unsigned.efi
python3 /src/boot/tools/normalize-pe.py artifacts/rommon.unsigned.efi
if [[ "${FCR_SIGNING_MODE:-development}" != external ]]; then
bash /src/boot/tools/sign-pe.sh --key build/keys/firmware.key --cert build/keys/firmware.crt \
    --output artifacts/rommon.efi artifacts/rommon.unsigned.efi
fi
cp build/kernel-rommon/.config artifacts/rommon-kernel.config
cp build/kernel-witness/.config artifacts/witness-kernel.config
cp build/keys/firmware.crt build/keys/release.pub artifacts/
if [[ "${FCR_SIGNING_MODE:-development}" == external ]]; then
    cp build/keys/trust.json artifacts/
    python3 /src/tools/signing-request.py --artifacts /work/artifacts --trust /work/build/keys --stage boot
    echo 'BOOT_UNSIGNED_IMAGES_BUILT: offline signatures required before boot or distribution'
    exit 0
fi
(cd artifacts && sha256sum rommon.efi system.bin > SHA256SUMS)
echo "BOOT_IMAGES_BUILT"
