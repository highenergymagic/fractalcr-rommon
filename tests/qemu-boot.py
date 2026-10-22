#!/usr/bin/env python3
"""Exercise the real UEFI → ROMMON → system path through a serial terminal."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
import pexpect
from qemu_process import finish_qemu

p = argparse.ArgumentParser()
p.add_argument("--case", choices=["trusted", "unsigned-rommon", "insecure-firmware", "unsigned-inner-kernel", "optional-firmware", "optional-unsigned-kernel"], default="trusted")
p.add_argument("--timeout", type=int, default=180)
a = p.parse_args()
root = Path("/work")
artifacts = root / "artifacts"
case = a.case
efi = artifacts / ("rommon.unsigned.efi" if case == "unsigned-rommon" else "rommon.efi")
disk = artifacts / f"flash-{case}.img"
system = artifacts / "system.bin"
if case in ("unsigned-inner-kernel", "optional-unsigned-kernel"):
    system = artifacts / "unsigned-inner-kernel.bin"
    subprocess.run([
        "python3", "boot/tools/sign-image.py", "--key", "/work/build/keys/release.key",
        "--kernel", "/work/build/kernel-witness/arch/x86/boot/bzImage",
        "--output", str(system),
    ], check=True)
subprocess.run(["python3", "boot/tools/make-flash.py", "--efi", str(efi),
                "--system", str(system), "--output", str(disk)], check=True)
variables = artifacts / f"OVMF_VARS-{case}.fd"
if case in ("insecure-firmware", "optional-firmware", "optional-unsigned-kernel"):
    shutil.copy("/usr/share/OVMF/OVMF_VARS_4M.fd", variables)
else:
    subprocess.run([
        "/opt/firmware-tools/bin/virt-fw-vars",
        "--input", "/usr/share/OVMF/OVMF_VARS_4M.fd",
        "--output", str(variables),
        "--set-pk", "748e6cc5-19b4-486b-9e22-4e06d330890a", "/work/build/keys/firmware.crt",
        "--add-kek", "748e6cc5-19b4-486b-9e22-4e06d330890a", "/work/build/keys/firmware.crt",
        "--add-db", "748e6cc5-19b4-486b-9e22-4e06d330890a", "/work/build/keys/firmware.crt",
        "--secure-boot",
    ], check=True)

args = [
    "-machine", "q35,smm=on", "-accel", "tcg", "-cpu", "max", "-m", "512",
    "-global", "driver=cfi.pflash01,property=secure,value=on",
    "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
    "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
    "-drive", f"if=virtio,format=raw,file={disk}",
    "-display", "none", "-chardev", "stdio,id=serial0,signal=off",
    "-serial", "chardev:serial0", "-monitor", "none",
    "-net", "none", "-no-reboot",
]
log = artifacts / f"qemu-{case}.log"
result = {
    "case": case, "passed": False,
    "efi_sha256": hashlib.sha256(efi.read_bytes()).hexdigest(),
    "system_sha256": hashlib.sha256(system.read_bytes()).hexdigest(),
}
with log.open("w") as transcript:
    child = pexpect.spawn("qemu-system-x86_64", args, encoding="utf-8", codec_errors="replace", timeout=a.timeout)
    child.logfile_read = transcript
    try:
        if case == "unsigned-rommon":
            # Explicit firmware rejection is required; a generic timeout is not
            # evidence of Secure Boot enforcement.
            child.expect("Access Denied|Security Violation")
            result["evidence"] = "OVMF rejected the unsigned ROMMON EFI image"
        else:
            boot_result = child.expect(["Autoboot in 5 seconds", "Access Denied", "Security Violation", "Kernel panic"])
            assert boot_result == 0, "Firmware or kernel rejected ROMMON; see serial transcript"
            child.sendcontrol("c")
            child.expect(r"rommon \d+ > ")
            child.sendline("show secure-boot")
            child.expect("Secure Boot: " + ("DISABLED" if case in ("insecure-firmware", "optional-firmware", "optional-unsigned-kernel") else "enabled"))
            child.expect(r"rommon \d+ > ")
            if case.startswith("optional-"):
                child.sendline("SECURE_BOOT=optional"); child.expect(r"rommon \d+ > ")
                child.sendline("sync"); child.expect(r"rommon \d+ > ")
                child.sendline("service recovery-console restart")
                child.expect("Firmware policy: optional; image authentication: mandatory")
                child.expect(r"rommon \d+ > ")
            child.sendline("boot flash:tampered.bin")
            child.expect("AUTHENTICATION FAILED: image rejected")
            child.expect(r"rommon \d+ > ")
            child.sendline("boot flash:system.bin")
            child.expect("Image signature verified")
            if case in ("trusted", "optional-firmware"):
                child.expect("FRACTALCR_SYSTEM_BOOT_OK")
                child.expect("Embedded initramfs PID: 1")
                result["evidence"] = "Tampered image rejected; signed kernel booted through kexec; firmware policy " + ("optional, persisted across console restart" if case == "optional-firmware" else "required")
            elif case == "insecure-firmware":
                child.expect("boot policy refuses handoff")
                child.expect(r"rommon \d+ > ")
                result["evidence"] = "ROMMON refused handoff with Secure Boot disabled"
            else:
                # quiet suppresses the kernel's informational signature line;
                # the syscall refusal is explicit and the fixture deliberately
                # has no PE signature, with enforced verification in .config.
                child.expect("kexec_file_load refused image: (No data available|Key was rejected by service|Required key not available)")
                child.expect(r"rommon \d+ > ")
                result["evidence"] = "Trusted outer release signature accepted; unsigned inner kernel rejected by kernel signature policy"
        result["passed"] = True
    finally:
        finish_qemu(child, result, artifacts / f"result-{case}.json")
print(json.dumps(result))
