#!/usr/bin/env python3
"""Real Secure Boot recovery tests: USB, two NIC models, persistence, failure.

Only disposable images in /work/artifacts are written. QEMU user networking
serves the fixture directory over TFTP; nothing is exposed on a host port.
"""
import argparse
import hashlib
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
import pexpect
from qemu_process import finish_qemu, stop_qemu

p = argparse.ArgumentParser()
p.add_argument("--case", choices=["usb-persist", "usb-boot", "usb-hotplug", "tftp-virtio", "tftp-e1000", "break", "env-fallback"], required=True)
a = p.parse_args()
art = Path("/work/artifacts")
case = a.case
prompt = r"rommon \d+ > "
disk = art / f"flash-{case}.img"
usb = art / f"usb-{case}.img"
variables = art / f"OVMF_VARS-{case}.fd"
qmp_path = art / f"qmp-{case}.sock"
qmp_path.unlink(missing_ok=True)
shutil.copy(art / "OVMF_VARS-trusted.fd", variables)
for path in [disk, usb]:
    make = ["python3", "boot/tools/make-flash.py", "--system", str(art / "system.bin"), "--output", str(path)]
    if path == disk:
        make += ["--efi", str(art / "rommon.efi")]
    subprocess.run(make, check=True)
bad = bytearray((art / "system.bin").read_bytes())
bad[len(bad)//2] ^= 1
(art / "recovery-tampered.bin").write_bytes(bad)
log = (art / f"qemu-{case}.log").open("w")
result = {"case": case, "passed": False,
          "efi_sha256": hashlib.sha256((art / "rommon.efi").read_bytes()).hexdigest(),
          "system_sha256": hashlib.sha256((art / "system.bin").read_bytes()).hexdigest(),
          "checks": []}
child = None

def spawn(interrupt=True):
    args = [
        "-machine", "q35,smm=on", "-accel", "tcg", "-cpu", "max", "-m", "512",
        "-global", "driver=cfi.pflash01,property=secure,value=on",
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
        "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
        "-drive", f"if=none,id=flash,format=raw,file={disk}",
        "-device", "virtio-blk-pci,drive=flash,bootindex=0",
        "-device", "qemu-xhci,id=xhci",
        "-drive", f"if=none,id=recovery,format=raw,file={usb},readonly=on",
        "-display", "none", "-no-reboot",
    ]
    if case == "usb-hotplug":
        args += ["-qmp", f"unix:{qmp_path},server=on,wait=off"]
    else:
        args += ["-device", "usb-storage,drive=recovery"]
    if case == "break":
        args += ["-serial", "mon:stdio"]
    else:
        args += ["-chardev", "stdio,id=serial0,signal=off", "-serial", "chardev:serial0", "-monitor", "none"]
    if case.startswith("tftp"):
        model = "e1000" if case.endswith("e1000") else "virtio-net-pci"
        args += ["-netdev", f"user,id=recovery-net,tftp={art}",
                 "-device", f"{model},netdev=recovery-net"]
    else:
        args += ["-net", "none"]
    c = pexpect.spawn("qemu-system-x86_64", args, encoding="utf-8", codec_errors="replace", timeout=100)
    c.logfile_read = log
    if interrupt:
        c.expect("Autoboot in 5 seconds")
        c.send("\x01b" if case == "break" else "\x03")
        c.expect("Autoboot interrupted")
        c.expect(prompt)
    return c

def cmd(text, expected=None):
    child.sendline(text)
    if expected:
        child.expect_exact(expected)
    child.expect(prompt)

def check(name):
    result["checks"].append(name)
    print(f"PASS {case}: {name}", flush=True)

try:
    child = spawn()
    if case == "usb-hotplug":
        child.sendline("dev")
        child.expect(prompt)
        assert "usb0" not in child.before
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as qmp:
            qmp.settimeout(5)
            qmp.connect(str(qmp_path))
            stream = qmp.makefile("rwb")
            assert "QMP" in json.loads(stream.readline())
            def execute(command, arguments=None):
                request = {"execute": command}
                if arguments is not None:
                    request["arguments"] = arguments
                stream.write((json.dumps(request) + "\n").encode())
                stream.flush()
                while True:
                    reply = json.loads(stream.readline())
                    if "error" in reply:
                        raise AssertionError(reply)
                    if "return" in reply:
                        return reply["return"]
            execute("qmp_capabilities")
            execute("device_add", {"driver": "usb-storage", "drive": "recovery",
                                   "id": "hotplug-usb", "bus": "xhci.0"})
            stream.close()
        time.sleep(2)
    cmd("dev", "usb0")
    cmd("dir usb0:", "system.bin")
    cmd("verify usb0:tampered.bin", "% AUTHENTICATION FAILED")
    check("USB mass storage enumerated; tampered USB image rejected")
    if case == "break":
        check("UART BREAK interrupted autoboot")
        cmd("show services", "recovery-console")
        cmd("service logging restart")
        cmd("service recovery-storage stop")
        time.sleep(1)
        cmd("show services", "recovery-storage    stopped")
        cmd("service recovery-storage start")
        time.sleep(2)
        cmd("dev", "usb0")
        cmd("service recovery-storage restart")
        time.sleep(2)
        cmd("dev", "usb0")
        child.sendline("service recovery-console restart")
        child.expect("Type help or \\? for recovery commands.")
        child.expect(prompt)
        cmd("dev", "usb0")
        check("recovery storage stop/start/restart preserves console; console restart does not repeat autoboot")
        cmd("confreg 0x2142", "Configuration register changed")
        cmd("confreg 0x2102", "Configuration register changed")
        cmd("BOOT=bootflash:missing.bin;bootflash:tampered.bin")
        cmd("boot", "% Boot sequence failed; remaining in ROM Monitor.")
        check("exhausted boot list returned to monitor without reboot")
    elif case in ("usb-boot", "usb-hotplug"):
        child.sendline("boot usb0:system.bin")
        child.expect("FRACTALCR_SYSTEM_BOOT_OK")
        check("signed USB image booted through kexec")
    elif case in ("usb-persist", "env-fallback"):
        cmd("copy usb0:system.bin bootflash:recovered.bin", "Copy complete:")
        child.sendline("copy usb0:tampered.bin bootflash:recovered.bin")
        child.expect_exact("Destination exists. Replace it? [y/N]: ")
        child.sendline("y")
        child.expect("AUTHENTICATION FAILED")
        child.expect(prompt)
        cmd("verify bootflash:recovered.bin", "Image signature verified.")
        check("verified copy installed; rejected replacement preserved original")
        cmd("BOOT=bootflash:missing.bin;bootflash:recovered.bin")
        cmd("sync", "Monitor environment saved.")
        cmd("confreg 0x0")
        cmd("sync", "Monitor environment saved.")
        child.sendline("reset")
        child.expect(pexpect.EOF)
        stop_qemu(child)
        child = spawn(interrupt=False)
        child.expect("Environment generation 2 loaded.")
        child.expect(prompt)
        check("saved manual boot setting survived reboot")
        cmd("confreg 0x2102")
        cmd("sync", "Monitor environment saved.")
        if case == "env-fallback":
            stop_qemu(child)
            # Corrupt the newest slot offline, leaving previous valid slot.
            corrupt = art / "corrupt-env.txt"
            corrupt.write_text("broken environment\n")
            subprocess.run(["mcopy", "-o", "-i", f"{disk}@@1048576", str(corrupt), "::/rommon.env1"], check=True)
            child = spawn(interrupt=False)
            child.expect("Invalid saved environment slot")
            child.expect("Environment generation 2 loaded.")
            child.expect(prompt)
            check("corrupt newest environment recovered prior generation")
            cmd("confreg 0x2102")
            cmd("sync", "Monitor environment saved.")
        child.sendline("reset")
        child.expect(pexpect.EOF)
        stop_qemu(child)
        child = spawn(interrupt=False)
        child.expect("Loading bootflash:missing.bin")
        child.expect("Loading bootflash:recovered.bin")
        child.expect("FRACTALCR_SYSTEM_BOOT_OK")
        check("saved ordered BOOT skipped missing image and automatically booted recovered image")
    else:
        for text in ["IP_ADDRESS=10.0.2.15", "IP_SUBNET_MASK=255.255.255.0",
                     "DEFAULT_GATEWAY=10.0.2.2", "TFTP_SERVER=10.0.2.2",
                     "TFTP_FILE=recovery-tampered.bin"]:
            cmd(text)
        cmd("ping 10.0.2.2", "Success rate is 100 percent (3/3)")
        cmd("verify tftp://10.0.2.2/not-present.bin", "TFTP reception failed.")
        cmd("BOOT=tftp://10.0.2.99/system.bin;bootflash:system.bin")
        child.sendline("boot")
        child.expect("Receiving system.bin from 10.0.2.99")
        child.sendcontrol("c")
        child.expect_exact("% Boot sequence failed; remaining in ROM Monitor.", timeout=10)
        # Cancellation must stop the whole BOOT list, not try the valid local
        # fallback. Check the actual native TFTP error and absence of handoff.
        assert "% TFTP reception failed." in child.before, child.before
        assert "Loading bootflash:system.bin" not in child.before, child.before
        assert "Booting authenticated system image" not in child.before, child.before
        child.expect(prompt)
        check("missing TFTP file failed; Ctrl-C aborted the entire ordered boot sequence")
        cmd("boot tftp:", "AUTHENTICATION FAILED")
        check("network configured; ping worked; tampered TFTP image rejected")
        cmd("copy tftp://10.0.2.2/system.bin bootflash:network.bin", "Copy complete:")
        cmd("verify bootflash:network.bin", "Image signature verified.")
        cmd("TFTP_FILE=system.bin")
        child.sendline("boot tftp:")
        child.expect("FRACTALCR_SYSTEM_BOOT_OK")
        check("TFTP copy and direct TFTP boot both succeeded")
    result["passed"] = True
finally:
    finish_qemu(child, result, art / f"result-{case}.json", log)
print(json.dumps(result))
