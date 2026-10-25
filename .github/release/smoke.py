#!/usr/bin/env python3
"""Run with the packaged kitty +launch, without a development Python or prefix."""

import importlib
import os
import ssl
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from kitty.constants import configure_gtk_runtime_paths, kitty_exe
from kitty.fast_data_types import gtk_backend_available

assert sys.frozen, 'Expected the frozen Python runtime'
assert ssl.create_default_context().get_ca_certs(), 'The shipped CA bundle did not load'
assert gtk_backend_available(), (
    'The frozen Linux release was built without GTK 4/libadwaita support'
)
for module in ('bz2', 'lzma', 'ctypes', 'sqlite3', 'cairo', 'kittens.transfer.rsync', 'kitty.fast_data_types'):
    importlib.import_module(module)
# Importing the real window module also checks PyGObject, typelibs, libadwaita
# versions and their shared libraries, without needing a display server.
configure_gtk_runtime_paths()
importlib.import_module('kitty.gtk_window')
kitty = Path(kitty_exe())
# Emulate a development package on the host exposing an unversioned systemd
# symlink. The portable process must keep using the runtime SONAME loaded by
# GTK/GIO, rather than mixing two library instances and corrupting sd_bus data.
host_systemd = next(Path('/usr/lib').glob('*/libsystemd.so.0'), None)
if host_systemd is not None:
    with TemporaryDirectory() as tdir:
        (Path(tdir) / 'libsystemd.so').symlink_to(host_systemd)
        env = dict(os.environ)
        env['LD_LIBRARY_PATH'] = tdir + os.pathsep + env.get('LD_LIBRARY_PATH', '')
        subprocess.run([str(kitty), '+runpy', '''
import ctypes, os
from kitty.constants import kitty_exe
ctypes.CDLL(os.path.join(os.path.dirname(os.path.dirname(kitty_exe())), 'lib', 'libsystemd.so.0'), mode=ctypes.RTLD_GLOBAL)
from kitty.fast_data_types import systemd_move_pid_into_new_scope
try:
    systemd_move_pid_into_new_scope(os.getpid(), 'kitty-release-test.scope', 'release test')
except (NotImplementedError, OSError):
    pass
with open('/proc/self/maps') as f:
    libraries = {line.split()[-1] for line in f if '/libsystemd.so' in line}
if len(libraries) != 1:
    raise RuntimeError(f'Loaded incompatible systemd libraries: {libraries}')
'''], env=env, check=True, timeout=10)
kitten = Path(sys.argv[1]) if len(sys.argv) > 1 else kitty.parent / 'kitten'
for executable, option in ((kitty, '--version'), (kitten, '--version'), (kitty.parent / 'slangc', '-version')):
    subprocess.run([str(executable), option], check=True)
print('Frozen runtime, GTK, native modules, certificates, kitty, kitten, and Slang OK')
