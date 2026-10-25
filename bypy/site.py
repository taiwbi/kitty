#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2021, Kovid Goyal <kovid at kovidgoyal.net>

import _sitebuiltins
import builtins
import os
import sys


def set_quit() -> None:
    eof = 'Ctrl-D (i.e. EOF)'
    builtins.quit = _sitebuiltins.Quitter('quit', eof)
    builtins.exit = _sitebuiltins.Quitter('exit', eof)


def set_helper() -> None:
    builtins.help = _sitebuiltins._Helper()


def main() -> None:
    sys.argv[0] = sys.calibre_basename
    if sys.platform.startswith('linux'):
        run_data = getattr(sys, 'kitty_run_data', {})
        executable_dir = run_data.get('bundle_exe_dir')
        if executable_dir:
            base = os.path.dirname(executable_dir)
        else:
            base = os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))
        typelibs = os.path.join(base, 'lib', 'girepository-1.0')
        if os.path.isdir(typelibs):
            existing = os.environ.get('GI_TYPELIB_PATH', '')
            os.environ['GI_TYPELIB_PATH'] = typelibs + (os.pathsep + existing if existing else '')
            os.environ['XDG_DATA_DIRS'] = os.path.join(base, 'share') + os.pathsep + os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share')
    set_helper()
    set_quit()
    mod = __import__(sys.calibre_module, fromlist=[1])
    func = getattr(mod, sys.calibre_function)
    return func()


if __name__ == '__main__':
    main()
