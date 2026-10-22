#!/usr/bin/env python3
"""Sign one complete PE bzImage, including its built-in initramfs."""
import argparse
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DOMAIN = b"FractalCR system image v1\n"
MAGIC = b"FCRBIN01"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--kernel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    key = serialization.load_pem_private_key(a.key.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        p.error("v1 requires an RSA-2048 release key")
    payload = a.kernel.read_bytes()
    if not payload.startswith(b"MZ") or len(payload) > 512 * 1024 * 1024 - 264:
        p.error("expected a PE bzImage below 512 MiB")
    signature = key.sign(DOMAIN + payload, padding.PKCS1v15(), hashes.SHA256())
    a.output.write_bytes(payload + signature + MAGIC)

if __name__ == "__main__":
    main()
