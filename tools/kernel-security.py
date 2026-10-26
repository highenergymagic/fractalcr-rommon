#!/usr/bin/env python3
"""Apply reviewed security updates to a disposable copy of verified CIP source."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def prepare(work, security):
    work = work.resolve()
    source = work/'sources/linux'
    target = work/'build/linux-security-source'
    manifest = json.loads((security/'kernel-security.json').read_text())
    makefile = (source/'Makefile').read_text()
    version = '.'.join(re.search(r'^'+name+r' = (\d+)$', makefile, re.M).group(1)
                       for name in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL'))
    version += (source/'localversion-cip').read_text().strip()
    if version != manifest['base']:
        raise ValueError('kernel security updates do not match CIP source version')
    patches = []
    for entry in manifest['patches'] + manifest.get('backports', []):
        name = entry['file']
        if Path(name).name != name:
            raise ValueError('invalid kernel patch filename')
        data = (security/name).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('kernel security patch checksum mismatch')
        patches.append(gzip.decompress(data) if name.endswith('.gz') else data)
    if source.is_symlink() or not source.is_dir() or target.is_symlink():
        raise ValueError('kernel source directories must not be links')
    # Recreate from the archive-verified source every time. A prior patched tree
    # is never considered an authenticated cache based only on a hash marker.
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)
    for patch in patches:
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1'],
                       input=patch, cwd=target, check=True)
    shutil.copyfile(security/'kernel-security.json', target/'fractal-kernel-security.json')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('/work'))
    parser.add_argument('--security', type=Path,
                        default=Path(__file__).resolve().parents[1]/'kernel/security')
    args = parser.parse_args()
    print(prepare(args.work, args.security))
