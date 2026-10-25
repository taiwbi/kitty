# License: GPL v3 Copyright: 2026

import warnings
from types import SimpleNamespace
from unittest.mock import Mock

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
        window._scrollbar_sync_source = 0
        window._sync_scrollbar = Mock()

        window._area_rendered(None, None)
        source = window._scrollbar_sync_source
        window.close()
        self.ae(window._scrollbar_sync_source, 0)
        self.assertIsNone(backend.GLib.MainContext.default().find_source_by_id(source))
        window._sync_scrollbar.assert_not_called()
        window.window.close.assert_called_once_with()
