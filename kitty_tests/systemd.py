# License: GPL v3 Copyright: 2026

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .base import BaseTest


class TestSystemd(BaseTest):
    def test_systemd_reuses_runtime_library(self):
        from kitty.constants import kitty_exe

        compiler = shutil.which('cc')
        if not sys.platform.startswith('linux') or not compiler:
            self.skipTest('Linux and a C compiler are required')
        with TemporaryDirectory() as tdir:
            # Emulate a host development symlink resolving to another version
            # than the runtime library already loaded by GTK/GIO.
            library = Path(tdir) / 'libsystemd.so'
            subprocess.run(
                [compiler, '-shared', '-fPIC', '-x', 'c', '-', '-o', str(library)],
                input=b'int sd_bus_default_user(void **bus) { return -38; }', check=True, capture_output=True,
            )
            code = '''
import ctypes, os, sys
try:
    runtime = ctypes.CDLL('libsystemd.so.0')
except OSError:
    sys.exit(77)
from kitty.fast_data_types import systemd_move_pid_into_new_scope
try:
    systemd_move_pid_into_new_scope(os.getpid(), 'kitty-loader-test.scope', 'loader test')
except (NotImplementedError, OSError):
    pass
with open('/proc/self/maps') as f:
    if os.environ['KITTY_TEST_SYSTEMD_LIBRARY'] in f.read():
        raise RuntimeError('loaded a second libsystemd through the host development symlink')
'''
            env = dict(os.environ, KITTY_TEST_SYSTEMD_LIBRARY=str(library))
            env['LD_LIBRARY_PATH'] = tdir + os.pathsep + env.get('LD_LIBRARY_PATH', '')
            result = subprocess.run([kitty_exe(), '+runpy', code], env=env, capture_output=True, timeout=10)
            if result.returncode == 77:
                self.skipTest('The systemd runtime library is unavailable')
            self.ae(result.returncode, 0, result.stderr.decode(errors='replace'))
