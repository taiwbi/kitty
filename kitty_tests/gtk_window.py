# License: GPL v3 Copyright: 2026

import os
import subprocess
import warnings
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

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

    def test_gtk_keypad_navigation(self):
        from kitty import fast_data_types as keys

        backend = self.backend
        # With Num Lock off, GDK emits KP_* navigation keysyms, which have
        # no Unicode value. Preserve their keypad identity for kitty's protocol.
        navigation = (
            ('Home', 'HOME', '\x1b[H'), ('End', 'END', '\x1b[F'),
            ('Page_Up', 'PAGE_UP', '\x1b[5~'), ('Page_Down', 'PAGE_DOWN', '\x1b[6~'),
            ('Left', 'LEFT', '\x1b[D'), ('Right', 'RIGHT', '\x1b[C'),
            ('Up', 'UP', '\x1b[A'), ('Down', 'DOWN', '\x1b[B'),
            ('Insert', 'INSERT', '\x1b[2~'), ('Delete', 'DELETE', '\x1b[3~'),
            ('Begin', 'BEGIN', '\x1b[E'),
        )
        for gdk_name, kitty_name, encoded in navigation:
            keyval = getattr(backend.Gdk, f'KEY_KP_{gdk_name}')
            expected = getattr(keys, f'GLFW_FKEY_KP_{kitty_name}')
            for state in (0, backend.Gdk.ModifierType.SHIFT_MASK, backend.Gdk.ModifierType.CONTROL_MASK):
                with self.subTest(key=gdk_name, state=state):
                    key, shifted = backend.GtkKittyWindow._kitty_key(keyval, state)
                    self.ae((key, shifted), (expected, 0))
            self.ae(keys.encode_key_for_tty(key=key), encoded)

        # Num Lock on still produces digits through the Unicode path.
        for digit in range(10):
            keyval = getattr(backend.Gdk, f'KEY_KP_{digit}')
            self.ae(backend.GtkKittyWindow._kitty_key(keyval, 0), (ord(str(digit)), 0))

    def test_gtk_keypad_navigation_events(self):
        from kitty import fast_data_types as keys

        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window.os_window_id = 1
        window._pressed_keys = set()
        window._ime_filtered_keys = set()
        controller = Mock()
        controller.get_current_event.return_value = None

        for name in ('HOME', 'END', 'PAGE_UP', 'PAGE_DOWN'):
            keyval = getattr(backend.Gdk, f'KEY_KP_{name.title()}')
            key = getattr(keys, f'GLFW_FKEY_KP_{name}')
            with self.subTest(key=name), patch.object(backend, 'gtk_input_key', return_value=True) as input_key:
                self.assertTrue(window._key_pressed(controller, keyval, 79, 0))
                self.assertTrue(window._key_pressed(controller, keyval, 79, 0))
                self.assertTrue(window._key_released(controller, keyval, 79, 0))
                self.ae(input_key.call_args_list, [
                    call(1, key, 0, 0, keyval, action, 0, '')
                    for action in (backend.GLFW_PRESS, backend.GLFW_REPEAT, backend.GLFW_RELEASE)
                ])
                self.assertFalse(window._pressed_keys)

    def test_gtk_timer_in_python_main_context(self):
        from kitty.constants import kitty_exe
        from kitty.fast_data_types import gtk_backend_available

        if not gtk_backend_available() or not any(os.environ.get(k) for k in ('DISPLAY', 'WAYLAND_DISPLAY')):
            self.skipTest('GTK and a display server are required')
        # MainContext.iteration() releases the GIL. Native GTK timer callbacks
        # must reacquire it before calling Python, even outside kitty's C loop.
        code = r'''
import warnings
warnings.filterwarnings('ignore', message=r"'asyncio\..*' is deprecated", category=DeprecationWarning)
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
