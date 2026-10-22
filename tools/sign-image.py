#!/usr/bin/env python3
"""Sign one complete PE bzImage, including its built-in initramfs."""
import argparse
import json
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DOMAIN = b"FractalCR system image v2\n"
MAGIC = b"FCRBIN02"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--kernel", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--security-epoch", type=int)
    a = p.parse_args()
    key = serialization.load_pem_private_key(a.key.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        p.error("v2 requires an RSA-2048 release key")
    policy = json.loads(Path(__file__).resolve().parents[1].joinpath('image-policy.json').read_text())
    epoch = a.security_epoch if a.security_epoch is not None else policy['security_epoch']
    if type(epoch) is not int or not 1 <= epoch < 2**64 or epoch < policy['minimum_security_epoch']:
        p.error('security epoch must satisfy the embedded minimum policy')
    epoch_bytes = epoch.to_bytes(8, 'big')
    payload = a.kernel.read_bytes()
    if not payload.startswith(b"MZ") or len(payload) > 512 * 1024 * 1024 - 272:
        p.error("expected a PE bzImage below 512 MiB")
    signature = key.sign(DOMAIN + payload + epoch_bytes, padding.PKCS1v15(), hashes.SHA256())
    a.output.write_bytes(payload + epoch_bytes + signature + MAGIC)

if __name__ == "__main__":
    main()
