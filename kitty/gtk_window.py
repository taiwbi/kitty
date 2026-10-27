# License: GPL v3 Copyright: 2026

"""GTK/libadwaita window chrome used by the optional GNOME backend.

The tab strip is backed by Adw.TabView so it gets the same sizing, overflow,
accessibility and tab styling as other libadwaita applications. Kitty remains
the owner of tab state; this class mirrors that state into GTK and forwards
user actions through callbacks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from weakref import ReferenceType, ref

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk  # type: ignore[import-not-found]

from .constants import appname, is_wayland
from .fast_data_types import (
    GLFW_FKEY_BACKSPACE,
    GLFW_FKEY_CAPS_LOCK,
    GLFW_FKEY_DELETE,
    GLFW_FKEY_DOWN,
    GLFW_FKEY_END,
    GLFW_FKEY_ENTER,
    GLFW_FKEY_ESCAPE,
    GLFW_FKEY_F1,
    GLFW_FKEY_F10,
    GLFW_FKEY_F11,
    GLFW_FKEY_F12,
    GLFW_FKEY_F13,
    GLFW_FKEY_F14,
    GLFW_FKEY_F15,
    GLFW_FKEY_F16,
    GLFW_FKEY_F17,
    GLFW_FKEY_F18,
    GLFW_FKEY_F19,
    GLFW_FKEY_F2,
    GLFW_FKEY_F20,
    GLFW_FKEY_F21,
    GLFW_FKEY_F22,
    GLFW_FKEY_F23,
    GLFW_FKEY_F24,
    GLFW_FKEY_F25,
    GLFW_FKEY_F26,
    GLFW_FKEY_F27,
    GLFW_FKEY_F28,
    GLFW_FKEY_F29,
    GLFW_FKEY_F3,
    GLFW_FKEY_F30,
    GLFW_FKEY_F31,
    GLFW_FKEY_F32,
    GLFW_FKEY_F33,
    GLFW_FKEY_F34,
    GLFW_FKEY_F35,
    GLFW_FKEY_F4,
    GLFW_FKEY_F5,
    GLFW_FKEY_F6,
    GLFW_FKEY_F7,
    GLFW_FKEY_F8,
    GLFW_FKEY_F9,
    GLFW_FKEY_HOME,
    GLFW_FKEY_INSERT,
    GLFW_FKEY_LEFT_ALT,
    GLFW_FKEY_LEFT_CONTROL,
    GLFW_FKEY_LEFT_HYPER,
    GLFW_FKEY_LEFT_META,
    GLFW_FKEY_LEFT_SHIFT,
    GLFW_FKEY_LEFT_SUPER,
    GLFW_FKEY_LEFT,
    GLFW_FKEY_NUM_LOCK,
    GLFW_FKEY_PAGE_DOWN,
    GLFW_FKEY_PAGE_UP,
    GLFW_FKEY_PAUSE,
    GLFW_FKEY_PRINT_SCREEN,
    GLFW_FKEY_RIGHT,
    GLFW_FKEY_RIGHT_ALT,
    GLFW_FKEY_RIGHT_CONTROL,
    GLFW_FKEY_RIGHT_HYPER,
    GLFW_FKEY_RIGHT_META,
    GLFW_FKEY_RIGHT_SHIFT,
    GLFW_FKEY_RIGHT_SUPER,
    GLFW_FKEY_SCROLL_LOCK,
    GLFW_FKEY_TAB,
    GLFW_FKEY_UP,
    GLFW_IME_COMMIT_TEXT,
    GLFW_IME_PREEDIT_CHANGED,
    GLFW_MOD_ALT,
    GLFW_MOD_CAPS_LOCK,
    GLFW_MOD_CONTROL,
    GLFW_MOD_HYPER,
    GLFW_MOD_META,
    GLFW_MOD_SHIFT,
    GLFW_MOD_SUPER,
    GLFW_PRESS,
    GLFW_RELEASE,
    GLFW_REPEAT,
    GLFW_MOUSE_BUTTON_1,
    GLFW_MOUSE_BUTTON_2,
    GLFW_MOUSE_BUTTON_3,
    gtk_backend_activate,
    gtk_backend_available,
    gtk_backend_clear_window_blur,
    gtk_backend_has_wayland_blur,
    gtk_backend_initialize,
    gtk_backend_set_window_app_id,
    gtk_backend_set_window_blur,
    gtk_input_close,
    gtk_input_focus,
    gtk_input_key,
    gtk_input_mouse_button,
    gtk_input_pointer,
    gtk_input_resize,
    gtk_input_scroll,
    gtk_render_os_window,
)
from .tab_bar import TabBarData


# Prefer kitty's region protocol whenever it was compiled in. GTK's CSS
# backdrop filter cannot be constrained to the visible CSD window allocation.
_use_gtk_css_wayland_blur = (
    Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()
) >= (4, 23, 3) and not gtk_backend_has_wayland_blur()

if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
    raise RuntimeError('kitty GTK backend requires libadwaita 1.4 or newer')


PageWidgetFactory = Callable[[int], Gtk.Widget]
TabAction = Callable[[int], None]
_gtk_initialized = False
_gtk_chrome_css: Gtk.CssProvider | None = None
_shared_gl_context: ReferenceType[Any] | None = None


def initialize_gtk() -> None:
    global _gtk_initialized
    if not _gtk_initialized:
        if not gtk_backend_available():
            raise RuntimeError('kitty was built without GTK 4 and libadwaita support')
        GLib.set_prgname("com.taiwbi.kitty")
        gtk_backend_initialize()
        _gtk_initialized = True


class GtkKittyEvents:
    """Bridge GTK events and frames to kitty's existing OS-window handlers."""

    def __init__(self) -> None:
        self.os_window_id = 0

    def bind(self, os_window_id: int) -> None:
        self.os_window_id = os_window_id

    def gl_error(self, error: Any) -> None:
        raise RuntimeError(f'GTK could not create an OpenGL context: {error.message}')

    def gl_realized(self, context: Any) -> None:
        global _shared_gl_context
        shared = _shared_gl_context() if _shared_gl_context is not None else None
        if shared is None:
            _shared_gl_context = ref(context)
        elif not context.is_shared(shared):
            raise RuntimeError('GTK did not share OpenGL resources between kitty windows')

    def gl_unrealized(self) -> None:
        pass

    def resized(self, width: int, height: int, scale: int) -> None:
        if self.os_window_id and width > 0 and height > 0:
            gtk_input_resize(self.os_window_id, width, height, scale)

    def render(self, _context: Any) -> bool:
        if self.os_window_id:
            gtk_render_os_window(self.os_window_id)
        return True

    def close_requested(self) -> bool:
        if self.os_window_id:
            gtk_input_close(self.os_window_id)
        return True

    def focus_changed(self, focused: bool) -> None:
        if self.os_window_id:
            gtk_input_focus(self.os_window_id, focused)


class GtkTabHeaderBar:
    """A one-row libadwaita header bar mirroring one kitty TabManager.

    ``page_widget_factory`` lets the GTK window backend attach a GtkGLArea to
    each AdwTabPage. The default empty page is useful while a window is being
    assembled, before the renderer has created its area.
    """

    def __init__(
        self,
        *,
        on_new_tab: Callable[[], None],
        on_select_tab: TabAction,
        on_close_tab: TabAction,
        on_reorder_tabs: Callable[[Sequence[int]], None],
        page_widget_factory: PageWidgetFactory | None = None,
    ) -> None:
        self._on_new_tab = on_new_tab
        self._on_select_tab = on_select_tab
        self._on_close_tab = on_close_tab
        self._on_reorder_tabs = on_reorder_tabs
        self._page_widget_factory = page_widget_factory or self._empty_page
        self._pages: dict[int, Adw.TabPage] = {}
        self._syncing = False

        self.tab_view = Adw.TabView.new()
        self.tab_bar = Adw.TabBar.new()
        self.tab_bar.add_css_class('kitty-tabbar')
        self.tab_bar.add_css_class('inline')
        self.tab_bar.set_view(self.tab_view)
        # Kitty's tab_bar_min_tabs decides whether the row is shown. Once shown,
        # keep the add button visible even when the window has only one tab.
        self.tab_bar.set_autohide(False)
        self.tab_bar.set_hexpand(True)
        self.tab_bar.set_expand_tabs(True)
        self.tab_bar.set_halign(Gtk.Align.FILL)

        self.new_tab_button = Gtk.Button.new()
        self.new_tab_button.add_css_class('flat')
        # Draw the simple symbolic glyph with the current theme foreground.
        # The frozen runtime does not ship files beside Python modules, and
        # SVG decoding additionally requires external pixbuf loaders.
        icon = Gtk.DrawingArea()
        icon.set_content_width(16)
        icon.set_content_height(16)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_draw_func(self._draw_new_tab_icon)
        self.new_tab_button.set_child(icon)
        self.new_tab_button.set_tooltip_text('New Tab')
        self.new_tab_button.update_property([Gtk.AccessibleProperty.LABEL], ['New Tab'])
        self.new_tab_button.connect('clicked', self._new_tab_clicked)
        self.tab_bar.set_start_action_widget(self.new_tab_button)

        self.header_bar = Adw.HeaderBar.new()
        self.header_bar.add_css_class('kitty-headerbar')
        self.header_bar.add_css_class('flat')
        self.header_bar.set_show_start_title_buttons(False)
        self.header_bar.set_show_end_title_buttons(False)
        # HeaderBar balances the title slot against its side controls, leaving
        # a matching empty area on the left. Put the native window controls in
        # the expanding row itself so tabs use all space up to those controls.
        self.window_controls = Gtk.WindowControls.new(Gtk.PackType.END)
        self.window_controls.set_valign(Gtk.Align.CENTER)
        self.title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.title_row.set_hexpand(True)
        self.title_row.append(self.tab_bar)
        self.title_row.append(self.window_controls)
        self.header_bar.set_title_widget(self.title_row)

        # Widget-local providers cannot style descendants. Install these
        # class-scoped rules on the display so they reach the internal boxes,
        # action widgets and tabs, including widgets created after startup.
        # Remove the outer header inset; retain native tab/action padding and
        # the translucent selected-tab background inside the transparent strip.
        global _gtk_chrome_css
        if _gtk_chrome_css is None:
            _gtk_chrome_css = Gtk.CssProvider()
            _gtk_chrome_css.load_from_data(b'''
                headerbar.kitty-headerbar {
                    min-height: 42px;
                    padding: 0;
                    padding-right: 7px;
                    margin: 0;
                    border: none;
                    box-shadow: none;
                }
                headerbar.kitty-headerbar > windowhandle > box,
                .kitty-tabbar {
                    padding: 0;
                }
                headerbar.kitty-headerbar box.start,
                headerbar.kitty-headerbar box.end {
                    margin: 0;
                    border-spacing: 0;
                }
                headerbar.kitty-headerbar > windowhandle > box .kitty-tabbar > revealer > box,
                tabbar.kitty-tabbar .box {
                    background: 0;
                    padding: 0;
                    border: none;
                    box-shadow: none;
                }
                tabbar.kitty-tabbar tab:selected .tab-title {
                    font-weight: bold;
                }
            ''')
            Gtk.StyleContext.add_provider_for_display(
                self.header_bar.get_display(), _gtk_chrome_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.tab_view.connect('notify::selected-page', self._selected_page_changed)
        self.tab_view.connect('close-page', self._close_page_requested)
        self.tab_view.connect('page-reordered', self._page_reordered)

    @staticmethod
    def _draw_new_tab_icon(area: Gtk.DrawingArea, cr: Any, width: int, height: int) -> None:
        color = area.get_style_context().get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.rectangle(width / 2 - 1, height / 2 - 6, 2, 12)
        cr.rectangle(width / 2 - 6, height / 2 - 1, 12, 2)
        cr.fill()

    @staticmethod
    def _empty_page(_tab_id: int) -> Gtk.Widget:
        return Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    def set_visible(self, visible: bool) -> None:
        """Show or hide the entire GTK titlebar, preserving its parent window."""
        self.header_bar.set_visible(visible)

    def set_actions(
        self,
        *,
        on_new_tab: Callable[[], None],
        on_select_tab: TabAction,
        on_close_tab: TabAction,
        on_reorder_tabs: Callable[[Sequence[int]], None],
    ) -> None:
        self._on_new_tab = on_new_tab
        self._on_select_tab = on_select_tab
        self._on_close_tab = on_close_tab
        self._on_reorder_tabs = on_reorder_tabs

    def _new_tab_clicked(self, _button: Gtk.Button) -> None:
        self._on_new_tab()

    def _selected_page_changed(self, view: Adw.TabView, _pspec: Any) -> None:
        if self._syncing:
            return
        tab_id = self._tab_id_for_page(view.get_selected_page())
        if tab_id is not None:
            self._on_select_tab(tab_id)

    def _close_page_requested(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        tab_id = self._tab_id_for_page(page)
        if self._syncing:
            view.close_page_finish(page, True)
        else:
            if tab_id is not None:
                self._on_close_tab(tab_id)
            # Kitty confirms the close through its normal close policy. Keep
            # the page until the next model synchronization reflects the result.
            view.close_page_finish(page, False)
        return True

    def _tab_id_for_page(self, page: Adw.TabPage | None) -> int | None:
        if page is None:
            return None
        for tab_id, candidate in self._pages.items():
            if candidate is page or candidate == page:
                return tab_id
        return None

    def _set_close_buttons_visible(self, visible: bool) -> None:
        pending = [self.tab_bar]
        while pending:
            widget = pending.pop()
            if widget.has_css_class('tab-close-button'):
                widget.set_visible(visible)
            child = widget.get_first_child()
            while child is not None:
                pending.append(child)
                child = child.get_next_sibling()

    def _page_reordered(self, _view: Adw.TabView, _page: Adw.TabPage, _position: int) -> None:
        if not self._syncing:
            order = (self._tab_id_for_page(self.tab_view.get_nth_page(i)) for i in range(self.tab_view.get_n_pages()))
            self._on_reorder_tabs(tuple(tab_id for tab_id in order if tab_id is not None))

    def sync_tabs(self, tabs: Sequence[TabBarData], active_tab_id: int) -> None:
        """Mirror kitty's visible tabs into the libadwaita tab view."""
        # Kitty can append a synthetic '+' entry for its own renderer. The GTK
        # headerbar always owns exactly one real new-tab button instead.
        wanted = tuple(tab for tab in tabs if tab.tab_id >= 0 and tab.title != '+')
        wanted_ids = {tab.tab_id for tab in wanted}

        self._syncing = True
        try:
            for tab_id, page in tuple(self._pages.items()):
                if tab_id not in wanted_ids:
                    self.tab_view.close_page(page)
                    self._pages.pop(tab_id, None)

            for position, tab in enumerate(wanted):
                page = self._pages.get(tab.tab_id)
                if page is None:
                    child = self._page_widget_factory(tab.tab_id)
                    page = self.tab_view.insert(child, position)
                    self._pages[tab.tab_id] = page
                else:
                    self.tab_view.reorder_page(page, position)
                page.set_title(tab.title)
                page.set_needs_attention(tab.needs_attention)
                if tab.is_active or tab.tab_id == active_tab_id:
                    self.tab_view.set_selected_page(page)

            selected = self._pages.get(active_tab_id)
            if selected is not None:
                self.tab_view.set_selected_page(selected)
            self._set_close_buttons_visible(len(wanted) > 1)
        finally:
            self._syncing = False


class GtkKittyWindow:
    """GTK4/libadwaita toplevel shell for one kitty OS window.

    The GTK window owns the CSD frame and the GLArea. Platform events are
    forwarded through ``events`` so the kitty core can retain its existing
    terminal and tab state machines while the native surface is GTK-owned.
    """

    def __init__(
        self,
        title: str,
        initial_size: tuple[int, int],
        *,
        application_id: str = appname,
        events: Any,
        on_new_tab: Callable[[], None],
        on_select_tab: TabAction,
        on_close_tab: TabAction,
        on_reorder_tabs: Callable[[Sequence[int]], None],
        page_widget_factory: PageWidgetFactory | None = None,
    ) -> None:
        initialize_gtk()

        self.application_id = "com.taiwbi.kitty" if application_id == appname else application_id
        self.window = Gtk.Window.new()
        self.window.set_title(title)
        self.window.set_default_size(*initial_size)
        self.window.set_focusable(True)
        self.window.add_css_class('kitty-window')

        self.tab_chrome = GtkTabHeaderBar(
            on_new_tab=on_new_tab,
            on_select_tab=on_select_tab,
            on_close_tab=on_close_tab,
            on_reorder_tabs=on_reorder_tabs,
            page_widget_factory=page_widget_factory,
        )
        self.window.set_titlebar(self.tab_chrome.header_bar)

        self.gl_area = Gtk.GLArea.new()
        self.gl_area.set_use_es(False)
        self.gl_area.set_required_version(3, 3)
        self.gl_area.set_has_depth_buffer(False)
        self.gl_area.set_has_stencil_buffer(False)
        self.gl_area.set_auto_render(False)
        self.gl_area.set_hexpand(True)
        self.gl_area.set_vexpand(True)
        self.gl_area.set_focusable(True)
        self.content = Gtk.Overlay.new()
        # Gtk.Align.END follows text direction, which can move the scrollbar
        # to the physical left edge in RTL locales. Terminal chrome is always
        # laid out left-to-right, even when the desktop locale is RTL.
        self.content.set_direction(Gtk.TextDirection.LTR)
        self.content.set_child(self.gl_area)
        self.scrollbar_adjustment = Gtk.Adjustment.new(0, 0, 1, 1, 1, 1)
        self.scrollbar = Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, self.scrollbar_adjustment)
        self.scrollbar.set_direction(Gtk.TextDirection.LTR)
        self.scrollbar.set_halign(Gtk.Align.END)
        self.scrollbar.set_valign(Gtk.Align.FILL)
        self.scrollbar.set_margin_top(4)
        self.scrollbar.set_margin_bottom(4)
        self.scrollbar.set_visible(False)
        self.scrollbar_adjustment.connect('value-changed', self._scrollbar_value_changed)
        self.content.add_overlay(self.scrollbar)
        self.window.set_child(self.content)

        self.events = events
        self.os_window_id = 0
        self._programmatic_close = False
        self._gtk_scale = 1
        self._background_blur = 0
        self._tab_bar_visible = True
        self._cursor_name = 'default'
        self._cursor_hidden = False
        self._pressed_keys: set[int] = set()
        self._ime_filtered_keys: set[int] = set()
        self._scrollbar_state_getter: Callable[[], tuple[float, int, int, bool, str, bool]] | None = None
        self._scrollbar_scroll_handler: Callable[[float], None] | None = None
        self._scrollbar_state: tuple[float, int, int, bool, str, bool] = (0, 0, 0, False, 'never', False)
        self._scrollbar_hovered = False
        self._syncing_scrollbar = False
        self._scrollbar_sync_source = 0
        self._scrollbar_adjustment_key: tuple[float, int, int, int, int, int] | None = None
        self.im_context = Gtk.IMMulticontext.new()
        self.im_context.set_client_widget(self.gl_area)
        self.im_context.connect('commit', self._ime_commit)
        self.im_context.connect('preedit-changed', self._ime_preedit_changed)
        self.gl_area.connect('realize', self._area_realized)
        self.gl_area.connect('unrealize', self._area_unrealized)
        self.gl_area.connect('resize', self._area_resized)
        self.gl_area.connect('render', self._area_rendered)
        self.window.connect('realize', self._window_realized)
        self.window.connect('unrealize', self._window_unrealized)
        self.window.connect('close-request', self._close_requested)
        self.window.connect('notify::is-active', self._focus_changed)
        self._install_input_controllers()

    def _install_input_controllers(self) -> None:
        key = Gtk.EventControllerKey.new()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect('key-pressed', self._key_pressed)
        key.connect('key-released', self._key_released)
        # Attach at the toplevel so typing and kitty shortcuts still reach the
        # terminal if GTK has not assigned keyboard focus directly to GLArea.
        self.window.add_controller(key)
        # Keep a focused-widget controller as well. GTK may dispatch directly
        # to GLArea on some backends; the capture controller consumes events
        # first when the toplevel path is used.
        area_key = Gtk.EventControllerKey.new()
        area_key.connect('key-pressed', self._key_pressed)
        area_key.connect('key-released', self._key_released)
        self.gl_area.add_controller(area_key)

        click = Gtk.GestureClick.new()
        click.set_button(0)
        click.connect('pressed', self._pointer_pressed)
        click.connect('released', self._pointer_released)
        self.gl_area.add_controller(click)

        motion = Gtk.EventControllerMotion.new()
        motion.connect('motion', self._pointer_moved)
        motion.connect('enter', self._pointer_entered)
        motion.connect('leave', self._pointer_left)
        self.gl_area.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect('scroll', self._scrolled)
        self.gl_area.add_controller(scroll)

        # This capture controller sees pointer motion over both the GL area and
        # its overlay scrollbar, so visibility policies can use the right edge
        # as the hover target even while the scrollbar itself is hidden.
        scrollbar_motion = Gtk.EventControllerMotion.new()
        scrollbar_motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scrollbar_motion.connect('enter', self._scrollbar_pointer_moved)
        scrollbar_motion.connect('motion', self._scrollbar_pointer_moved)
        scrollbar_motion.connect('leave', self._scrollbar_pointer_left)
        self.content.add_controller(scrollbar_motion)

    def _area_realized(self, area: Gtk.GLArea) -> None:
        area.make_current()
        error = area.get_error()
        if error is not None:
            self.events.gl_error(error)
        else:
            self.events.gl_realized(area.get_context())

    def _area_unrealized(self, _area: Gtk.GLArea) -> None:
        self.events.gl_unrealized()

    def _window_realized(self, _window: Gtk.Window) -> None:
        gtk_backend_set_window_app_id(self.window.__gpointer__, self.application_id)
        self._apply_wayland_blur()

    def _window_unrealized(self, _window: Gtk.Window) -> None:
        if self.os_window_id:
            gtk_backend_clear_window_blur(self.os_window_id)

    def _area_resized(self, _area: Gtk.GLArea, width: int, height: int) -> None:
        self._gtk_scale = max(1, self.gl_area.get_scale_factor())
        if self.os_window_id and width > 0 and height > 0:
            gtk_input_resize(self.os_window_id, width, height, self._gtk_scale)
            self._apply_wayland_blur()

    def _area_rendered(self, _area: Gtk.GLArea, context: Any) -> bool:
        rendered = bool(self.events.render(context))
        # GLArea renders during GTK's snapshot phase. Changing the adjustment
        # or visibility here invalidates the scrollbar's allocation while GTK
        # is drawing it. Sync after the snapshot so GTK can lay it out first.
        if not self._scrollbar_sync_source:
            self._scrollbar_sync_source = GLib.idle_add(self._sync_scrollbar_after_render)
        return rendered

    def _sync_scrollbar_after_render(self) -> bool:
        self._scrollbar_sync_source = 0
        self._sync_scrollbar()
        return GLib.SOURCE_REMOVE

    def _close_requested(self, _window: Gtk.Window) -> bool:
        if self._programmatic_close:
            return False
        if self.os_window_id:
            gtk_input_close(self.os_window_id)
        return True

    def _focus_changed(self, window: Gtk.Window, _pspec: Any) -> None:
        if window.is_active():
            self.im_context.focus_in()
            window.set_focus(self.gl_area)
            self.gl_area.grab_focus()
        else:
            self.im_context.focus_out()
        if self.os_window_id:
            gtk_input_focus(self.os_window_id, window.is_active())

    def _key_pressed(self, _controller: Gtk.EventControllerKey, keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        event = _controller.get_current_event()
        # IM contexts may consume modified keys, including terminal controls
        # and kitty shortcuts. Only text input belongs to the IM context.
        shortcut_mods = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
        if event is not None and not state & shortcut_mods and self.im_context.filter_keypress(event):
            self._ime_filtered_keys.add(keycode)
            return True
        if not self.os_window_id:
            return False
        key, shifted = self._kitty_key(keyval, state)
        action = GLFW_REPEAT if keycode in self._pressed_keys else GLFW_PRESS
        self._pressed_keys.add(keycode)
        text = ''
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            codepoint = Gdk.keyval_to_unicode(keyval)
            if codepoint >= 32 and codepoint != 127:
                text = chr(codepoint)
        # Kitty's Linux native_key is the XKB keysym (the GDK keyval), not the
        # hardware keycode used here only to track press/repeat pairs.
        return bool(gtk_input_key(self.os_window_id, key, shifted, 0, keyval, action, self._mods(state), text))

    def _key_released(self, _controller: Gtk.EventControllerKey, keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        event = _controller.get_current_event()
        if event is not None and keycode in self._ime_filtered_keys:
            self.im_context.filter_keypress(event)
        if keycode in self._ime_filtered_keys:
            self._ime_filtered_keys.discard(keycode)
            return True
        self._pressed_keys.discard(keycode)
        if self.os_window_id:
            key, shifted = self._kitty_key(keyval, state)
            gtk_input_key(self.os_window_id, key, shifted, 0, keyval, GLFW_RELEASE, self._mods(state), '')
            return True
        return False

    def _pointer_pressed(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        if self.os_window_id:
            self.gl_area.grab_focus()
            button = {1: GLFW_MOUSE_BUTTON_1, 2: GLFW_MOUSE_BUTTON_3, 3: GLFW_MOUSE_BUTTON_2}.get(gesture.get_current_button(), -1)
            gtk_input_mouse_button(self.os_window_id, button, GLFW_PRESS, self._mods(gesture.get_current_event_state()), x, y)

    def _pointer_released(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        if self.os_window_id:
            button = {1: GLFW_MOUSE_BUTTON_1, 2: GLFW_MOUSE_BUTTON_3, 3: GLFW_MOUSE_BUTTON_2}.get(gesture.get_current_button(), -1)
            gtk_input_mouse_button(self.os_window_id, button, GLFW_RELEASE, self._mods(gesture.get_current_event_state()), x, y)

    def _pointer_moved(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        if self.os_window_id:
            gtk_input_pointer(self.os_window_id, 0, x, y)

    def _pointer_entered(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        if self.os_window_id:
            gtk_input_pointer(self.os_window_id, 1, x, y)

    def _pointer_left(self, _controller: Gtk.EventControllerMotion) -> None:
        if self.os_window_id:
            gtk_input_pointer(self.os_window_id, 2, 0, 0)

    def _scrolled(self, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        if not self.os_window_id:
            return False
        x = y = 0.0
        event = controller.get_current_event()
        if event is not None:
            pos = event.get_position()
            if pos and pos[0]:
                x, y = pos[1], pos[2]
        is_wheel = controller.get_unit() == Gdk.ScrollUnit.WHEEL
        return bool(gtk_input_scroll(self.os_window_id, dx, dy, x, y, self._mods(controller.get_current_event_state()), is_wheel))

    def set_scrollbar_callbacks(
        self,
        state_getter: Callable[[], tuple[float, int, int, bool, str, bool]],
        scroll_handler: Callable[[float], None],
    ) -> None:
        self._scrollbar_state_getter = state_getter
        self._scrollbar_scroll_handler = scroll_handler
        self._sync_scrollbar()

    def _sync_scrollbar(self) -> None:
        if self._scrollbar_state_getter is None:
            return
        self._scrollbar_state = self._scrollbar_state_getter()
        scrolled_by, history_lines, screen_lines, is_main_screen, _policy, interactive = self._scrollbar_state
        lines = max(1, screen_lines)
        history = max(0, history_lines) if is_main_screen else 0
        adjustment_key = (max(0, history - scrolled_by), 0, history + lines, 1, max(1, lines - 1), lines)
        if adjustment_key != self._scrollbar_adjustment_key:
            self._syncing_scrollbar = True
            try:
                self.scrollbar_adjustment.configure(*adjustment_key)
            finally:
                self._syncing_scrollbar = False
            self._scrollbar_adjustment_key = adjustment_key
        self.scrollbar.set_can_target(interactive)
        self._update_scrollbar_visibility()

    def _scrollbar_value_changed(self, adjustment: Gtk.Adjustment) -> None:
        if self._syncing_scrollbar or self._scrollbar_scroll_handler is None:
            return
        history = max(0, self._scrollbar_state[1])
        if self._scrollbar_state[3]:
            self._scrollbar_scroll_handler(max(0.0, history - adjustment.get_value()))

    def _scrollbar_pointer_moved(self, _controller: Gtk.EventControllerMotion, x: float, _y: float) -> None:
        hovered = x >= max(0, self.content.get_width() - 24)
        if hovered != self._scrollbar_hovered:
            self._scrollbar_hovered = hovered
            self._update_scrollbar_visibility()

    def _scrollbar_pointer_left(self, _controller: Gtk.EventControllerMotion) -> None:
        if self._scrollbar_hovered:
            self._scrollbar_hovered = False
            self._update_scrollbar_visibility()

    def _update_scrollbar_visibility(self) -> None:
        _scrolled_by, history, _lines, is_main_screen, policy, _interactive = self._scrollbar_state
        has_history = is_main_screen and history > 0
        if not has_history:
            self.scrollbar.set_visible(False)
            return
        scrolling_up = has_history and self._scrollbar_state[0] > 0
        hovered = self._scrollbar_hovered
        visible = {
            'always': has_history,
            'never': False,
            'scrolled': scrolling_up,
            'hovered': hovered,
            'scrolled-and-hovered': scrolling_up and hovered,
            'scrolled-or-hovered': scrolling_up or hovered,
        }.get(policy, scrolling_up)
        self.scrollbar.set_visible(visible)

    @staticmethod
    def _mods(state: Gdk.ModifierType) -> int:
        from .fast_data_types import GLFW_MOD_CAPS_LOCK

        ans = 0
        if state & Gdk.ModifierType.SHIFT_MASK:
            ans |= GLFW_MOD_SHIFT
        if state & Gdk.ModifierType.CONTROL_MASK:
            ans |= GLFW_MOD_CONTROL
        if state & Gdk.ModifierType.ALT_MASK:
            ans |= GLFW_MOD_ALT
        if state & Gdk.ModifierType.SUPER_MASK:
            ans |= GLFW_MOD_SUPER
        if state & Gdk.ModifierType.HYPER_MASK:
            ans |= GLFW_MOD_HYPER
        if state & Gdk.ModifierType.META_MASK:
            ans |= GLFW_MOD_META
        if state & Gdk.ModifierType.LOCK_MASK:
            ans |= GLFW_MOD_CAPS_LOCK
        return ans

    @staticmethod
    def _kitty_key(keyval: int, state: Gdk.ModifierType) -> tuple[int, int]:
        special = {
            Gdk.KEY_Escape: GLFW_FKEY_ESCAPE,
            Gdk.KEY_Return: GLFW_FKEY_ENTER,
            Gdk.KEY_KP_Enter: GLFW_FKEY_ENTER,
            Gdk.KEY_Tab: GLFW_FKEY_TAB,
            Gdk.KEY_ISO_Left_Tab: GLFW_FKEY_TAB,
            Gdk.KEY_BackSpace: GLFW_FKEY_BACKSPACE,
            Gdk.KEY_Insert: GLFW_FKEY_INSERT,
            Gdk.KEY_Delete: GLFW_FKEY_DELETE,
            Gdk.KEY_Left: GLFW_FKEY_LEFT,
            Gdk.KEY_Right: GLFW_FKEY_RIGHT,
            Gdk.KEY_Up: GLFW_FKEY_UP,
            Gdk.KEY_Down: GLFW_FKEY_DOWN,
            Gdk.KEY_Page_Up: GLFW_FKEY_PAGE_UP,
            Gdk.KEY_Page_Down: GLFW_FKEY_PAGE_DOWN,
            Gdk.KEY_Home: GLFW_FKEY_HOME,
            Gdk.KEY_End: GLFW_FKEY_END,
            Gdk.KEY_Caps_Lock: GLFW_FKEY_CAPS_LOCK,
            Gdk.KEY_Num_Lock: GLFW_FKEY_NUM_LOCK,
            Gdk.KEY_Scroll_Lock: GLFW_FKEY_SCROLL_LOCK,
            Gdk.KEY_Print: GLFW_FKEY_PRINT_SCREEN,
            Gdk.KEY_Pause: GLFW_FKEY_PAUSE,
            Gdk.KEY_Shift_L: GLFW_FKEY_LEFT_SHIFT,
            Gdk.KEY_Shift_R: GLFW_FKEY_RIGHT_SHIFT,
            Gdk.KEY_Control_L: GLFW_FKEY_LEFT_CONTROL,
            Gdk.KEY_Control_R: GLFW_FKEY_RIGHT_CONTROL,
            Gdk.KEY_Alt_L: GLFW_FKEY_LEFT_ALT,
            Gdk.KEY_Alt_R: GLFW_FKEY_RIGHT_ALT,
            Gdk.KEY_Super_L: GLFW_FKEY_LEFT_SUPER,
            Gdk.KEY_Super_R: GLFW_FKEY_RIGHT_SUPER,
            Gdk.KEY_Hyper_L: GLFW_FKEY_LEFT_HYPER,
            Gdk.KEY_Hyper_R: GLFW_FKEY_RIGHT_HYPER,
            Gdk.KEY_Meta_L: GLFW_FKEY_LEFT_META,
            Gdk.KEY_Meta_R: GLFW_FKEY_RIGHT_META,
        }
        for i, name in enumerate((
            GLFW_FKEY_F1, GLFW_FKEY_F2, GLFW_FKEY_F3, GLFW_FKEY_F4, GLFW_FKEY_F5,
            GLFW_FKEY_F6, GLFW_FKEY_F7, GLFW_FKEY_F8, GLFW_FKEY_F9, GLFW_FKEY_F10,
            GLFW_FKEY_F11, GLFW_FKEY_F12, GLFW_FKEY_F13, GLFW_FKEY_F14, GLFW_FKEY_F15,
            GLFW_FKEY_F16, GLFW_FKEY_F17, GLFW_FKEY_F18, GLFW_FKEY_F19, GLFW_FKEY_F20,
            GLFW_FKEY_F21, GLFW_FKEY_F22, GLFW_FKEY_F23, GLFW_FKEY_F24, GLFW_FKEY_F25,
            GLFW_FKEY_F26, GLFW_FKEY_F27, GLFW_FKEY_F28, GLFW_FKEY_F29, GLFW_FKEY_F30,
            GLFW_FKEY_F31, GLFW_FKEY_F32, GLFW_FKEY_F33, GLFW_FKEY_F34, GLFW_FKEY_F35,
        ), start=1):
            special[getattr(Gdk, f'KEY_F{i}')] = name
        actual = Gdk.keyval_to_unicode(keyval)
        shifted = actual if state & Gdk.ModifierType.SHIFT_MASK else 0
        base_keyval = Gdk.keyval_to_lower(keyval)
        base = Gdk.keyval_to_unicode(base_keyval)
        actual_char = chr(actual) if actual else ''
        base_chars = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/', '~': '`'}
        if actual_char in base_chars:
            base = ord(base_chars[actual_char])
        key = special.get(keyval, base)
        return key, shifted

    def _ime_commit(self, _context: Gtk.IMContext, text: str) -> None:
        if self.os_window_id:
            gtk_input_key(self.os_window_id, 0, 0, 0, 0, GLFW_PRESS, 0, text, GLFW_IME_COMMIT_TEXT)

    def _ime_preedit_changed(self, context: Gtk.IMContext) -> None:
        if self.os_window_id:
            text, _attributes, _cursor = context.get_preedit_string()
            gtk_input_key(self.os_window_id, 0, 0, 0, 0, GLFW_PRESS, 0, text, GLFW_IME_PREEDIT_CHANGED)

    def set_ime_cursor_location(self, left: int, top: int, width: int, height: int) -> None:
        scale = max(1, self._gtk_scale)
        self.im_context.set_cursor_location(Gdk.Rectangle(left // scale, top // scale, max(1, width // scale), max(1, height // scale)))

    def apply_chrome_mode(self, hide_window_decorations: int, tab_bar_visible: bool | None = None) -> None:
        """Apply kitty's existing hide_window_decorations bit field.

        The GTK headerbar also contains the tabs. Keep it visible in
        titlebar-only mode so that setting cannot hide the tab UI.
        """
        self.window.set_decorated(not bool(hide_window_decorations & 0b01))
        if tab_bar_visible is not None:
            self._tab_bar_visible = tab_bar_visible
        topbar_visible = not bool(hide_window_decorations & 0b01) and self._tab_bar_visible
        self.tab_chrome.set_visible(topbar_visible)

    def _apply_wayland_blur(self) -> None:
        if _use_gtk_css_wayland_blur or not self.os_window_id or not self.window.get_realized():
            return
        gtk_backend_set_window_blur(self.os_window_id, self.window.__gpointer__, self._background_blur)

    def apply_chrome_style(self, background_color: int, background_opacity: float, background_blur: int) -> None:
        """Match GTK's header surface to kitty's active terminal background."""
        self._background_blur = max(0, int(background_blur)) if background_opacity < 1.0 else 0
        self._apply_wayland_blur()
        style_key = (background_color, float(background_opacity), self._background_blur)
        if getattr(self, '_chrome_style_key', None) == style_key:
            return
        self._chrome_style_key = style_key
        r = (background_color >> 16) & 0xff
        g = (background_color >> 8) & 0xff
        b = background_color & 0xff
        alpha = max(0.0, min(float(background_opacity), 1.0))
        blur_css = ''
        if _use_gtk_css_wayland_blur and is_wayland():
            blur_css = f'backdrop-filter: {"blur(20px)" if self._background_blur > 0 else "none"};'
        css = f'''
            window.kitty-window,
            glarea {{
                background-color: transparent;
                background-image: none;
            }}
            window.kitty-window {{
                {blur_css}
            }}
            headerbar.kitty-headerbar {{
                background-color: rgba({r}, {g}, {b}, {alpha:.4f});
                background-image: none;
                box-shadow: none;
                border-bottom: none;
            }}
        '''
        old_provider = getattr(self, '_background_css', None)
        if old_provider is not None:
            self.window.get_style_context().remove_provider(old_provider)
            self.gl_area.get_style_context().remove_provider(old_provider)
            self.tab_chrome.header_bar.get_style_context().remove_provider(old_provider)
        self._background_css = Gtk.CssProvider()
        self._background_css.load_from_data(css.encode())
        for widget in (self.window, self.gl_area, self.tab_chrome.header_bar):
            widget.get_style_context().add_provider(
                self._background_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def bind(self, os_window_id: int) -> None:
        self.os_window_id = os_window_id
        if hasattr(self.events, 'bind'):
            self.events.bind(os_window_id)
        if not os_window_id:
            return
        self._gtk_scale = max(1, self.gl_area.get_scale_factor())
        self.events.resized(self.gl_area.get_width(), self.gl_area.get_height(), self._gtk_scale)
        self.events.focus_changed(self.window.is_active())
        self._apply_wayland_blur()

    def set_tab_actions(
        self,
        *,
        on_new_tab: Callable[[], None],
        on_select_tab: TabAction,
        on_close_tab: TabAction,
        on_reorder_tabs: Callable[[Sequence[int]], None],
    ) -> None:
        self.tab_chrome.set_actions(
            on_new_tab=on_new_tab,
            on_select_tab=on_select_tab,
            on_close_tab=on_close_tab,
            on_reorder_tabs=on_reorder_tabs,
        )

    def set_title(self, title: str) -> None:
        self.window.set_title(title)

    def hide(self) -> None:
        self.window.set_visible(False)

    def hide_pointer(self) -> None:
        self._cursor_hidden = True
        self.gl_area.set_cursor_from_name('none')

    def show_pointer(self) -> None:
        self._cursor_hidden = False
        self.gl_area.set_cursor_from_name(self._cursor_name)

    def set_pointer_shape(self, cursor_name: str) -> None:
        self._cursor_name = cursor_name
        if not self._cursor_hidden:
            self.gl_area.set_cursor_from_name(cursor_name)

    def toggle_fullscreen(self) -> None:
        self._fullscreen = not getattr(self, '_fullscreen', False)
        if self._fullscreen:
            self.window.fullscreen()
        else:
            self.window.unfullscreen()

    def fullscreen(self) -> None:
        self._fullscreen = True
        self.window.fullscreen()

    def is_fullscreen(self) -> bool:
        return getattr(self, '_fullscreen', False)

    def toggle_maximized(self) -> bool:
        is_maximized = getattr(self.window, 'is_maximized', None)
        if is_maximized and is_maximized():
            self.window.unmaximize()
            return False
        self.window.maximize()
        return True

    def maximize(self) -> None:
        self.window.maximize()

    def minimize(self) -> None:
        self.window.minimize()

    def restore(self) -> None:
        if self.is_fullscreen():
            self.window.unfullscreen()
            self._fullscreen = False
        self.window.unmaximize()
        self.window.present()

    def present(self) -> None:
        self.window.present()
        self.window.set_focus(self.gl_area)
        self.gl_area.grab_focus()
        gtk_backend_activate()

    def realize_gl_area(self, present: bool = True) -> None:
        # The first GTK GL context must be current before kitty uploads shader
        # programs and sprite atlases during create_os_window().
        if present:
            self.window.present()
            gtk_backend_activate()
        else:
            self.window.realize()
        import time

        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.gl_area.get_realized() and self.gl_area.get_width() > 0 and self.gl_area.get_height() > 0:
                self.gl_area.make_current()
                if error := self.gl_area.get_error():
                    raise RuntimeError(f'Failed to create GTK OpenGL area: {error.message}')
                if present:
                    self.window.set_focus(self.gl_area)
                    self.gl_area.grab_focus()
                if not present and self.window.get_mapped():
                    self.window.set_visible(False)
                return
            context.iteration(False)
            time.sleep(0.005)
        raise RuntimeError('Timed out waiting for GTK OpenGL area realization')

    def make_gl_current(self) -> None:
        self.gl_area.make_current()
        self.gl_area.attach_buffers()

    def get_size(self) -> tuple[int, int, int, int]:
        width, height = self.gl_area.get_width(), self.gl_area.get_height()
        scale = max(1, self.gl_area.get_scale_factor())
        return width, height, width * scale, height * scale

    def is_mapped(self) -> bool:
        if not self.window.get_mapped():
            return False
        surface = self.window.get_surface()
        if surface is not None and hasattr(surface, 'get_state'):
            return not bool(surface.get_state() & Gdk.ToplevelState.MINIMIZED)
        return True

    def is_maximized(self) -> bool:
        return self.window.is_maximized()

    def set_size(self, width: int, height: int) -> None:
        bar_height = 0
        if self.tab_chrome.header_bar.get_visible():
            _, bar_height, _, _ = self.tab_chrome.header_bar.measure(Gtk.Orientation.VERTICAL, -1)
        self.window.set_default_size(width, height + bar_height)

    def get_scale(self) -> float:
        return float(max(1, self.gl_area.get_scale_factor()))

    def set_title(self, title: str) -> None:
        self.window.set_title(title)

    def queue_render(self) -> None:
        self.gl_area.queue_render()

    def close(self) -> None:
        self._programmatic_close = True
        if self._scrollbar_sync_source:
            GLib.source_remove(self._scrollbar_sync_source)
            self._scrollbar_sync_source = 0
        if self.os_window_id:
            gtk_backend_clear_window_blur(self.os_window_id)
        self.bind(0)
        self.window.close()
