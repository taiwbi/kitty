# License: GPL v3 Copyright: 2026

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .base import BaseTest


class TestStartup(BaseTest):
    def test_first_gtk_window_callback(self):
        from kitty import main
        from kitty.cli import create_opts, parse_args

        args, args.args = parse_args(['--config', 'NONE'])
        opts = create_opts(args)
        window = Mock()
        backend = SimpleNamespace(GtkKittyWindow=Mock(return_value=window), GtkKittyEvents=Mock())
        first_window_callback = Mock()
        boss = Mock(misc_config_errors=[])
        runner = main.AppRunner()
        runner.gtk_window_enabled = True
        runner.first_window_callback = first_window_callback

        def create_window(get_size, callback, *a, **kw):
            # Match the native window creation contract: the notification
            # wrapper must pass the actual GTK window back to C.
            self.assertIs(callback(123), window)
            self.assertIs(runner.pending_gtk_window, window)
            window.apply_chrome_mode.assert_called_once_with(opts.hide_window_decorations)
            return 42

        with (
            patch.dict('sys.modules', {'kitty.gtk_window': backend}),
            patch.object(main, 'run_app', runner),
            patch.object(main, 'is_macos', False),
            patch.object(main, 'is_wayland', return_value=True),
            patch.object(main, 'set_window_icon'),
            patch.object(main, 'logout_snapshot_to_restore', return_value=None),
            patch.object(main, 'cached_values_for', return_value=nullcontext({})),
            patch.object(main, 'create_os_window', side_effect=create_window),
            patch.object(main, 'Boss', return_value=boss),
            patch('kitty.utils.init_startup_notification', return_value=None),
        ):
            main._run_app(opts, args)

        first_window_callback.assert_called_once_with(123)
        self.assertIs(runner.first_window_callback, first_window_callback)
        boss.register_gtk_window.assert_called_once_with(42, window)
        boss.start.assert_called_once()
        boss.child_monitor.main_loop.assert_called_once_with()
        boss.destroy.assert_called_once_with()
