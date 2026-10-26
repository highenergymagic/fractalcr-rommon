#!/bin/bash
set -euo pipefail
export LC_ALL=C
export SOURCE_DATE_EPOCH=1792454400
export KBUILD_BUILD_TIMESTAMP="Tue Oct 20 00:00:00 UTC 2026"
export KBUILD_BUILD_USER=fractalcr KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export FORCE_UNSAFE_CONFIGURE=1
export BR2_DL_DIR=/work/downloads/buildroot-packages
JOBS=${JOBS:-2}
cd /work
mkdir -p build sources downloads artifacts
rsync -ac --delete --exclude=.git --exclude=__pycache__ /src/boot/ boot/
rsync -ac --delete --exclude=.git --exclude=__pycache__ /src/service/ boot/rommon/service/
python3 /src/tools/version-metadata.py --output boot/rommon/service/release.h
chmod +x boot/buildroot/board/prune-rommon.sh
bash boot/tools/fetch.sh
python3 /src/boot/tools/kernel-security.py
kernel_source=/work/build/linux-security-source
bash boot/tools/make-dev-keys.sh

make -C sources/buildroot O=/work/build/rommon-root \
    BR2_EXTERNAL=/work/boot/buildroot fractalcr_rommon_defconfig
# Local source and the embedded public key may have changed between builds.
# Local-package rsync also uses timestamps. Recreate this small package so a
# same-sized source edit in a deterministic snapshot cannot preserve old code.
make -C sources/buildroot O=/work/build/rommon-root rommon-dirclean
make -C sources/buildroot O=/work/build/rommon-root -j"$JOBS"

toolchain=/work/build/rommon-root/host/bin/x86_64-buildroot-linux-musl-
# Resolve Buildroot's actual tuple instead of depending on vendor spelling.
cc=$(find /work/build/rommon-root/host/bin -maxdepth 1 -name '*-gcc' | head -n 1)
test -n "$cc"
toolchain=${cc%gcc}

mkdir -p build/witness-root/dev build/witness-root/proc
"$cc" -Os -static -s boot/witness/init.c -o build/witness-root/init
cat > build/witness.list <<EOF
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
dir /proc 0555 0 0
file /init /work/build/witness-root/init 0755 0 0
EOF

build_kernel() {
    local name=$1 initramfs=$2
    local output=/work/build/kernel-"$name"
    mkdir -p "$output"
    cp "boot/kernel/$name.config" "$output/.config"
    "$kernel_source/scripts/config" --file "$output/.config" \
        --set-str INITRAMFS_SOURCE "$initramfs" \
        --set-str SYSTEM_TRUSTED_KEYS /work/build/keys/firmware.crt \
        --set-str LOCALVERSION "-security2-fractalcr-$name"
    make -C "$kernel_source" O="$output" ARCH=x86_64 CROSS_COMPILE="$toolchain" olddefconfig
    for option in EFI_STUB KEXEC_FILE KEXEC_SIG_FORCE KEXEC_BZIMAGE_VERIFY_SIG \
        SYSTEM_TRUSTED_KEYRING LOCK_DOWN_KERNEL_FORCE_INTEGRITY; do
        grep -qx "CONFIG_${option}=y" "$output/.config" || {
            echo "Required kernel option missing: $option" >&2; exit 1;
        }
    done
    if [[ "$name" == rommon ]]; then
        for option in SLAB_FREELIST_RANDOM SLAB_FREELIST_HARDENED HARDENED_USERCOPY \
            FORTIFY_SOURCE INIT_ON_ALLOC_DEFAULT_ON LIST_HARDENED SECURITY_DMESG_RESTRICT \
            TIGON3 BNX2 BNX2X BNXT IXGBE I40E MLX4_EN MLX5_CORE MLX5_CORE_EN ENA_ETHERNET VMXNET3 \
            MEGARAID_SAS SCSI_MPT3SAS SCSI_MPI3MR FUSION_SAS SCSI_AACRAID SCSI_HPSA SCSI_SMARTPQI SCSI_ISCI SCSI_VIRTIO; do
            grep -qx "CONFIG_${option}=y" "$output/.config" || {
                echo "Required ROMMON kernel option missing: $option" >&2; exit 1;
            }
        done
    fi
    for option in KEXEC VT VGA_CONSOLE DRM FB; do
        if grep -qx "CONFIG_${option}=y" "$output/.config"; then
            echo "Forbidden kernel option enabled: $option" >&2; exit 1
        fi
    done
    make -C "$kernel_source" O="$output" ARCH=x86_64 CROSS_COMPILE="$toolchain" -j"$JOBS" bzImage
}
build_kernel rommon /work/build/rommon-root/images/rootfs.cpio
build_kernel witness /work/build/witness.list

bash boot/tools/package.sh
