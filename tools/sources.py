#!/usr/bin/env python3
"""Fetch exact source archives and invalidate incompatible extracted/build caches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.request


class UnsafeArchive(ValueError):
    pass


def extract_source(tar, destination, allowed_absolute_links=()):
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
            allowed_leaf = member.issym() and (member.name, member.linkname) in allowed_absolute_links
            if not allowed_leaf and (Path(member.linkname).is_absolute() or not link.resolve().is_relative_to(root)):
                raise UnsafeArchive('archive link escapes extraction directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        if member.isdir():
            path.mkdir(exist_ok=True)
        elif member.issym():
            path.symlink_to(member.linkname)
        elif member.islnk():
            # Reject forward links instead of recursively extracting a later,
            # not-yet-validated tar member through tarfile's link machinery.
            if link.is_symlink() or not link.is_file():
                raise UnsafeArchive('hardlink target must be an extracted regular file')
            os.link(link, path)
        else:
            with tar.extractfile(member) as src, path.open('xb') as dst:
                shutil.copyfileobj(src, dst)
        if not member.issym():
            path.chmod((member.mode & 0o777) | (0o700 if member.isdir() else 0o600))
            os.utime(path, (member.mtime, member.mtime))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_source_cache(archive, target):
    """Compare cached source to the authenticated archive, including extra files.

    A matching version marker alone cannot detect local edits to extracted
    source. Build outputs must stay outside this source directory.
    """
    expected, records = set(), {}
    # Hash in a single streaming pass. Seeking among duplicate headers in an
    # xz-compressed kernel archive otherwise repeatedly decompresses gigabytes.
    with tarfile.open(archive, 'r|*') as tar:
        for member in tar:
            relative = Path(*Path(member.name).parts[1:])
            if relative == Path('.'):
                continue
            name = relative.as_posix()
            expected.add(name)
            expected.update(parent.as_posix() for parent in relative.parents if parent != Path('.'))
            mode = (member.mode & 0o777) | (0o700 if member.isdir() else 0o600)
            if member.issym():record = ('link', mode, member.linkname)
            elif member.isdir():record = ('directory', mode, None)
            elif member.isfile():
                with tar.extractfile(member) as source:
                    record = ('file', mode, hashlib.file_digest(source, 'sha256').hexdigest())
            elif member.islnk():
                linked = Path(*Path(member.linkname).parts[1:]).as_posix()
                if linked not in records or records[linked][0] != 'file':
                    raise ValueError('invalid cached archive hardlink')
                record = ('file', mode, records[linked][2])
            else:raise ValueError('special file in cached archive')
            # Directory headers can repeat; extraction leaves the final mode.
            records[name] = record
    for name, (kind, mode, value) in records.items():
        path = target/name
        try:info = path.lstat()
        except FileNotFoundError:raise ValueError('source cache file missing: ' + name)
        if kind == 'link':valid = stat.S_ISLNK(info.st_mode) and os.readlink(path) == value
        elif kind == 'directory':valid = stat.S_ISDIR(info.st_mode)
        else:
            valid = stat.S_ISREG(info.st_mode) and path.resolve().is_relative_to(target.resolve())
            if valid:valid = digest(path) == value
        if valid and kind != 'link' and os.name == 'posix':
            valid = stat.S_IMODE(info.st_mode) == mode
        if not valid:raise ValueError('modified source cache: ' + name)
    actual = {p.relative_to(target).as_posix() for p in target.rglob('*')}
    if actual != expected:
        raise ValueError('source cache contains untracked or missing paths')


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
    if target.is_symlink():
        raise ValueError('source cache must not be a symlink')
    if name == 'buildroot' and (target/'dl').exists():
        # Older builds let Buildroot place its download cache inside source.
        # Preserve it outside the verified tree before validating/replacing it.
        legacy, destination = target/'dl', archives/'buildroot-packages'
        if legacy.is_symlink() or not legacy.is_dir() or destination.is_symlink():
            raise ValueError('ambiguous legacy Buildroot download cache; reconcile without overwriting files')
        if destination.exists():
            # Buildroot housekeeping can recreate an empty default dl directory.
            # Only remove that empty directory; never merge two populated caches.
            if not destination.is_dir() or any(legacy.iterdir()):
                raise ValueError('ambiguous legacy Buildroot download cache; reconcile without overwriting files')
            legacy.rmdir()
        else:
            legacy.rename(destination)
    if target.is_dir() and not target.is_symlink() and marker.is_file() and marker.read_text().strip() == pin['sha256']:
        verify_source_cache(archive, target)
        return False
    # Validate extraction before touching a previously working cache.
    with tempfile.TemporaryDirectory(dir=sources, prefix=name + '.pending-') as tmp:
        with tarfile.open(archive) as tar:
            # Buildroot ships one absolute link as target-rootfs skeleton data.
            # Preserve that exact leaf, but never traverse it during extraction.
            allowed = {(f"buildroot-{pin['version']}/package/skeleton-init-openrc/skeleton/etc/runlevels/default/sysv-rcs",
                        '/etc/init.d/sysv-rcs')} if name == 'buildroot' else ()
            extract_source(tar, tmp, allowed)
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
    prepare_configuration(args.manifest.parent / 'buildroot', args.work)


def prepare_configuration(external, work):
    # Buildroot cannot safely migrate a populated rootfs/sysroot between libc
    # or compiler configurations. Include overrides and their hash/patch inputs.
    files = [external/'configs/fractalcr_rommon_defconfig', external/'security-overrides.mk']
    files += sorted(p for p in (external/'patches').rglob('*') if p.is_file())
    state = json.dumps({str(p.relative_to(external)): digest(p) for p in files}, sort_keys=True)
    marker = work/'sources/buildroot-configuration.json'
    if marker.is_file() and marker.read_text() == state:
        return False
    targets = [work/'build'/name for name in ('rommon-root', 'kernel-rommon', 'kernel-witness')]
    if any(p.is_symlink() for p in targets):
        raise ValueError('build cache must not be a symlink')
    for path in targets:
        if path.exists():shutil.rmtree(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(state)
    print('Buildroot configuration changed; incompatible compiler/rootfs/kernel outputs invalidated')
    return True


if __name__ == '__main__':
    main()
