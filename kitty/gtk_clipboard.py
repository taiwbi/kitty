# License: GPL v3 Copyright: 2026

"""Clipboard access on the same GDK display as kitty's GTK windows."""

from collections.abc import Callable
from copy import copy
from time import monotonic
from typing import Any

import gi

gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib  # type: ignore[import-not-found]

from .fast_data_types import GLFW_PRIMARY_SELECTION, get_boss


class ClipboardProvider(Gdk.ContentProvider):
    def __init__(self, ctype: int, mime_types: tuple[str, ...]) -> None:
        super().__init__()
        source = get_boss().primary_selection if ctype == GLFW_PRIMARY_SELECTION else get_boss().clipboard
        self.source = copy(source)
        self.source.data = dict(source.data)
        self.formats = Gdk.ContentFormats.new(mime_types)

    def do_ref_formats(self) -> Any:
        return self.formats

    def do_ref_storable_formats(self) -> Any:
        return self.formats

    def do_write_mime_type_async(
        self, mime: str, stream: Any, priority: int, cancellable: Any, callback: Any, user_data: Any
    ) -> None:
        task = Gio.Task.new(self, cancellable, callback, user_data)
        chunker: Callable[[], bytes] | None = None

        def write_next() -> None:
            nonlocal chunker
            try:
                if chunker is None:
                    chunker = self.source(mime)
                chunk = chunker()
                if chunk:
                    # Keep the bytes alive until the asynchronous write finishes.
                    stream.write_all_async(chunk, priority, cancellable, written, chunk)
                else:
                    task.return_boolean(True)
            except Exception as err:
                task.return_error(err if isinstance(err, GLib.Error) else GLib.Error.new_literal(
                    Gio.io_error_quark(), str(err), Gio.IOErrorEnum.FAILED
                ))

        def written(output: Any, result: Any, _data: Any) -> None:
            try:
                output.write_all_finish(result)
            except GLib.Error as err:
                task.return_error(err)
            else:
                write_next()

        write_next()

    def do_write_mime_type_finish(self, result: Any) -> bool:
        return bool(result.propagate_boolean())


_clipboards: dict[int, Any] = {}


def clipboard_for(ctype: int) -> Any:
    if ctype not in _clipboards:
        display = Gdk.Display.get_default()
        clipboard = display.get_primary_clipboard() if ctype == GLFW_PRIMARY_SELECTION else display.get_clipboard()
        owned = False

        def changed(clipboard: Any) -> None:
            nonlocal owned
            was_owned = owned
            owned = isinstance(clipboard.get_content(), ClipboardProvider)
            if was_owned and not owned:
                get_boss().on_clipboard_lost('primary' if ctype == GLFW_PRIMARY_SELECTION else 'clipboard')

        clipboard.connect('changed', changed)
        _clipboards[ctype] = clipboard
    return _clipboards[ctype]


def set_clipboard_data_types(ctype: int, mime_types: tuple[str, ...]) -> None:
    clipboard = clipboard_for(ctype)
    if not clipboard.set_content(ClipboardProvider(ctype, mime_types) if mime_types else None):
        raise RuntimeError('GDK could not set clipboard content')


def get_clipboard_mime(ctype: int, mime: str | None, output: Callable[[bytes], None]) -> None:
    clipboard = clipboard_for(ctype)
    if isinstance(clipboard.get_content(), ClipboardProvider):
        # Let Clipboard use its original data, including lazy chunkers.
        raise RuntimeError('is_self_offer')
    if mime is None:
        for available in clipboard.get_formats().get_mime_types():
            output(available.encode('utf-8'))
        return
    if not clipboard.get_formats().contain_mime_type(mime):
        return

    # Kitty's clipboard API is synchronous. Dispatch GDK's async transfer on the
    # GTK context so the display and the supplying application can make progress.
    context = GLib.MainContext.default()
    finished = False
    cancellable = Gio.Cancellable.new()
    errors: list[Exception] = []
    last_progress = monotonic()

    def check_timeout() -> bool:
        if monotonic() - last_progress >= 2:
            cancellable.cancel()
        return True

    def read_chunk(stream: Any, result: Any, _data: Any) -> None:
        nonlocal finished, last_progress
        try:
            data = stream.read_bytes_finish(result).get_data()
            if data:
                last_progress = monotonic()
                output(data)
                stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, cancellable, read_chunk, None)
                return
        except Exception as err:
            errors.append(err)
        stream.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
        finished = True

    def opened(source: Any, result: Any, _data: Any) -> None:
        nonlocal finished
        try:
            stream, _mime = source.read_finish(result)
            stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, cancellable, read_chunk, None)
        except Exception as err:
            errors.append(err)
            finished = True

    clipboard.read_async([mime], GLib.PRIORITY_DEFAULT, cancellable, opened, None)
    timeout = GLib.timeout_add(1000, check_timeout)
    try:
        while not finished:
            context.iteration(True)
    finally:
        GLib.source_remove(timeout)
        cancellable.cancel()
    if errors:
        raise RuntimeError(f'GDK clipboard read failed: {errors[0]}') from errors[0]
