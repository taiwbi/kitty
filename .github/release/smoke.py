#!/usr/bin/env python3
"""Run with the packaged kitty +launch, without a development Python or prefix."""

import importlib
import ssl
import subprocess
import sys
from pathlib import Path

from kitty.constants import kitty_exe
from kitty.fast_data_types import gtk_backend_available

assert sys.frozen, 'Expected the frozen Python runtime'
assert ssl.create_default_context().get_ca_certs(), 'The shipped CA bundle did not load'
assert gtk_backend_available(), (
    'The frozen Linux release was built without GTK 4/libadwaita support'
)
for module in ('bz2', 'lzma', 'ctypes', 'sqlite3', 'kittens.transfer.rsync', 'kitty.fast_data_types'):
    importlib.import_module(module)
kitty = Path(kitty_exe())
kitten = Path(sys.argv[1]) if len(sys.argv) > 1 else kitty.parent / 'kitten'
for executable, option in ((kitty, '--version'), (kitten, '--version'), (kitty.parent / 'slangc', '-version')):
    subprocess.run([str(executable), option], check=True)
print('Frozen runtime, native modules, certificates, kitty, kitten, and Slang OK')
