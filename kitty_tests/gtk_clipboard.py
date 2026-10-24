# License: GPL v3 Copyright: 2026

import warnings
from types import SimpleNamespace
from unittest.mock import patch

from .base import BaseTest


class TestGtkClipboard(BaseTest):
    def setUp(self):
        super().setUp()
        try:
            from kitty import gtk_clipboard
        except (ImportError, ValueError):
            self.skipTest('GTK 4 introspection is unavailable')
        self.backend = gtk_clipboard
        # PyGObject's GLib loop integration probes deprecated asyncio policies.
        warning_context = warnings.catch_warnings()
        warning_context.__enter__()
        self.addCleanup(warning_context.__exit__, None, None, None)
        warnings.filterwarnings('ignore', message=r"'asyncio\..*' is deprecated", category=DeprecationWarning)

    def test_gtk_clipboard_provider(self):
        from kitty.clipboard import Clipboard
        from kitty.fast_data_types import GLFW_CLIPBOARD, GLFW_PRIMARY_SELECTION

        backend = self.backend
        clipboard, primary = Clipboard(), Clipboard()
        clipboard.data = {'text/plain': b'clipboard', 'application/test': lambda: iter((b'one', b'two', b'')).__next__}
        primary.data = {'text/plain': b'primary'}
        boss = SimpleNamespace(clipboard=clipboard, primary_selection=primary)
        with patch.object(backend, 'get_boss', return_value=boss):
            providers = [backend.ClipboardProvider(c, tuple(s.data)) for c, s in (
                (GLFW_CLIPBOARD, clipboard), (GLFW_PRIMARY_SELECTION, primary)
            )]
        # A pending transfer must retain the offer even after another copy.
        clipboard.data = {'text/plain': b'replaced'}
        for provider, mime, expected in (
            (providers[0], 'text/plain', b'clipboard'),
            (providers[0], 'application/test', b'onetwo'),
            (providers[0], 'application/test', b'onetwo'),
            (providers[1], 'text/plain', b'primary'),
        ):
            self.assertIn(mime, provider.ref_formats().get_mime_types())
            self.assertIn(mime, provider.ref_storable_formats().get_mime_types())
            stream = backend.Gio.MemoryOutputStream.new_resizable()
            context = backend.GLib.MainContext.default()
            results = []

            def finished(source, result, _data):
                results.append(source.write_mime_type_finish(result))

            provider.write_mime_type_async(mime, stream, 0, None, finished, None)
            while not results:
                context.iteration(True)
            self.ae(results, [True])
            stream.close()
            self.ae(stream.steal_as_bytes().get_data(), expected)

    def test_gtk_clipboard_read(self):
        from kitty.clipboard import Clipboard
        from kitty.fast_data_types import GLFW_CLIPBOARD, GLFW_PRIMARY_SELECTION

        backend = self.backend
        payload = b'clipboard data\x00' * 20000

        class FakeClipboard:
            content = None

            def get_content(self):
                return self.content

            def set_content(self, provider):
                self.content = provider
                return True

            def get_formats(self):
                return backend.Gdk.ContentFormats.new(('text/plain', 'application/test'))

            def read_async(self, mimes, priority, cancellable, callback, data):
                self.mimes = mimes
                backend.GLib.idle_add(lambda: callback(self, None, data))

            def read_finish(self, result):
                return backend.Gio.MemoryInputStream.new_from_bytes(backend.GLib.Bytes.new(payload)), 'text/plain'

        regular, primary = FakeClipboard(), FakeClipboard()
        with patch.dict(backend._clipboards, {GLFW_CLIPBOARD: regular, GLFW_PRIMARY_SELECTION: primary}):
            for ctype in (GLFW_CLIPBOARD, GLFW_PRIMARY_SELECTION):
                parts = []
                backend.get_clipboard_mime(ctype, None, parts.append)
                self.ae(parts, [b'text/plain', b'application/test'])
                parts.clear()
                backend.get_clipboard_mime(ctype, 'text/plain', parts.append)
                self.ae(b''.join(parts), payload)
                self.assertGreater(len(parts), 1)
                parts.clear()
                backend.get_clipboard_mime(ctype, 'unavailable', parts.append)
                self.ae(parts, [])
            self.ae(regular.mimes, ['text/plain'])
            self.ae(primary.mimes, ['text/plain'])
            source = Clipboard()
            source.data = {'text/plain': b'local'}
            with patch.object(backend, 'get_boss', return_value=SimpleNamespace(clipboard=source)):
                backend.set_clipboard_data_types(GLFW_CLIPBOARD, ('text/plain',))
            with self.assertRaisesRegex(RuntimeError, '^is_self_offer$'):
                backend.get_clipboard_mime(GLFW_CLIPBOARD, 'text/plain', parts.append)
            backend.set_clipboard_data_types(GLFW_CLIPBOARD, ())
            self.assertIsNone(regular.content)
