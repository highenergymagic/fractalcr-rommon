#!/usr/bin/env python3
"""Make a disposable MBR/ESP disk image; never opens physical disks."""
import argparse
import shutil
import struct
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--efi", type=Path, help="Omit for recovery data media without an EFI boot image")
p.add_argument("--system", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--size-mib", type=int, default=128)
p.add_argument("--no-tampered-fixture", action="store_true", help="omit the negative-test image on ordinary OS media")
a = p.parse_args()
if not 128 <= a.size_mib <= 4096:
    p.error("--size-mib must be between 128 and 4096")
# A 128 MiB FAT ESP in an MBR is supported by OVMF and the ROMMON kernel.
sectors = a.size_mib * 2048
offset = 2048
part = a.output.with_suffix(".fat")
with part.open("wb") as f:
    f.truncate((sectors - offset) * 512)
subprocess.run(["mkfs.vfat", "-F", "32", "-n", "FRFLASH", str(part)], check=True)
if a.efi:
    subprocess.run(["mmd", "-i", str(part), "::/EFI", "::/EFI/BOOT"], check=True)
    subprocess.run(["mcopy", "-i", str(part), str(a.efi), "::/EFI/BOOT/BOOTX64.EFI"], check=True)
subprocess.run(["mcopy", "-i", str(part), str(a.system), "::/system.bin"], check=True)
if not a.no_tampered_fixture:
    bad = a.output.with_suffix(".bad")
    data = bytearray(a.system.read_bytes())
    data[len(data) // 2] ^= 1
    bad.write_bytes(data)
    subprocess.run(["mcopy", "-i", str(part), str(bad), "::/tampered.bin"], check=True)
    bad.unlink()
mbr = bytearray(512)
mbr[446:462] = struct.pack("<B3sB3sII", 0x80, b"\0\2\0", 0xEF, b"\xfe\xff\xff", offset, sectors - offset)
mbr[510:512] = b"\x55\xaa"
with a.output.open("wb") as out:
    out.write(mbr)
    out.seek(offset * 512)
    with part.open("rb") as src:
        shutil.copyfileobj(src, out)
part.unlink()
