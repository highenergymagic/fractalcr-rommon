#!/bin/sh
set -eu
umask 077
case "${FCR_SIGNING_MODE:-development}" in
    external)
        : "${FCR_TRUST_DIR:?Public trust bundle required for external signing}"
        exec python3 /src/tools/release.py prepare-trust --public "$FCR_TRUST_DIR" --destination /work/build/keys
        ;;
    development) ;;
    *) echo 'Unknown signing mode' >&2; exit 2 ;;
esac
mkdir -p /work/build/keys
cd /work/build/keys
test ! -f trust.json || { echo 'Refusing development key generation in a production trust volume' >&2; exit 1; }
# These are disposable development keys, retained in the build volume so
# incremental builds keep the same trust anchor. Never enroll them on hardware.
if [ ! -f firmware.key ]; then
    openssl req -new -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 \
        -subj '/CN=FractalCR Development Secure Boot/' \
        -keyout firmware.key -out firmware.crt
fi
if [ ! -f release.key ]; then
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out release.key
fi
openssl pkey -in release.key -pubout -out release.pub
python3 - <<'PY'
from pathlib import Path
import json
policy = json.loads(Path('/src/boot/image-policy.json').read_text())
floor = policy['minimum_security_epoch']
if type(floor) is not int or not 1 <= floor < 2**64 or policy['format'] != 2:
    raise ValueError('invalid embedded image policy')
pem = Path("release.pub").read_text()
Path("release-key.h").write_text(
    "/* Generated public key, embedded in signed ROMMON. */\n"
    f"#define FCR_MIN_SECURITY_EPOCH {floor}ULL\n"
    "static const char release_key_pem[] =\n" +
    "".join('"' + line + '\\n"\n' for line in pem.splitlines()) + ";\n")
PY
