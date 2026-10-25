# License: GPL v3 Copyright: 2026

import os
import subprocess
import warnings
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .base import BaseTest


class TestGtkWindow(BaseTest):
    def setUp(self):
        super().setUp()
        try:
            from kitty import gtk_window
        except (ImportError, ValueError):
            self.skipTest('GTK 4 introspection is unavailable')
        self.backend = gtk_window
        warning_context = warnings.catch_warnings()
        warning_context.__enter__()
        self.addCleanup(warning_context.__exit__, None, None, None)
        warnings.filterwarnings('ignore', message=r"'asyncio\..*' is deprecated", category=DeprecationWarning)

    def test_gtk_timer_in_python_main_context(self):
        from kitty.constants import kitty_exe
        from kitty.fast_data_types import gtk_backend_available

        if not gtk_backend_available() or not any(os.environ.get(k) for k in ('DISPLAY', 'WAYLAND_DISPLAY')):
            self.skipTest('GTK and a display server are required')
        # MainContext.iteration() releases the GIL. Native GTK timer callbacks
        # must reacquire it before calling Python, even outside kitty's C loop.
        code = '''
from kitty.constants import configure_gtk_runtime_paths
configure_gtk_runtime_paths()
from kitty.fast_data_types import gtk_backend_initialize, add_timer
from gi.repository import GLib
gtk_backend_initialize()
fired = []
add_timer(lambda timer_id, fired=fired: fired.append(timer_id), 0.001, False)
while not fired:
    GLib.MainContext.default().iteration(True)
assert len(fired) == 1
'''
        result = subprocess.run([kitty_exe(), '+runpy', code], capture_output=True, timeout=10)
        self.ae(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_gtk_ignores_unallocated_resize(self):
        backend = self.backend
        events = backend.GtkKittyEvents()
        events.bind(1)
        with patch.object(backend, 'gtk_input_resize') as resized:
            for width, height in ((0, 0), (640, 0), (0, 480)):
                events.resized(width, height, 1)
            resized.assert_not_called()
            events.resized(640, 480, 2)
            resized.assert_called_once_with(1, 640, 480, 2)

    def test_gtk_scrollbar_sync_after_snapshot(self):
        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window.events = SimpleNamespace(render=Mock(return_value=True))
        window._scrollbar_sync_source = 0
        window._sync_scrollbar = Mock()

        # GTK must finish snapshotting before scrollbar changes invalidate its
        # allocation. Multiple renders should share a single pending update.
        self.assertTrue(window._area_rendered(None, None))
        source = window._scrollbar_sync_source
        self.assertGreater(source, 0)
        self.assertTrue(window._area_rendered(None, None))
        self.ae(window._scrollbar_sync_source, source)
        window._sync_scrollbar.assert_not_called()
        context = backend.GLib.MainContext.default()
        while window._scrollbar_sync_source:
            context.iteration(True)
        window._sync_scrollbar.assert_called_once_with()
        self.ae(window._scrollbar_sync_source, 0)

    def test_gtk_scrollbar_sync_cancelled_on_close(self):
        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window.events = SimpleNamespace(render=Mock(return_value=True), bind=Mock())
        window.window = Mock()
        window.os_window_id = 0
        window._scrollbar_sync_source = 0
        window._sync_scrollbar = Mock()

        window._area_rendered(None, None)
        source = window._scrollbar_sync_source
        window.close()
        self.ae(window._scrollbar_sync_source, 0)
        self.assertIsNone(backend.GLib.MainContext.default().find_source_by_id(source))
        window._sync_scrollbar.assert_not_called()
        window.window.close.assert_called_once_with()
