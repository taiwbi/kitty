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
        window._context_menu_keys = set()
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

    def test_gtk_blur_uses_css_capability(self):
        version = (self.backend.Gtk.get_major_version(), self.backend.Gtk.get_minor_version(), self.backend.Gtk.get_micro_version())
        self.ae(self.backend._use_gtk_css_wayland_blur, version >= (4, 23, 3))
        self.assertFalse(hasattr(self.backend, 'gtk_backend_set_window_blur'))

    def test_gtk_realization_wait(self):
        backend = self.backend
        for present in (False, True):
            with self.subTest(present=present):
                window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
                window.window, window.gl_area = Mock(), Mock()
                window.gl_area.get_realized.side_effect = (False, True, True)
                window.gl_area.get_width.side_effect = (0, 640)
                window.gl_area.get_height.return_value = 480
                window.gl_area.get_error.return_value = None
                context = Mock()
                with (
                    patch.object(backend.GLib.MainContext, 'default', return_value=context),
                    patch.object(backend.GLib, 'timeout_add', return_value=42),
                    patch.object(backend.GLib, 'source_remove') as remove,
                    patch.object(backend, 'gtk_backend_activate') as activate,
                ):
                    window.realize_gl_area(present)
                self.ae(context.iteration.call_args_list, [call(True), call(True)])
                remove.assert_called_once_with(42)
                window.gl_area.make_current.assert_called_once_with()
                if present:
                    activate.assert_called_once_with()
                    window.window.present.assert_called_once_with()
                    window.gl_area.grab_focus.assert_called_once_with()
                else:
                    activate.assert_not_called()
                    window.window.realize.assert_called_once_with()
                    window.window.set_visible.assert_called_once_with(False)

    def test_gtk_realization_failure(self):
        backend = self.backend
        for failure in ('timeout', 'gl-error'):
            with self.subTest(failure=failure):
                window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
                window.window, window.gl_area = Mock(), Mock()
                window.gl_area.get_realized.return_value = failure != 'timeout'
                window.gl_area.get_width.return_value = 640
                window.gl_area.get_height.return_value = 480
                window.gl_area.get_error.return_value = SimpleNamespace(message='GL failed')
                context = Mock()
                with (
                    patch.object(backend.GLib.MainContext, 'default', return_value=context),
                    patch.object(backend.GLib, 'timeout_add', return_value=42) as add_timeout,
                    patch.object(backend.GLib, 'source_remove') as remove,
                    patch.object(backend, 'gtk_backend_activate'),
                ):
                    context.iteration.side_effect = lambda block: add_timeout.call_args.args[1]()
                    message = 'Timed out waiting' if failure == 'timeout' else 'GL failed'
                    with self.assertRaisesRegex(RuntimeError, message):
                        window.realize_gl_area()
                remove.assert_called_once_with(42)

    def test_gtk_context_menu_selection_and_target(self):
        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window.gl_area = Mock()
        window._gtk_scale = 2
        window._context_menu = None
        target_getter, action_handler = Mock(return_value=(42, False)), Mock()
        window.set_context_menu_callbacks(target_getter, action_handler)
        menu = Mock()
        menu.popdown.side_effect = lambda: window._context_menu_closed(menu)
        with patch.object(backend.Gtk.PopoverMenu, 'new_from_model', return_value=menu):
            window._show_context_menu(10.5, 20)
        target_getter.assert_called_once_with(21, 40)
        rect = menu.set_pointing_to.call_args.args[0]
        self.ae((rect.x, rect.y, rect.width, rect.height), (10, 20, 1, 1))
        copy = window._context_menu_actions.lookup_action('copy')
        self.assertFalse(copy.get_enabled())
        copy.activate(None)
        action_handler.assert_not_called()

        target_getter.return_value = (43, True)
        window._show_context_menu(10, 20)
        self.assertTrue(copy.get_enabled())
        # Native GTK menu buttons emit closed before activating the action.
        window._context_menu_closed(menu)
        window.gl_area.grab_focus.reset_mock()
        copy.activate(None)
        action_handler.assert_called_once_with(43, 'copy')
        self.ae(window._context_menu_target, 0)
        window.gl_area.grab_focus.assert_called_once_with()

    def test_gtk_context_menu_consumes_right_click(self):
        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window.os_window_id = 1
        window.gl_area = Mock()
        window._show_context_menu = Mock()
        gesture = Mock()
        gesture.get_current_button.return_value = 3
        with patch.object(backend, 'gtk_input_mouse_button') as mouse_button:
            window._pointer_pressed(gesture, 1, 10, 20)
            window._pointer_released(gesture, 1, 10, 20)
        mouse_button.assert_not_called()
        window._show_context_menu.assert_called_once_with(10, 20)
        gesture.set_state.assert_called_once_with(backend.Gtk.EventSequenceState.CLAIMED)

    def test_gtk_context_menu_keyboard(self):
        backend = self.backend
        window = backend.GtkKittyWindow.__new__(backend.GtkKittyWindow)
        window._context_menu = Mock()
        window._context_menu_keys = set()
        window._context_menu.get_visible.return_value = True
        controller = Mock()
        with patch.object(backend, 'gtk_input_key') as input_key:
            self.assertFalse(window._key_pressed(controller, backend.Gdk.KEY_Escape, 9, 0))
            window._context_menu.get_visible.return_value = False
            self.assertFalse(window._key_released(controller, backend.Gdk.KEY_Escape, 9, 0))
        self.assertFalse(window._context_menu_keys)
        input_key.assert_not_called()
        controller.get_current_event.assert_not_called()

    def test_gtk_context_menu_actions(self):
        from kitty.boss import Boss

        boss = Boss.__new__(Boss)
        window = Mock(os_window_id=1)
        tab = window.tabref.return_value
        boss.window_id_map = {42: window}
        boss.os_window_map = {1: SimpleNamespace(active_tab=tab, _find_window_at=Mock(return_value=window))}
        boss.combine = Mock()
        boss.queue_gtk_render = Mock()
        window.id = 42
        window.has_selection.return_value = True
        self.ae(boss.gtk_context_menu_target(1, 10, 20), (42, True))
        tab.set_active_window.assert_called_once_with(window)

        boss.gtk_context_menu_action(1, 42, 'search')
        window.show_scrollback.return_value.send_key.assert_called_once_with('/')
        boss.gtk_context_menu_action(1, 42, 'copy')
        window.copy_to_clipboard.assert_called_once_with()
        window.text_for_selection.return_value = 'selected text'
        boss.gtk_context_menu_action(1, 42, 'paste')
        window.paste_with_actions.assert_called_once_with('selected text')
        boss.combine.assert_not_called()
        window.text_for_selection.return_value = ''
        boss.gtk_context_menu_action(1, 42, 'paste')
        boss.gtk_context_menu_action(1, 42, 'clear')
        boss.gtk_context_menu_action(1, 42, 'set-title')
        self.ae(boss.combine.call_args_list, [
            call('paste_from_clipboard', window), call('clear_terminal clear active', window), call('set_tab_title', window),
        ])

        # A menu must not act on a pane that closed, moved, or changed tabs.
        window.reset_mock()
        for os_window_id, window_id in ((1, 99), (2, 42)):
            boss.gtk_context_menu_action(os_window_id, window_id, 'copy')
        boss.os_window_map[1].active_tab = Mock()
        boss.gtk_context_menu_action(1, 42, 'copy')
        window.copy_to_clipboard.assert_not_called()

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
