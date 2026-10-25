# License: GPL v3 Copyright: 2026

import warnings
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        clipboard.data = {
            'text/plain': b'clipboard',
            'application/test': lambda: iter((b'one', b'two', b'')).__next__,
            'application/empty': b'',
        }
        primary.data = {'text/plain': b'primary', 'text/plain;charset=utf-8': b'explicit alias'}
        boss = SimpleNamespace(clipboard=clipboard, primary_selection=primary)
        with patch.object(backend, 'get_boss', return_value=boss):
            providers = [backend.content_provider_for(c, tuple(s.data)) for c, s in ((GLFW_CLIPBOARD, clipboard), (GLFW_PRIMARY_SELECTION, primary))]
        # A pending transfer must retain the offer even after another copy.
        clipboard.data = {'text/plain': b'replaced'}
        for provider, mime, expected in (
            (providers[0], 'text/plain', b'clipboard'),
            (providers[0], 'text/plain;charset=utf-8', b'clipboard'),
            (providers[0], 'application/test', b'onetwo'),
            (providers[0], 'application/test', b'onetwo'),
            (providers[0], 'application/empty', b''),
            (providers[1], 'text/plain', b'primary'),
            (providers[1], 'text/plain;charset=utf-8', b'explicit alias'),
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

    def test_gtk_clipboard_native_callback(self):
        import ctypes
        from ctypes.util import find_library

        from kitty.clipboard import Clipboard
        from kitty.fast_data_types import GLFW_CLIPBOARD

        backend = self.backend
        library = find_library('gtk-4')
        if library is None:
            self.skipTest('GTK shared library is unavailable')
        gtk = ctypes.CDLL(library)
        pointer = ctypes.pythonapi.PyCapsule_GetPointer
        pointer.argtypes = (ctypes.py_object, ctypes.c_char_p)
        pointer.restype = ctypes.c_void_p
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        write = gtk.gdk_content_provider_write_mime_type_async
        write.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, callback_type, ctypes.c_void_p)
        write.restype = None
        finish = gtk.gdk_content_provider_write_mime_type_finish
        finish.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        finish.restype = ctypes.c_int

        source = Clipboard()
        payload = 'Copied from kitty: سلام\n'.encode() * 10000
        source.data = {'text/plain': payload}
        with patch.object(backend, 'get_boss', return_value=SimpleNamespace(clipboard=source)):
            provider = backend.content_provider_for(GLFW_CLIPBOARD, tuple(source.data))
        stream = backend.Gio.MemoryOutputStream.new_resizable()
        self.addCleanup(stream.close)
        results = []
        user_data = ctypes.c_int(42)
        address = ctypes.addressof(user_data)

        @callback_type
        def finished(source, result, data):
            results.append((data, finish(source, result, None)))

        # A Python caller hides the callback context inside a GI closure. Call
        # from C like GDK does when serving another app, with native user_data.
        write(pointer(provider.__gpointer__, None), b'text/plain', pointer(stream.__gpointer__, None), 0, None, finished, address)
        context = backend.GLib.MainContext.default()
        timeout = backend.GLib.timeout_add(2000, lambda: results.append(('timeout', False)) or False)
        try:
            while not results:
                context.iteration(True)
        finally:
            if context.find_source_by_id(timeout) is not None:
                backend.GLib.source_remove(timeout)
        self.ae(results, [(address, True)])
        stream.close()
        self.ae(stream.steal_as_bytes().get_data(), payload)

    def test_gtk_clipboard_read(self):
        from kitty.clipboard import Clipboard
        from kitty.fast_data_types import GLFW_CLIPBOARD, GLFW_PRIMARY_SELECTION

        backend = self.backend
        payload = b'clipboard data\x00' * 20000

        class FakeClipboard:
            content = None

            def get_content(self):
                return self.content

            def is_local(self):
                return self.content is not None

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

    def test_gtk_clipboard_ownership(self):
        from kitty.fast_data_types import GLFW_CLIPBOARD, GLFW_PRIMARY_SELECTION

        backend = self.backend
        regular, primary = Mock(), Mock()
        display = SimpleNamespace(get_clipboard=lambda: regular, get_primary_clipboard=lambda: primary)
        boss = SimpleNamespace(on_clipboard_lost=Mock())
        with (
            patch.dict(backend._clipboards, {}, clear=True),
            patch.object(backend.Gdk.Display, 'get_default', return_value=display),
            patch.object(backend, 'get_boss', return_value=boss),
        ):
            for ctype, clipboard, name in (
                (GLFW_CLIPBOARD, regular, 'clipboard'),
                (GLFW_PRIMARY_SELECTION, primary, 'primary'),
            ):
                boss.on_clipboard_lost.reset_mock()
                self.assertIs(backend.clipboard_for(ctype), clipboard)
                self.assertIs(backend.clipboard_for(ctype), clipboard)
                clipboard.connect.assert_called_once()
                signal, changed = clipboard.connect.call_args.args
                self.ae(signal, 'changed')
                clipboard.is_local.return_value = True
                changed(clipboard)
                changed(clipboard)  # Replacing a local offer retains ownership.
                boss.on_clipboard_lost.assert_not_called()
                clipboard.is_local.return_value = False
                changed(clipboard)
                changed(clipboard)
                boss.on_clipboard_lost.assert_called_once_with(name)
