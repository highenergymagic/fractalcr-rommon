#!/bin/bash
set -euo pipefail
cd /work
gcc -Wall -Wextra -Werror -I/src/service /src/service/test-graph.c \
    /src/service/graph.c /src/service/supervisor.c -o /tmp/test-graph
/tmp/test-graph | tee artifacts/service-graph-tests.log
/work/build/rommon-root/host/bin/x86_64-linux-gcc -static -Wall -Wextra -Werror \
    -I/src/service /src/service/test-graph.c /src/service/graph.c /src/service/supervisor.c -o /tmp/test-graph-musl
/tmp/test-graph-musl | tee artifacts/service-graph-musl-tests.log
gcc -DFCR_ROMMON -Wall -Wextra -Werror -I/src/service /src/service/test-control.c \
    /src/service/graph.c /src/service/supervisor.c -o /tmp/test-control
/tmp/test-control | tee artifacts/control-tests.log
/work/build/rommon-root/host/bin/x86_64-linux-gcc -static -DFCR_ROMMON -Wall -Wextra -Werror \
    -I/src/service /src/service/test-control.c /src/service/graph.c /src/service/supervisor.c -o /tmp/test-control-musl
/tmp/test-control-musl | tee artifacts/control-musl-tests.log
rsync -a /src/boot/ boot/
python3 boot/tests/test-qemu-process.py
python3 boot/tests/test-verifier.py | tee artifacts/verifier-tests.log
gcc -Os -Wall -Wextra -Werror -I/src/service boot/tests/test-monitor.c \
    /src/service/control.c /src/service/logging.c /src/service/supervisor.c -o build/monitor-input-test -lcrypto
build/monitor-input-test | tee artifacts/monitor-input-tests.log
for option in VIRTIO_NET E1000 E1000E IGB IGC R8169 USB_XHCI_HCD USB_EHCI_HCD \
    USB_OHCI_HCD USB_UHCI_HCD USB_STORAGE USB_UAS BLK_DEV_SD SATA_AHCI BLK_DEV_NVME; do
    grep -qx "CONFIG_${option}=y" artifacts/rommon-kernel.config || {
        echo "Missing recovery driver: $option" >&2; exit 1;
    }
done
test ! -e build/rommon-root/target/bin/sh
grep -qx '#define NUM_APPLETS 3' build/rommon-root/build/busybox-*/include/applet_tables.h
sbverify --cert build/keys/firmware.crt artifacts/rommon.efi
sbverify --cert build/keys/firmware.crt artifacts/witness.efi
python3 boot/tests/qemu-boot.py --case trusted
python3 boot/tests/qemu-boot.py --case unsigned-rommon
python3 boot/tests/qemu-boot.py --case insecure-firmware
python3 boot/tests/qemu-boot.py --case unsigned-inner-kernel
python3 boot/tests/qemu-boot.py --case optional-firmware
python3 boot/tests/qemu-boot.py --case optional-unsigned-kernel
for case in usb-persist usb-boot usb-hotplug tftp-virtio tftp-e1000 break env-fallback; do
    python3 boot/tests/qemu-recovery.py --case "$case"
done
cp /usr/share/OVMF/OVMF_CODE_4M.secboot.fd artifacts/
python3 boot/tools/report.py
