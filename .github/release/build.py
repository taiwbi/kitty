#!/usr/bin/env python3
"""Build portable releases with upstream's prebuilt dependencies and freezer."""

import argparse
import hashlib
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

FRIBIDI_VERSION = '1.0.16'
FRIBIDI_SHA256 = '1b1cde5b235d40479e91be2f0e88a309e3214c8ab470ec8a2744d82a5a9ea05c'
SOURCE = Path.cwd()
ROOT = Path(os.environ['BYPY_ROOT'])
PREFIX = ROOT / 'sw/sw'
OUTPUT = SOURCE / 'dist'
IS_MACOS = sys.platform == 'darwin'


def run(*cmd: str, cwd: Path = SOURCE, env: dict[str, str] | None = None) -> None:
    print(shlex.join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def check_gtk_fallback() -> None:
    # Linux builders with GTK installed would otherwise never compile these
    # stubs. Reuse setup.py's compiler/headers and keep warnings fatal, as on
    # macOS and on systems without the optional GTK development packages.
    commands = json.loads((SOURCE / 'build/compile_commands.json').read_text())
    entry, = (c for c in commands if c['file'] == 'kitty/gtk_backend.c')
    args = [a for a in entry['arguments'] if a != '-MMD']
    args.insert(args.index('-c'), '-UKITTY_WITH_GTK')
    with tempfile.TemporaryDirectory() as tmp:
        args[args.index('-o') + 1] = str(Path(tmp) / 'gtk_backend.o')
        run(*args, cwd=Path(entry['directory']))


def build_fribidi() -> None:
    # This dependency is newer than the upstream CI bundles. Static linking also
    # avoids introducing a system FriBidi dependency into the portable archives.
    archive = ROOT / f'sw/downloads/fribidi-{FRIBIDI_VERSION}.tar.xz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == FRIBIDI_SHA256, 'FriBidi checksum mismatch'
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with tarfile.open(archive) as tf:
            tf.extractall(base, filter='data')
        source = base / f'fribidi-{FRIBIDI_VERSION}'
        arches = ('arm64', 'x86_64') if IS_MACOS else ('native',)
        libraries = []
        for arch in arches:
            build = base / arch
            build.mkdir()
            env = os.environ.copy()
            env['CFLAGS'] = '-O2 -fPIC'
            env['LDFLAGS'] = ''
            flags = []
            if IS_MACOS:
                env['CC'] = 'clang'
                env['CFLAGS'] += f' -arch {arch} -mmacosx-version-min=12.0'
                env['LDFLAGS'] = f'-arch {arch} -mmacosx-version-min=12.0'
                host = 'aarch64' if arch == 'arm64' else arch
                flags.append(f'--host={host}-apple-darwin')
            run(str(source / 'configure'), f'--prefix={PREFIX}', '--disable-shared', '--enable-static', '--with-pic', *flags, cwd=build, env=env)
            run('make', '-j4', cwd=build, env=env)
            run('make', 'install', cwd=build, env=env)
            library = base / f'libfribidi-{arch}.a'
            shutil.copy2(PREFIX / 'lib/libfribidi.a', library)
            libraries.append(str(library))
        if IS_MACOS:
            run('lipo', '-create', *libraries, '-output', str(PREFIX / 'lib/libfribidi.a'))
            run('lipo', str(PREFIX / 'lib/libfribidi.a'), '-verify_arch', 'arm64', 'x86_64')


def smoke_test(kitty: Path, kitten: Path) -> None:
    # Run from outside the source tree without the build-time library paths.
    build_vars = {'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'SW', 'SLANGC', 'FONTCONFIG_PATH', 'FONTCONFIG_FILE', 'SSL_CERT_FILE'}
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON') and k not in build_vars}
    with tempfile.TemporaryDirectory() as tmp:
        run(str(kitty), '+launch', str(SOURCE / '.github/release/smoke.py'), str(kitten), cwd=Path(tmp), env=env)


def sign_macos_binaries(app: Path) -> None:
    # Include Python extensions outside the standard macOS nested-code
    # directories (which --deep alone can miss).
    macho = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
    for path in sorted(app.rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as f:
            if f.read(4) not in macho:
                continue
        run('lipo', str(path), '-verify_arch', 'arm64', 'x86_64')
        run('codesign', '--force', '--sign', '-', str(path))


def sign_macos_app(app: Path) -> None:
    # Sign code from the inside out, then seal framework and app bundles.
    sign_macos_binaries(app)
    bundles = [p for p in app.rglob('*') if p.is_dir() and not p.is_symlink() and p.suffix in ('.app', '.framework')]
    for path in sorted(bundles, key=lambda p: len(p.parts), reverse=True):
        run('codesign', '--force', '--sign', '-', str(path))
    run('codesign', '--force', '--sign', '-', str(app))
    run('codesign', '--verify', '--deep', '--strict', str(app))


def preserve_library_aliases(destination: Path) -> None:
    # The freezer copies one filename per library. Some exported libraries have
    # no SONAME, so extension modules need the unversioned name as well (SQLite
    # currently needs libsqlite3.so). Preserve all names for the copied libraries.
    copied = {
        (PREFIX / 'lib' / p.name).resolve(): p.name
        for p in destination.iterdir() if p.is_file() and not p.is_symlink()
    }
    for source in (PREFIX / 'lib').iterdir():
        if (target := copied.get(source.resolve())) and not (destination / source.name).exists():
            (destination / source.name).symlink_to(target)


def freeze() -> Path:
    # bypy.main.build_program() first deletes PREFIX and reinstalls private pkg
    # caches. Our prefix is already populated by the exported CI dependency bundle,
    # so invoke the exact project-specific build/freezing hooks directly.
    from bypy.constants import build_dir
    from bypy.utils import mkdtemp, setup_program_parser

    parser = argparse.ArgumentParser()
    setup_program_parser(parser)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    args = parser.parse_args(['--non-interactive', '--skip-tests', '--extra-program-data', revision])
    init_env = runpy.run_path(str(SOURCE / 'bypy/init_env.py'), run_name='program')
    # Download once with curl's retry and platform trust-store support, before
    # building. Both upstream freezers use this URL to copy the shipped CA bundle.
    init_env['kitty_constants']['cacerts_url'] = (ROOT / 'sw/downloads/cacert.pem').as_uri()
    ext_dir = mkdtemp('plugins-')
    build_dir(mkdtemp('build-'))
    init_env['build_c_extensions'](ext_dir, args)
    data: dict[str, Any] = {'args': args, 'ext_dir': ext_dir, 'init_env': init_env}
    if IS_MACOS:
        mod = runpy.run_path(str(SOURCE / 'bypy/macos'), init_globals=data, run_name='release_builder')
        upstream_freeze = mod['Freeze']

        class AdHocFreeze(upstream_freeze):
            def add_misc_libraries(self) -> None:
                super().add_misc_libraries()
                preserve_library_aliases(Path(self.frameworks_dir))

            def build_frozen_tools(self) -> None:
                # This step executes the relocated frozen kitty to generate Go
                # code and shaders. Its rewritten Mach-O load commands must be
                # re-signed already, or Apple Silicon can kill the executable.
                sign_macos_binaries(Path(self.build_dir))
                super().build_frozen_tools()

            def makedmg(self, app: str, volname: str, format: str = 'ULMO') -> str:
                # install_name_tool invalidates Apple Silicon code signatures.
                # Re-sign the completed app before testing and putting it in a DMG.
                sign_macos_app(Path(app))
                smoke_test(Path(app) / 'Contents/MacOS/kitty', Path(app) / 'Contents/MacOS/kitten')
                return super().makedmg(app, volname, format)

        AdHocFreeze(str(Path(ext_dir) / 'kitty.app'), skip_tests=True)
    else:
        # Same sequence as upstream main(), with aliases retained after copying.
        mod = runpy.run_path(str(SOURCE / 'bypy/linux'), init_globals=data, run_name='release_builder')
        env = mod['Env'](str(Path(ext_dir) / 'kitty'))
        mod['copy_libs'](env)
        preserve_library_aliases(Path(env.lib_dir))
        mod['copy_python'](env)
        init_env['build_frozen_launcher']([mod['path_to_freeze_dir'](), env.obj_dir])
        files = mod['find_binaries'](env)
        mod['fix_permissions'](files)
        mod['add_ca_certs'](env)
        init_env['build_frozen_tools'](str(Path(env.base) / 'bin/kitty'))
        if not args.dont_strip:
            mod['strip_binaries'](files)
        smoke_test(Path(env.base) / 'bin/kitty', Path(env.base) / 'bin/kitten')
        mod['create_tarfile'](env, args.compression_level)
    return Path(ext_dir)


def source_archive(version: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        name = f'kitty-{version}'
        archive = base / f'{name}.tar'
        run('git', 'archive', f'--prefix={name}/', '-o', str(archive), 'HEAD')
        with tarfile.open(archive) as tf:
            tf.extractall(base, filter='tar')
        for kind in ('html', 'man'):
            shutil.copytree(SOURCE / f'docs/_build/{kind}', base / name / f'docs/_build/{kind}')
        run('tar', '-cf', str(archive), '-C', str(base), name)
        run('xz', '-T2', '-9', str(archive))
        shutil.copy2(archive.with_suffix('.tar.xz'), OUTPUT)


def sbom(version: str) -> None:
    from bypy.main import sbom, setup_sbom_parser

    dest = OUTPUT / f'kitty-{version}.tar.xz.spdx.json'
    repo = os.environ.get('GITHUB_REPOSITORY', 'kovidgoyal/kitty')
    parser = argparse.ArgumentParser()
    setup_sbom_parser(parser)
    sbom(parser.parse_args([repo, version, '--url', f'https://github.com/{repo}', '--person', repo.split('/')[0], '--output', str(dest)]))
    document = json.loads(dest.read_text())
    document['packages'].append({
        'name': 'fribidi', 'SPDXID': 'SPDXRef-fribidi', 'versionInfo': FRIBIDI_VERSION,
        'downloadLocation': f'https://github.com/fribidi/fribidi/releases/download/v{FRIBIDI_VERSION}/fribidi-{FRIBIDI_VERSION}.tar.xz',
        'licenseConcluded': 'LGPL-2.1-or-later', 'filesAnalyzed': False,
        'checksums': [{'algorithm': 'SHA256', 'checksumValue': FRIBIDI_SHA256}],
    })
    document['relationships'].append({
        'spdxElementId': 'SPDXRef-fribidi', 'relatedSpdxElement': document['packages'][0]['SPDXID'], 'relationshipType': 'DEPENDENCY_OF',
    })
    dest.write_text(json.dumps(document, indent=2) + '\n')


def main() -> None:
    sys.path.insert(0, str(ROOT / 'bypy'))
    # Exported Python must match sources.json: frozen bytecode is ABI-specific.
    from bypy.constants import python_major_minor_version

    assert sys.version_info[:2] == python_major_minor_version(), 'Dependency bundle Python version does not match sources.json'
    OUTPUT.mkdir(exist_ok=True)
    build_fribidi()
    run(sys.executable, 'setup.py', 'build')
    check_gtk_fallback()
    docs_env = dict(os.environ, PYTHONPATH=os.environ['RELEASE_DOCS_DEPS'])
    run('make', 'docs', f'SPHINXBUILD={shlex.quote(sys.executable)} -m sphinx', env=docs_env)
    ext_dir = freeze()
    try:
        for asset in (ROOT / 'sw/dist').iterdir():
            shutil.copy2(asset, OUTPUT)
        if not IS_MACOS:
            # Test the extracted download, at a different location, without SW.
            asset, = OUTPUT.glob('kitty-*.txz')
            with tempfile.TemporaryDirectory() as tmp:
                package = Path(tmp)
                with tarfile.open(asset) as tf:
                    tf.extractall(package, filter='tar')
                smoke_test(package / 'bin/kitty', package / 'bin/kitten')
        if os.environ.get('RELEASE_STATIC_KITTENS') == '1':
            run(sys.executable, 'setup.py', 'build-static-binaries', '--dir-for-static-binaries', str(OUTPUT))
            run(str(OUTPUT / 'kitten-linux-amd64'), '--version')
            version = runpy.run_path('setup.py', run_name='release_metadata')['version']
            ver = '.'.join(map(str, version))
            source_archive(ver)
            sbom(ver)
    finally:
        shutil.rmtree(ext_dir)


if __name__ == '__main__':
    main()
