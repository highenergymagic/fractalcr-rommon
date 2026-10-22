#!/usr/bin/env python3
"""Boot exported artifacts with the host's QEMU, without opening a GUI."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument("--artifacts", type=Path,
                    default=Path(__file__).resolve().parents[2] / "out" / "carbon" / "boot")
artifacts = parser.parse_args().artifacts.resolve()
firmware = artifacts / "OVMF_CODE_4M.secboot.fd"
variables = artifacts / "OVMF_VARS-native-test.fd"
shutil.copyfile(artifacts / "OVMF_VARS-trusted.fd", variables)
qemu = shutil.which("qemu-system-x86_64")
if not qemu:
    raise SystemExit("qemu-system-x86_64 is not on PATH")
cmd = [
    qemu, "-machine", "q35,smm=on", "-accel", "tcg", "-cpu", "max", "-m", "512",
    "-global", "driver=cfi.pflash01,property=secure,value=on",
    "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={firmware}",
    "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
    "-drive", f"if=virtio,format=raw,snapshot=on,file={artifacts / 'flash-trusted.img'}",
    "-display", "none", "-serial", "stdio", "-monitor", "none", "-net", "none", "-no-reboot",
]
child = subprocess.Popen(
    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
messages = queue.Queue()
def read():
    while data := child.stdout.read1(4096):
        messages.put(data)
    messages.put(None)
thread = threading.Thread(target=read, daemon=True)
thread.start()
text = ""
sent = False
result = {"case": "native-host", "passed": False, "qemu": qemu}
transcript = (artifacts / "qemu-native.log").open("w", encoding="utf-8")
try:
    end = time.monotonic() + 120
    while time.monotonic() < end:
        try:
            data = messages.get(timeout=1)
        except queue.Empty:
            continue
        if data is None:
            raise RuntimeError("QEMU exited before the boot witness")
        chunk = data.decode("utf-8", errors="replace")
        text += chunk
        transcript.write(chunk)
        transcript.flush()
        if not sent and "Autoboot in 5 seconds" in text:
            assert "Secure Boot: enabled" in text
            # Let the default BOOT entry exercise automatic handoff.
            sent = True
        if "FRACTALCR_SYSTEM_BOOT_OK" in text and "Embedded initramfs PID: 1" in text:
            result["passed"] = True
            break
    assert result["passed"], "Boot timed out; see qemu-native.log"
finally:
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    thread.join(timeout=2)
    transcript.close()
    for name in ("rommon.efi", "system.bin"):
        result[name + "_sha256"] = hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
    (artifacts / "result-native.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
