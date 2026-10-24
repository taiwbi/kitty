#!/usr/bin/env python3
"""Reject incomplete releases and validate cross-compiled binaries before upload."""

import hashlib
import json
import re
import struct
import subprocess
import tarfile
from pathlib import Path

PLATFORMS = {
    'linux': ('386', 'amd64', 'arm', 'arm64'),
}


def source_version() -> str:
    match = re.search(r'^version: Version = Version\((\d+), (\d+), (\d+)\)', Path('kitty/constants.py').read_text(), re.M)
    assert match, 'Could not read kitty source version'
    return '.'.join(match.groups())


def check_elf(data: bytes, arch: str) -> None:
    assert data[:4] == b'\x7fELF', f'Not an ELF executable: {arch}'
    assert data[5] == 1, 'Expected little endian ELF'
    expected = {'386': (1, 3), 'amd64': (2, 62), 'arm': (1, 40), 'arm64': (2, 183)}
    assert (data[4], struct.unpack_from('<H', data, 18)[0]) == expected[arch], f'Wrong ELF architecture: {arch}'


def check_kitten(path: Path, goos: str, arch: str, revision: str) -> None:
    assert path.stat().st_size > 1_000_000, f'Empty or truncated binary: {path}'
    metadata = subprocess.check_output(['go', 'version', '-m', str(path)], text=True)
    for item in (f'GOOS={goos}', f'GOARCH={arch}', 'CGO_ENABLED=0', 'IsStandaloneBuild=true', f'VCSRevision={revision}'):
        assert item in metadata, f'{path}: missing build metadata {item}'
    header = path.read_bytes()[:32]
    check_elf(header, arch)


def check_bundle(path: Path, arch: str) -> None:
    with tarfile.open(path) as tf:
        members = {m.name.removeprefix('./'): m for m in tf}
        for name in ('bin/kitty', 'bin/kitten', 'lib/cacert.pem', 'share/man/man1/kitty.1', 'share/doc/kitty/html/index.html'):
            assert name in members, f'{path}: missing {name}'
        for binary in ('bin/kitty', 'bin/kitten'):
            member = members[binary]
            assert member.mode & 0o111, f'{path}: {binary} is not executable'
            stream = tf.extractfile(member)
            assert stream is not None
            check_elf(stream.read(32), arch)
        assert any(n.startswith('lib/kitty-extensions/') and 'fast_data_types' in n for n in members), f'{path}: missing native kitty module'


def main() -> None:
    version = source_version()
    dest = Path('dist')
    kittens = {f'kitten-{goos}-{arch}': (goos, arch) for goos, arches in PLATFORMS.items() for arch in arches}
    expected = set(kittens) | {
        f'kitty-{version}-x86_64.txz', f'kitty-{version}-arm64.txz',
        f'kitty-{version}.tar.xz', f'kitty-{version}.tar.xz.spdx.json',
    }
    actual = {p.name for p in dest.iterdir()} - {'SHA256SUMS'}
    assert actual == expected, f'Asset matrix mismatch: missing={expected - actual}, unexpected={actual - expected}'
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    for name, (goos, arch) in kittens.items():
        check_kitten(dest / name, goos, arch, revision)
    for suffix, arch in (('x86_64', 'amd64'), ('arm64', 'arm64')):
        check_bundle(dest / f'kitty-{version}-{suffix}.txz', arch)
    with tarfile.open(dest / f'kitty-{version}.tar.xz') as tf:
        names = set(tf.getnames())
        prefix = f'kitty-{version}/'
        for name in ('setup.py', 'go.mod', 'docs/_build/html/index.html', 'docs/_build/man/kitty.1'):
            assert prefix + name in names, f'Source archive missing {name}'
    sbom = json.loads((dest / f'kitty-{version}.tar.xz.spdx.json').read_text())
    assert sbom['spdxVersion'] == 'SPDX-2.3'
    assert any(p['name'] == 'fribidi' for p in sbom['packages']), 'SBOM omits the static FriBidi dependency'
    checksums = []
    for name in sorted(expected):
        with (dest / name).open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        checksums.append(f'{digest}  {name}\n')
    (dest / 'SHA256SUMS').write_text(''.join(checksums))
    print(f'Validated {len(expected)} release assets and wrote SHA256SUMS')


if __name__ == '__main__':
    main()
