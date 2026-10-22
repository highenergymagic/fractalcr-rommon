#!/usr/bin/env python3
"""Negative verification tests against the real ROMMON C verifier."""
import subprocess
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path("/work")
DOMAIN = b"FractalCR system image v1\n"
MAGIC = b"FCRBIN01"
key = serialization.load_pem_private_key((ROOT / "build/keys/release.key").read_bytes(), None)
binary = ROOT / "build/rommon-verifier-test"
subprocess.run([
    "gcc", "-Os", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
    "-DROMMON_HOST_TEST", "-I/work/build/keys", "/work/boot/rommon/rommon.c", "/work/boot/rommon/monitor.c",
    "-I/src/service", "/src/service/control.c", "/src/service/logging.c", "/src/service/supervisor.c",
    "-o", str(binary), "-lcrypto",
], check=True)

def signed(payload, signing_key=key):
    return payload + signing_key.sign(DOMAIN + payload, padding.PKCS1v15(), hashes.SHA256()) + MAGIC

payload = b"MZ" + bytes(range(256)) * 1024
valid = signed(payload)
other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
cases = {
    "valid": (valid, True),
    "payload-tampered": (bytes([valid[0] ^ 1]) + valid[1:], False),
    "signature-tampered": (valid[:-264] + bytes([valid[-264] ^ 1]) + valid[-263:], False),
    "wrong-key": (signed(payload, other), False),
    "truncated": (valid[:-1], False),
    "empty": (b"", False),
    "magic-tampered": (valid[:-1] + b"2", False),
    "appended-garbage": (valid + b"x", False),
    "unsigned": (payload, False),
    "signature-from-other-payload": (b"x" + valid[1:], False),
}
with tempfile.TemporaryDirectory() as directory:
    for name, (data, expected) in cases.items():
        path = Path(directory) / name
        path.write_bytes(data)
        result = subprocess.run([str(binary), "--verify", str(path)], capture_output=True, timeout=10)
        assert (result.returncode == 0) == expected, (name, result.stdout, result.stderr)
        print(f"PASS {name}")
    link = Path(directory) / "symlink"
    link.symlink_to(Path(directory) / "valid")
    result = subprocess.run([str(binary), "--verify", str(link)], capture_output=True, timeout=10)
    assert result.returncode != 0
    print("PASS symlink rejected")
print("VERIFIER_TESTS_OK")
