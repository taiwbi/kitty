# License: GPL v3 Copyright: 2026

"""Clipboard access on the same GDK display as kitty's GTK windows."""

from collections.abc import Callable
from time import monotonic
from typing import Any

import gi

from .fast_data_types import GLFW_PRIMARY_SELECTION, get_boss

gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib  # type: ignore[import-not-found]  # noqa: E402


def content_provider_for(ctype: int, mime_types: tuple[str, ...]) -> Any:
    source = get_boss().primary_selection if ctype == GLFW_PRIMARY_SELECTION else get_boss().clipboard
    providers = []
    for mime in mime_types:
        chunker = source(mime)
        data = GLib.Bytes.new(b''.join(iter(chunker, b'')))
        providers.append(Gdk.ContentProvider.new_for_bytes(mime, data))
        if mime == 'text/plain' and 'text/plain;charset=utf-8' not in mime_types:
            providers.append(Gdk.ContentProvider.new_for_bytes('text/plain;charset=utf-8', data))
    # Keep the transfer entirely in GDK: PyGObject does not preserve native
    # callback user_data when forwarding the async provider vfunc to Gio.Task.
    # GDK's byte providers also retain this offer when another copy replaces it.
    return Gdk.ContentProvider.new_union(providers)


_clipboards: dict[int, Any] = {}


def clipboard_for(ctype: int) -> Any:
    if ctype not in _clipboards:
        display = Gdk.Display.get_default()
        clipboard = display.get_primary_clipboard() if ctype == GLFW_PRIMARY_SELECTION else display.get_clipboard()
        owned = False

        def changed(clipboard: Any) -> None:
            nonlocal owned
            was_owned = owned
            owned = clipboard.is_local()
            if was_owned and not owned:
                get_boss().on_clipboard_lost('primary' if ctype == GLFW_PRIMARY_SELECTION else 'clipboard')

        clipboard.connect('changed', changed)
        _clipboards[ctype] = clipboard
    return _clipboards[ctype]


def set_clipboard_data_types(ctype: int, mime_types: tuple[str, ...]) -> None:
    clipboard = clipboard_for(ctype)
    if not clipboard.set_content(content_provider_for(ctype, mime_types) if mime_types else None):
        raise RuntimeError('GDK could not set clipboard content')


def get_clipboard_mime(ctype: int, mime: str | None, output: Callable[[bytes], None]) -> None:
    clipboard = clipboard_for(ctype)
    if clipboard.is_local():
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
