#!/bin/bash
set -euo pipefail
export LC_ALL=C
export SOURCE_DATE_EPOCH=1792454400
export KBUILD_BUILD_TIMESTAMP="Tue Oct 20 00:00:00 UTC 2026"
export KBUILD_BUILD_USER=fractalcr KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export FORCE_UNSAFE_CONFIGURE=1
JOBS=${JOBS:-2}
cd /work
mkdir -p build sources downloads artifacts
rsync -a --delete --exclude=.git --exclude=__pycache__ /src/boot/ boot/
rsync -a --delete --exclude=.git --exclude=__pycache__ /src/service/ boot/rommon/service/
chmod +x boot/buildroot/board/prune-rommon.sh
bash boot/tools/fetch.sh
bash boot/tools/make-dev-keys.sh

make -C sources/buildroot O=/work/build/rommon-root \
    BR2_EXTERNAL=/work/boot/buildroot fractalcr_rommon_defconfig
# Local source and the embedded public key may have changed between builds.
make -C sources/buildroot O=/work/build/rommon-root rommon-rebuild
make -C sources/buildroot O=/work/build/rommon-root busybox-reconfigure
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
    sources/linux/scripts/config --file "$output/.config" \
        --set-str INITRAMFS_SOURCE "$initramfs" \
        --set-str SYSTEM_TRUSTED_KEYS /work/build/keys/firmware.crt \
        --set-str LOCALVERSION "-fractalcr-$name"
    make -C sources/linux O="$output" ARCH=x86_64 CROSS_COMPILE="$toolchain" olddefconfig
    for option in EFI_STUB KEXEC_FILE KEXEC_SIG_FORCE KEXEC_BZIMAGE_VERIFY_SIG \
        SYSTEM_TRUSTED_KEYRING LOCK_DOWN_KERNEL_FORCE_INTEGRITY; do
        grep -qx "CONFIG_${option}=y" "$output/.config" || {
            echo "Required kernel option missing: $option" >&2; exit 1;
        }
    done
    for option in KEXEC VT VGA_CONSOLE DRM FB; do
        if grep -qx "CONFIG_${option}=y" "$output/.config"; then
            echo "Forbidden kernel option enabled: $option" >&2; exit 1
        fi
    done
    make -C sources/linux O="$output" ARCH=x86_64 CROSS_COMPILE="$toolchain" -j"$JOBS" bzImage
}
build_kernel rommon /work/build/rommon-root/images/rootfs.cpio
build_kernel witness /work/build/witness.list

bash boot/tools/package.sh
