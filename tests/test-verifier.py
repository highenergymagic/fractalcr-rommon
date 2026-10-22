#!/usr/bin/env python3
"""Negative verification tests against the real ROMMON C verifier."""
import subprocess
import os
import random
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path("/work")
DOMAIN = b"FractalCR system image v2\n"
MAGIC = b"FCRBIN02"
key = serialization.load_pem_private_key((ROOT / "build/keys/release.key").read_bytes(), None)
binary = ROOT / "build/rommon-verifier-test"
compiler = [
    "gcc", "-Os", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
    "-DROMMON_HOST_TEST", "-I/work/build/keys", "/work/boot/rommon/rommon.c", "/work/boot/rommon/monitor.c",
    "-I/src/service", "/src/service/control.c", "/src/service/logging.c", "/src/service/supervisor.c",
    "-o", str(binary), "-lcrypto",
]
if os.environ.get('FCR_SANITIZE') == '1':
    compiler += ['-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
subprocess.run(compiler, check=True)

def signed(payload, signing_key=key, epoch=1):
    body = payload + epoch.to_bytes(8, 'big')
    return body + signing_key.sign(DOMAIN + body, padding.PKCS1v15(), hashes.SHA256()) + MAGIC

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
    "magic-tampered": (valid[:-1] + bytes([valid[-1] ^ 1]), False),
    "appended-garbage": (valid + b"x", False),
    "unsigned": (payload, False),
    "signature-from-other-payload": (b"x" + valid[1:], False),
    "older-security-epoch": (signed(payload, epoch=0), False),
    "newer-security-epoch": (signed(payload, epoch=2), True),
    "epoch-tampered": (valid[:-272] + (2).to_bytes(8, 'big') + valid[-264:], False),
    "legacy-envelope": (payload + key.sign(b'FractalCR system image v1\n'+payload, padding.PKCS1v15(), hashes.SHA256()) + b'FCRBIN01', False),
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
    oversized = Path(directory) / 'oversized'
    with oversized.open('wb') as stream:stream.truncate(512*1024*1024+1)
    result = subprocess.run([str(binary), '--verify', str(oversized)], capture_output=True, timeout=10)
    assert result.returncode != 0
    fifo = Path(directory) / 'fifo'
    os.mkfifo(fifo)
    result = subprocess.run([str(binary), '--verify', str(fifo)], capture_output=True, timeout=10)
    assert result.returncode != 0
    print('PASS oversize and FIFO rejected before reading')
    if os.environ.get('FCR_SANITIZE') == '1':
        rng = random.Random(20261022)
        path = Path(directory) / 'mutation'
        for _ in range(512):
            data = bytearray(valid)
            offset = rng.randrange(len(data))
            data[offset] ^= 1 << rng.randrange(8)
            path.write_bytes(data)
            result = subprocess.run([str(binary), '--verify', str(path)], capture_output=True, timeout=10)
            assert result.returncode > 0 and b'AddressSanitizer' not in result.stderr and b'runtime error:' not in result.stderr, (offset, result.returncode, result.stderr)
        print('PASS 512 deterministic signed-envelope mutations under ASAN/UBSAN')
print("VERIFIER_TESTS_OK")
