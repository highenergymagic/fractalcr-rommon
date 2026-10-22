#!/usr/bin/env python3
"""Fetch exact source archives and invalidate incompatible extracted/build caches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.request


class UnsafeArchive(ValueError):
    pass


def extract_source(tar, destination):
    """Data-only extraction, including on Debian's pre-filter Python 3.11."""
    root = Path(destination).resolve()
    for member in tar:
        # Check immediately before each extraction so preceding symlinks are
        # resolved. No special files, absolute names, or escaping link targets.
        path = root / member.name
        if Path(member.name).is_absolute() or not path.resolve().is_relative_to(root):
            raise UnsafeArchive('archive path escapes extraction directory')
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise UnsafeArchive('special file in source archive')
        if member.issym() or member.islnk():
            link = (path.parent if member.issym() else root) / member.linkname
            if Path(member.linkname).is_absolute() or not link.resolve().is_relative_to(root):
                raise UnsafeArchive('archive link escapes extraction directory')
        member.mode &= 0o777
        tar.extract(member, root, set_attrs=False)
        if not member.issym():
            path.chmod(member.mode | (0o700 if member.isdir() else 0o600))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(name, pin, work, download=True):
    if name not in ('buildroot', 'linux') or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256']):
        raise ValueError('invalid source pin')
    if not pin['url'].startswith('https://'):
        raise ValueError('source URLs must use HTTPS')
    work = work.resolve()
    archives = work / 'downloads'
    archives.mkdir(parents=True, exist_ok=True)
    archive = archives / (name + '.tar.xz')
    if not archive.is_file() or digest(archive) != pin['sha256']:
        if not download:
            raise ValueError('archive missing or checksum mismatch')
        pending = archive.with_suffix('.pending')
        try:
            with urllib.request.urlopen(pin['url'], timeout=120) as src, pending.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            if digest(pending) != pin['sha256']:
                raise ValueError('download checksum mismatch: ' + name)
            pending.replace(archive)
        finally:
            pending.unlink(missing_ok=True)
    sources = work / 'sources'
    sources.mkdir(exist_ok=True)
    target = sources / name
    marker = sources / (name + '.sha256')
    if target.is_dir() and not target.is_symlink() and marker.is_file() and marker.read_text().strip() == pin['sha256']:
        return False
    # Validate extraction before touching a previously working cache.
    with tempfile.TemporaryDirectory(dir=sources, prefix=name + '.pending-') as tmp:
        with tarfile.open(archive) as tar:
            extract_source(tar, tmp)
        roots = list(Path(tmp).iterdir())
        if len(roots) != 1 or not roots[0].is_dir() or roots[0].is_symlink():
            raise ValueError('source archive must have one root directory')
        if target.is_symlink():
            raise ValueError('source cache must not be a symlink')
        outputs = ['kernel-rommon', 'kernel-witness']
        if name == 'buildroot':
            outputs.append('rommon-root')
        for output in outputs:
            if (work / 'build' / output).is_symlink():
                raise ValueError('build cache must not be a symlink')
        marker.unlink(missing_ok=True)
        if target.exists():
            shutil.rmtree(target)
        roots[0].rename(target)
        # Buildroot can retain old packages even after the source was replaced.
        for output in outputs:
            path = work / 'build' / output
            if path.exists():
                shutil.rmtree(path)
        marker.write_text(pin['sha256'] + '\n')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('/src/boot/sources.json'))
    parser.add_argument('--work', type=Path, default=Path('/work'))
    args = parser.parse_args()
    pins = json.loads(args.manifest.read_text())
    for name in ('buildroot', 'linux'):
        changed = prepare(name, pins[name], args.work)
        print(f"PINNED {name} {pins[name]['version']} ({'extracted' if changed else 'cached'})")


if __name__ == '__main__':
    main()
