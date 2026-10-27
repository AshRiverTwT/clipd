from __future__ import annotations

import copy
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from .. import __version__, util  # noqa: E402
from ..actions import action_by_id, actions_for, primary_action  # noqa: E402
from ..config import Config, save_config  # noqa: E402
from ..models import FILTER_KINDS, KIND_ICONS, Entry  # noqa: E402
from ..search import search  # noqa: E402
from .theme import CSS  # noqa: E402

HELP_ROWS = [
    ("↑ ↓", "navigate"),
    ("↵", "copy / restore"),
    ("Ctrl+↵", "context action"),
    ("Ctrl+1…6", "action buttons"),
    ("Ctrl+P", "pin / unpin"),
    ("Ctrl+D", "delete entry"),
    ("Ctrl+Z", "undo delete"),
    ("Tab", "cycle type filter"),
    ("Ctrl+L", "focus search"),
    ("Ctrl+Shift+P", "pause capture"),
    ("Ctrl+,", "settings"),
    ("Ctrl+S", "save settings"),
    ("F1", "this help"),
    ("Esc", "close"),
]


def _icon_available(name: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    try:
        return Gtk.IconTheme.get_for_display(display).has_icon(name)
    except Exception:
        return False


def _texture(data: bytes):
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except Exception:
        return None


class EntryRow(Gtk.Box):
    def __init__(self, entry: Entry, ui: "GTKUI") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.entry_id = entry.id
        self.summary = entry
        self.ui = ui
        self.add_css_class("clipd-row")
        self.set_can_focus(False)
        try:
            self.set_focusable(False)
        except AttributeError:
            pass

        accent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        accent.add_css_class("row-accent")
        accent.set_size_request(2, -1)
        accent.set_can_focus(False)
        self.append(accent)
        self.accent = accent

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("row-box")

        icon_name = KIND_ICONS.get(entry.kind)
        if icon_name and _icon_available(icon_name):
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(18)
            icon.add_css_class("row-icon")
            box.append(icon)
        else:
            badge = Gtk.Label(label="·")
            badge.add_css_class("row-icon")
            badge.set_size_request(20, -1)
            box.append(badge)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)
        title = Gtk.Label(label=entry.preview or "(empty)")
        title.set_xalign(0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(64)
        title.add_css_class("row-title")
        meta = Gtk.Label(
            label=f"{entry.label}  ·  {util.human_size(entry.size)}  ·  "
            f"{util.relative_time(entry.last_used_at)}"
        )
        meta.set_xalign(0.0)
        meta.add_css_class("row-meta")
        text.append(title)
        text.append(meta)
        box.append(text)

        if entry.pinned:
            pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
            pin.set_pixel_size(13)
            pin.add_css_class("row-pin")
            box.append(pin)

        more = Gtk.Button(label="⋯")
        more.add_css_class("more")
        more.set_tooltip_text("Actions")
        more.set_focus_on_click(False)
        more.connect("clicked", self._on_more)
        box.append(more)
        self.more_button = more

        self.append(box)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._hover_in)
        motion.connect("leave", self._hover_out)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_click)
        self.add_controller(click)

    def _on_click(self, _gesture, n_press: int, _x: float, _y: float) -> None:
        self.ui.select_row(self)
        self.ui.search.grab_focus()
        if n_press >= 2:
            self.ui.restore_entry(self.entry_id)

    def _hover_in(self, _controller, _x, _y) -> None:
        self.more_button.set_opacity(1.0)

    def _hover_out(self, _controller) -> None:
        if not self.has_css_class("active"):
            self.more_button.set_opacity(0.0)

    def _on_more(self, _button: Gtk.Button) -> None:
        self.ui.show_row_menu(self.more_button, self.summary)


class GTKUI:
    frontend = "gtk"

    def __init__(self, daemon) -> None:
        self.daemon = daemon
        self.config: Config = daemon.config
        self.window: Gtk.Window | None = None
        self._visible_rows: list[EntryRow] = []
        self._entries: list[Entry] = []
        self._selected_id: int | None = None
        self._filter = "all"
        self._shown_at = 0.0
        self._dirty = True
        self._dialog_open = False
        self._row_popover: Gtk.Popover | None = None
        self._help_popover: Gtk.Popover | None = None
        self._prompt_loop: GLib.MainLoop | None = None
        self._prompt_result: str | None = None
        self._toast_timeout: int | None = None
        self._action_buttons: list[Gtk.Button] = []
        self._actions = []
        self.app = Gtk.Application(application_id="io.github.clipd.Clipd")
        self.app.connect("activate", lambda *_args: None)
        try:
            self.app.register(None)
        except GLib.Error:
            # The application is already registered in this session; reuse it.
            pass
        self._build()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        self.window = Gtk.Window(application=self.app)
        self.window.add_css_class("clipd-window")
        self.window.set_title("clipd")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_hide_on_close(True)
        self.window.set_default_size(self.config.ui.width, self.config.ui.height)
        self.window.connect("close-request", self._on_close_request)
        self.window.connect("notify::is-active", self._on_active_changed)
        LayerShell.init_for_window(self.window)
        if not LayerShell.is_layer_window(self.window):
            raise RuntimeError(
                "gtk4-layer-shell failed to initialize; start clipd through "
                "bin/clipd so it can set LD_PRELOAD"
            )
        LayerShell.set_namespace(self.window, "clipd")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.EXCLUSIVE)
        LayerShell.set_exclusive_zone(self.window, -1)
        try:
            LayerShell.set_anchor(self.window, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self.window, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self.window, LayerShell.Edge.RIGHT, False)
            LayerShell.set_anchor(self.window, LayerShell.Edge.BOTTOM, False)
        except Exception:
            pass

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("clipd-outer")
        outer.set_vexpand(True)
        outer.set_hexpand(True)
        self.window.set_child(outer)

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class("clipd-card")
        self.card.set_vexpand(True)
        self.card.set_hexpand(True)
        outer.append(self.card)

        self.card.append(self._build_topbar())
        self.page_stack = Gtk.Stack()
        self.page_stack.set_vexpand(True)
        self.page_stack.set_hexpand(True)
        self.page_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.main_page = self._build_main_page()
        self.settings_page = self._build_settings_page()
        self.page_stack.add_named(self.main_page, "main")
        self.page_stack.add_named(self.settings_page, "settings")
        self.page_stack.set_visible_child_name("main")
        self.card.append(self.page_stack)

        self._install_css()
        self._key_controller = Gtk.EventControllerKey()
        self._key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._key_controller.connect("key-pressed", self._on_key)
        self.window.add_controller(self._key_controller)
        self.window.set_visible(False)

    def _build_main_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("main-page")
        page.append(self._build_filters())
        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        page.append(self._build_body())
        self._build_prompt_bar()
        self.main_page = page
        page.append(self.prompt_bar)
        page.append(self._build_footer())
        return page

    def _build_topbar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("topbar")
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        brand.set_valign(Gtk.Align.CENTER)
        dot = Gtk.Box()
        dot.add_css_class("brand-dot")
        dot.set_size_request(6, 6)
        brand.append(dot)
        word = Gtk.Label(label="CLIPD")
        word.add_css_class("brand")
        brand.append(word)
        sep = Gtk.Box()
        sep.add_css_class("brand-sep")
        sep.set_size_request(1, 14)
        brand.append(sep)
        bar.append(brand)
        self.search = Gtk.SearchEntry()
        self.search.add_css_class("clipd-search")
        self.search.set_placeholder_text("Search clipboard…")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda _e: self._primary_activate())
        bar.append(self.search)
        self.count_label = Gtk.Label(label="0")
        self.count_label.add_css_class("count")
        bar.append(self.count_label)
        self.paused_label = Gtk.Label(label="PAUSED")
        self.paused_label.add_css_class("paused-badge")
        self.paused_label.set_visible(False)
        bar.append(self.paused_label)
        self.settings_button = Gtk.Button()
        self.settings_button.add_css_class("gear")
        self.settings_button.set_focus_on_click(False)
        self.settings_button.set_tooltip_text("Settings  Ctrl+,")
        if _icon_available("emblem-system-symbolic"):
            self.settings_button.set_child(
                Gtk.Image.new_from_icon_name("emblem-system-symbolic")
            )
        else:
            self.settings_button.set_label("⚙")
        self.settings_button.connect("clicked", lambda _b: self._show_settings())
        bar.append(self.settings_button)
        return bar

    def _build_filters(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("filters")
        group: Gtk.ToggleButton | None = None
        for key, label in FILTER_KINDS:
            button = Gtk.ToggleButton(label=label.upper())
            button.add_css_class("filter")
            button.set_focus_on_click(False)
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("clicked", self._on_filter_clicked, key)
            box.append(button)
            self.__dict__.setdefault("_filter_buttons", {})[key] = button
        self._filter_buttons["all"].set_active(True)
        return box

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.add_css_class("body")
        body.set_vexpand(True)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(70)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.listbox.add_css_class("entry-list")
        self.listbox.set_valign(Gtk.Align.START)
        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.add_css_class("list-scroll")
        self.list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroll.set_child(self.listbox)
        self.list_scroll.set_vexpand(True)
        self.stack.add_named(self.list_scroll, "list")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        self.empty_title = Gtk.Label(label="Nothing here yet")
        self.empty_title.add_css_class("empty-title")
        self.empty_sub = Gtk.Label(label="Copy something to get started")
        self.empty_sub.add_css_class("empty")
        empty.append(self.empty_title)
        empty.append(self.empty_sub)
        self.stack.add_named(empty, "empty")
        self.stack.set_visible_child_name("list")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_size_request(492, -1)
        left.set_hexpand(True)
        left.append(self.stack)
        body.append(left)

        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.detail_box.add_css_class("detail")
        self.detail_box.set_size_request(345, -1)
        self.detail_box.set_visible(self.config.ui.show_preview)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.detail_kind = Gtk.Label(label="")
        self.detail_kind.add_css_class("detail-kind")
        self.detail_meta = Gtk.Label(label="")
        self.detail_meta.add_css_class("detail-meta")
        header.append(self.detail_kind)
        header.append(self.detail_meta)
        self.detail_box.append(header)
        self.detail_title = Gtk.Label(label="Select an entry")
        self.detail_title.set_xalign(0.0)
        self.detail_title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.detail_title.add_css_class("detail-title")
        self.detail_box.append(self.detail_title)

        self.preview_scroll = Gtk.ScrolledWindow()
        self.preview_scroll.add_css_class("preview-scroll")
        self.preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.preview_scroll.set_vexpand(True)
        self.preview = Gtk.Label(label="")
        self.preview.set_xalign(0.0)
        self.preview.set_yalign(0.0)
        self.preview.set_wrap(True)
        self.preview.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.preview.set_selectable(True)
        self.preview.add_css_class("preview")
        self.preview_scroll.set_child(self.preview)
        self.detail_box.append(self.preview_scroll)
        self.image_preview = Gtk.Picture()
        self.image_preview.add_css_class("image-preview")
        self.image_preview.set_can_shrink(True)
        self.image_preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.image_preview.set_vexpand(True)
        self.image_preview.set_visible(False)
        self.detail_box.append(self.image_preview)

        self.actions_flow = Gtk.FlowBox()
        self.actions_flow.add_css_class("actions")
        self.actions_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.actions_flow.set_max_children_per_line(2)
        self.actions_flow.set_row_spacing(4)
        self.actions_flow.set_column_spacing(4)
        self.detail_box.append(self.actions_flow)
        body.append(self.detail_box)
        return body

    def _build_prompt_bar(self) -> None:
        self.prompt_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.prompt_bar.add_css_class("prompt-bar")
        self.prompt_label = Gtk.Label(label="")
        self.prompt_label.add_css_class("prompt-label")
        self.prompt_label.set_xalign(0.0)
        self.prompt_bar.append(self.prompt_label)
        self.prompt_entry = Gtk.Entry()
        self.prompt_entry.add_css_class("prompt-entry")
        self.prompt_entry.set_hexpand(True)
        self.prompt_entry.set_activates_default(True)
        self.prompt_entry.connect("activate", self._prompt_accept)
        self.prompt_bar.append(self.prompt_entry)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("prompt-button")
        cancel.set_focus_on_click(False)
        cancel.connect("clicked", lambda _b: self._prompt_finish(None))
        self.prompt_bar.append(cancel)
        accept = Gtk.Button(label="OK")
        accept.add_css_class("prompt-button")
        accept.add_css_class("primary")
        accept.set_focus_on_click(False)
        accept.connect("clicked", self._prompt_accept)
        self.prompt_bar.append(accept)
        self.prompt_bar.set_visible(False)

    def _prompt_accept(self, *_args) -> None:
        self._prompt_finish(self.prompt_entry.get_text().strip() or None)

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("footer")
        hints = Gtk.Label(
            label="↑↓  ↵ copy   Ctrl+↵ action   Ctrl+1–6   Ctrl+P pin   Ctrl+D delete   F1 help"
        )
        hints.set_xalign(0.0)
        hints.set_hexpand(True)
        footer.append(hints)
        self.toast_label = Gtk.Label(label="")
        self.toast_label.add_css_class("toast")
        self.toast_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.toast_label.set_max_width_chars(48)
        self.toast_revealer = Gtk.Revealer()
        self.toast_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.toast_revealer.set_transition_duration(110)
        self.toast_revealer.set_child(self.toast_label)
        self.toast_revealer.set_reveal_child(False)
        footer.append(self.toast_revealer)
        return footer

    def _build_settings_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("settings-page")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("settings-header")
        back = Gtk.Button(label="←  Back")
        back.add_css_class("settings-back")
        back.set_focus_on_click(False)
        back.connect("clicked", lambda _b: self._show_main())
        self.settings_back = back
        header.append(back)
        title = Gtk.Label(label="SETTINGS")
        title.add_css_class("settings-title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        header.append(title)
        save = Gtk.Button(label="Save settings")
        save.add_css_class("settings-save")
        save.set_focus_on_click(False)
        save.connect("clicked", lambda _b: self._save_settings())
        header.append(save)
        page.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("settings-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form.add_css_class("settings-form")
        scroll.set_child(form)
        page.append(scroll)

        self._settings_controls: dict[str, object] = {}
        self._settings_text: dict[str, Gtk.TextView] = {}
        self._settings_actions: dict[str, Gtk.CheckButton] = {}

        self._settings_section(form, "General")
        self._settings_spin(form, "History limit", "max_entries", 1, 1000, 1)
        self._settings_spin(form, "Max text (MB)", "max_text_mb", 1, 16, 1)
        self._settings_spin(form, "Max image (MB)", "max_image_mb", 1, 10, 1)
        self._settings_dropdown(
            form, "Auto-paste", "auto_paste", ["off", "hyprland", "wtype"]
        )
        self._settings_spin(form, "Paste delay (ms)", "paste_delay_ms", 0, 3000, 20)
        self._settings_entry(form, "Terminal", "terminal")

        self._settings_section(form, "Appearance")
        self._settings_dropdown(
            form, "Frontend", "frontend", ["auto", "gtk", "rofi", "fuzzel", "none"]
        )
        self._settings_spin(form, "Window width", "width", 520, 1600, 10)
        self._settings_spin(form, "Window height", "height", 320, 1200, 10)
        self._settings_dropdown(form, "Enter action", "primary_action", ["copy", "context"])
        self._settings_switch(form, "Show preview pane", "show_preview")
        hint = Gtk.Label(label="Frontend changes take effect after restarting clipd.")
        hint.add_css_class("settings-hint")
        hint.set_xalign(0.0)
        form.append(hint)

        self._settings_section(form, "Privacy")
        self._settings_switch(form, "Pause when session locks", "pause_on_lock")
        self._settings_switch(form, "Ignore whitespace-only entries", "ignore_whitespace")
        self._settings_textarea(form, "Excluded regex patterns", "exclude_patterns")

        self._settings_section(form, "Actions")
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        action_box.add_css_class("settings-checks")
        for category in ("copy", "open", "clone", "save", "format", "lint"):
            check = Gtk.CheckButton(label=category.title())
            check.add_css_class("settings-check")
            action_box.append(check)
            self._settings_actions[category] = check
        self._settings_row(form, "Enabled actions", action_box)
        self._settings_entry(form, "Clone directory", "clone_dir")
        self._settings_entry(form, "Snippet directory", "snippet_dir")
        self._settings_switch(form, "Confirm clone destination", "confirm_writes")
        self._settings_switch(form, "Allow network actions", "network")

        self._settings_section(form, "Clipboard")
        self._settings_spin(form, "Read timeout (ms)", "read_timeout_ms", 250, 30000, 50)
        self._settings_textarea(form, "Excluded MIME types", "exclude_mime")
        self._sync_settings()
        return page

    def _settings_row(self, form: Gtk.Box, label: str, widget: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("settings-row")
        text = Gtk.Label(label=label)
        text.add_css_class("settings-label")
        text.set_xalign(0.0)
        text.set_size_request(170, -1)
        row.append(text)
        widget.set_hexpand(True)
        widget.set_halign(Gtk.Align.END)
        row.append(widget)
        form.append(row)

    def _settings_section(self, form: Gtk.Box, title: str) -> None:
        label = Gtk.Label(label=title.upper())
        label.add_css_class("settings-section")
        label.set_xalign(0.0)
        form.append(label)

    def _settings_spin(
        self, form: Gtk.Box, label: str, key: str, low: int, high: int, step: int
    ) -> None:
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.add_css_class("settings-input")
        spin.set_digits(0)
        spin.set_numeric(True)
        self._settings_controls[key] = spin
        self._settings_row(form, label, spin)

    def _settings_dropdown(
        self, form: Gtk.Box, label: str, key: str, values: list[str]
    ) -> None:
        dropdown = Gtk.DropDown.new_from_strings(values)
        dropdown.add_css_class("settings-input")
        self._settings_controls[key] = dropdown
        self._settings_row(form, label, dropdown)

    def _settings_entry(self, form: Gtk.Box, label: str, key: str) -> None:
        entry = Gtk.Entry()
        entry.add_css_class("settings-input")
        self._settings_controls[key] = entry
        self._settings_row(form, label, entry)

    def _settings_switch(self, form: Gtk.Box, label: str, key: str) -> None:
        # A square check control keeps the sharp-rectangle visual language.
        check = Gtk.CheckButton()
        check.add_css_class("settings-switch")
        check.set_halign(Gtk.Align.END)
        self._settings_controls[key] = check
        self._settings_row(form, label, check)

    def _settings_textarea(self, form: Gtk.Box, label: str, key: str) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text = Gtk.Label(label=label)
        text.add_css_class("settings-label")
        text.set_xalign(0.0)
        box.append(text)
        view = Gtk.TextView()
        view.add_css_class("settings-textarea")
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_size_request(-1, 62)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(view)
        box.append(scroll)
        self._settings_text[key] = view
        form.append(box)

    def _settings_values(self) -> tuple[dict[str, str], dict[str, str]]:
        texts: dict[str, str] = {}
        for key, view in self._settings_text.items():
            buffer = view.get_buffer()
            texts[key] = buffer.get_text(
                buffer.get_start_iter(), buffer.get_end_iter(), True
            )
        return {}, texts

    def _sync_settings(self) -> None:
        if not hasattr(self, "_settings_controls"):
            return
        cfg = self.config
        values = {
            "max_entries": cfg.general.max_entries,
            "max_text_mb": max(1, cfg.general.max_entry_bytes // (1024 * 1024)),
            "max_image_mb": max(1, cfg.general.max_image_bytes // (1024 * 1024)),
            "paste_delay_ms": cfg.general.paste_delay_ms,
            "width": cfg.ui.width,
            "height": cfg.ui.height,
            "read_timeout_ms": cfg.watch.read_timeout_ms,
        }
        for key, value in values.items():
            widget = self._settings_controls.get(key)
            if isinstance(widget, Gtk.SpinButton):
                widget.set_value(float(value))
        entries = {
            "terminal": cfg.general.terminal,
            "clone_dir": str(cfg.actions.clone_dir),
            "snippet_dir": str(cfg.actions.snippet_dir),
        }
        for key, value in entries.items():
            widget = self._settings_controls.get(key)
            if isinstance(widget, Gtk.Entry):
                widget.set_text(value)
        dropdowns = {
            "auto_paste": (["off", "hyprland", "wtype"], cfg.general.auto_paste),
            "frontend": (["auto", "gtk", "rofi", "fuzzel", "none"], cfg.ui.frontend),
            "primary_action": (["copy", "context"], cfg.ui.primary_action),
        }
        for key, (choices, current) in dropdowns.items():
            widget = self._settings_controls.get(key)
            if isinstance(widget, Gtk.DropDown) and current in choices:
                widget.set_selected(choices.index(current))
        switches = {
            "show_preview": cfg.ui.show_preview,
            "pause_on_lock": cfg.privacy.pause_on_lock,
            "ignore_whitespace": cfg.privacy.ignore_whitespace,
            "confirm_writes": cfg.actions.confirm_writes,
            "network": cfg.actions.network,
        }
        for key, value in switches.items():
            widget = self._settings_controls.get(key)
            if isinstance(widget, (Gtk.Switch, Gtk.CheckButton)):
                widget.set_active(bool(value))
        for category, check in self._settings_actions.items():
            check.set_active(category in cfg.actions.enabled)
        text_values = {
            "exclude_patterns": "\n".join(cfg.privacy.exclude_patterns),
            "exclude_mime": "\n".join(cfg.privacy.exclude_mime),
        }
        for key, value in text_values.items():
            view = self._settings_text.get(key)
            if view is not None:
                view.get_buffer().set_text(value)

    def _save_settings(self) -> None:
        cfg = copy.deepcopy(self.config)
        try:
            cfg.general.max_entries = int(self._settings_controls["max_entries"].get_value())
            cfg.general.max_entry_bytes = (
                int(self._settings_controls["max_text_mb"].get_value()) * 1024 * 1024
            )
            cfg.general.max_image_bytes = (
                int(self._settings_controls["max_image_mb"].get_value()) * 1024 * 1024
            )
            cfg.general.paste_delay_ms = int(
                self._settings_controls["paste_delay_ms"].get_value()
            )
            cfg.general.terminal = self._settings_controls["terminal"].get_text().strip() or "foot"
            cfg.general.auto_paste = self._dropdown_value("auto_paste", "off")
            cfg.ui.frontend = self._dropdown_value("frontend", "auto")
            cfg.ui.width = int(self._settings_controls["width"].get_value())
            cfg.ui.height = int(self._settings_controls["height"].get_value())
            cfg.ui.primary_action = self._dropdown_value("primary_action", "copy")
            cfg.ui.show_preview = bool(self._settings_controls["show_preview"].get_active())
            cfg.privacy.pause_on_lock = bool(
                self._settings_controls["pause_on_lock"].get_active()
            )
            cfg.privacy.ignore_whitespace = bool(
                self._settings_controls["ignore_whitespace"].get_active()
            )
            cfg.actions.clone_dir = Path(
                self._settings_controls["clone_dir"].get_text().strip()
            ).expanduser()
            cfg.actions.snippet_dir = Path(
                self._settings_controls["snippet_dir"].get_text().strip()
            ).expanduser()
            cfg.actions.confirm_writes = bool(
                self._settings_controls["confirm_writes"].get_active()
            )
            cfg.actions.network = bool(self._settings_controls["network"].get_active())
            cfg.actions.enabled = [
                category
                for category, check in self._settings_actions.items()
                if check.get_active()
            ]
            cfg.watch.read_timeout_ms = int(
                self._settings_controls["read_timeout_ms"].get_value()
            )
            cfg.privacy.exclude_patterns = self._text_lines("exclude_patterns")
            cfg.privacy.exclude_mime = self._text_lines("exclude_mime")
            save_config(cfg)
        except (OSError, TypeError, ValueError) as exc:
            self.notify(f"Could not save settings: {exc}")
            return
        response = self.daemon.reload()
        warnings = response.get("warnings") or []
        self.notify(f"Settings saved{f' ({len(warnings)} warning(s))' if warnings else ''}")
        self._show_main()

    def _dropdown_value(self, key: str, default: str) -> str:
        widget = self._settings_controls.get(key)
        if not isinstance(widget, Gtk.DropDown):
            return default
        selected = widget.get_selected()
        model = widget.get_model()
        if selected is None or selected < 0 or model is None:
            return default
        item = model.get_item(selected)
        return item.get_string() if item is not None else default

    def _text_lines(self, key: str) -> list[str]:
        view = self._settings_text.get(key)
        if view is None:
            return []
        buffer = view.get_buffer()
        raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _settings_visible(self) -> bool:
        return (
            getattr(self, "page_stack", None) is not None
            and self.page_stack.get_visible_child_name() == "settings"
        )

    def _show_settings(self) -> None:
        if not self._settings_visible():
            self._sync_settings()
        self.page_stack.set_visible_child_name("settings")
        self.search.set_visible(False)
        self.count_label.set_visible(False)
        self.paused_label.set_visible(False)
        self.settings_button.set_visible(False)
        self.settings_back.grab_focus()

    def _show_main(self) -> None:
        if getattr(self, "page_stack", None) is None:
            return
        self.page_stack.set_visible_child_name("main")
        self.search.set_visible(True)
        self.count_label.set_visible(True)
        self.paused_label.set_visible(self.daemon.paused)
        self.settings_button.set_visible(True)
        self.search.grab_focus()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_string(CSS)
        except (AttributeError, TypeError):
            provider.load_from_data(CSS.encode("utf-8"))  # type: ignore[arg-type]
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        self.window.set_visible(False)

    def _monitor_geometry(self) -> tuple[object | None, Gdk.Rectangle | None]:
        display = Gdk.Display.get_default()
        if display is None:
            return None, None
        monitor = None
        try:
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat else None
            if pointer is not None:
                ok, x, y = pointer.get_position()
                if ok:
                    monitor = display.get_monitor_at_point(int(x), int(y))
        except Exception:
            monitor = None
        if monitor is None:
            monitors = display.get_monitors()
            if monitors is not None and monitors.get_n_items() > 0:
                monitor = monitors.get_item(0)
        if monitor is None:
            return None, None
        return monitor, monitor.get_geometry()

    def _place(self) -> None:
        monitor, geo = self._monitor_geometry()
        if monitor is None or geo is None:
            return
        width = min(self.config.ui.width, max(520, geo.width - 48))
        height = min(self.config.ui.height, max(320, geo.height - 120))
        self.detail_box.set_visible(self.config.ui.show_preview and width >= 800)
        self.window.set_default_size(width, height)
        self.window.set_size_request(width, height)
        left = max(0, (geo.width - width) // 2)
        top = max(24, int((geo.height - height) * 0.16))
        try:
            LayerShell.set_monitor(self.window, monitor)
            LayerShell.set_margin(self.window, LayerShell.Edge.LEFT, left)
            LayerShell.set_margin(self.window, LayerShell.Edge.TOP, top)
        except Exception:
            pass

    def show(self) -> None:
        if self.window.get_visible():
            self.window.present()
            self.search.grab_focus()
            return
        self._place()
        self._show_main()
        self.search.set_text("")
        self._filter = "all"
        for key, button in self._filter_buttons.items():
            button.set_active(key == "all")
        self._refresh(select_first=True)
        self.paused_label.set_visible(self.daemon.paused)
        self._shown_at = time.monotonic()
        self.window.present()
        GLib.idle_add(self._focus_search)
        self._dirty = False

    def hide(self) -> None:
        if self._prompt_loop is not None:
            self._prompt_finish(None)
        if self._row_popover:
            self._row_popover.popdown()
        if self._help_popover:
            self._help_popover.popdown()
        self.window.set_visible(False)

    def toggle(self, force_show: bool = False) -> None:
        if self.window.get_visible() and not force_show:
            self.hide()
        else:
            self.show()

    def is_visible(self) -> bool:
        return bool(self.window.get_visible())

    def _search_focused(self) -> bool:
        """Whether focus is on the search entry or its internal text child."""
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self.search:
                return True
            widget = widget.get_parent()
        return False

    def _focus_search(self) -> bool:
        self.search.grab_focus()
        return False

    def _on_close_request(self, _window) -> bool:
        self.hide()
        return True

    def _on_active_changed(self, _window, _prop) -> None:
        if not self.window.get_visible():
            return
        try:
            active = bool(self.window.get_property("is-active"))
        except Exception:
            return
        if active:
            return
        if time.monotonic() - self._shown_at < 0.35:
            return
        if self._dialog_open:
            return
        if self._row_popover and self._row_popover.get_visible():
            return
        if self._help_popover and self._help_popover.get_visible():
            return
        GLib.timeout_add(70, self._hide_if_inactive)

    def _hide_if_inactive(self) -> bool:
        if not self.window.get_visible() or self._dialog_open:
            return False
        if self._row_popover and self._row_popover.get_visible():
            return False
        if self._help_popover and self._help_popover.get_visible():
            return False
        try:
            if self.window.get_property("is-active"):
                return False
        except Exception:
            return False
        self.hide()
        return False

    def reload(self, config: Config) -> None:
        self.config = config
        self.detail_box.set_visible(config.ui.show_preview)
        if self.window.get_visible():
            self._place()
            self._refresh()

    # -- data -----------------------------------------------------------

    def refresh(self) -> None:
        self._dirty = True
        if self.window.get_visible():
            self._refresh()
            self.paused_label.set_visible(self.daemon.paused)

    def _refresh(self, select_first: bool = False) -> None:
        if self._row_popover:
            self._row_popover.popdown()
        self._entries = self.daemon.store.list_summaries()
        query = self.search.get_text()
        visible = search(self._entries, query, self._filter)
        if not visible and not query and self._filter != "all":
            visible = []
        current_id = None if select_first else self._selected_id
        child = self.listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.listbox.remove(child)
            child = next_child
        self._visible_rows = []
        for entry in visible:
            row = EntryRow(entry, self)
            self.listbox.append(row)
            self._visible_rows.append(row)
        total = len(self._entries)
        self.count_label.set_text(f"{len(visible)}/{total}")
        if visible:
            self.stack.set_visible_child_name("list")
            selected = None
            if current_id is not None:
                selected = next((r for r in self._visible_rows if r.entry_id == current_id), None)
            if selected is None:
                selected = self._visible_rows[0]
            self.select_row(selected)
        else:
            self.stack.set_visible_child_name("empty")
            self._selected_id = None
            if query:
                self.empty_title.set_text("No matches")
                self.empty_sub.set_text("Try a different search or filter")
            elif total == 0:
                self.empty_title.set_text("Nothing copied yet")
                self.empty_sub.set_text("Your history stays on this machine")
            else:
                self.empty_title.set_text("No entries of this type")
                self.empty_sub.set_text("Switch filter or clear the search")
            self._clear_detail()
        self._dirty = False

    def _clear_detail(self) -> None:
        self.detail_kind.set_text("")
        self.detail_meta.set_text("")
        self.detail_title.set_text("Select an entry")
        self.preview.set_text("")
        self.preview_scroll.set_visible(True)
        if hasattr(self, "image_preview"):
            self.image_preview.set_paintable(None)
            self.image_preview.set_visible(False)
        self._clear_actions()

    def _clear_actions(self) -> None:
        while True:
            child = self.actions_flow.get_first_child()
            if child is None:
                break
            self.actions_flow.remove(child)
        self._action_buttons = []
        self._actions = []

    def _update_detail(self, entry_id: int) -> None:
        entry = self.daemon.store.get(entry_id)
        if entry is None:
            self._clear_detail()
            return
        self.detail_kind.set_text(entry.label.upper())
        if entry.kind == "image":
            meta = entry.meta
            dimensions = ""
            if meta.get("width") and meta.get("height"):
                dimensions = f" · {meta['width']}×{meta['height']}"
            mime = (entry.mime or meta.get("mime") or "image").split("/")[-1].upper()
            self.detail_meta.set_text(
                f"{util.human_size(entry.size)} · {mime}{dimensions} · "
                f"{util.relative_time(entry.last_used_at)}"
            )
        else:
            lines = entry.text.count("\n") + 1 if entry.text else 0
            self.detail_meta.set_text(
                f"{util.human_size(entry.size)} · {lines} line"
                f"{'s' if lines != 1 else ''} · {util.relative_time(entry.last_used_at)}"
            )
        self.detail_title.set_text(self._entry_title(entry))
        if entry.kind == "image" and entry.data:
            texture = _texture(entry.data)
            if texture is not None:
                self.image_preview.set_paintable(texture)
                self.image_preview.set_visible(True)
                self.preview_scroll.set_visible(False)
                self.preview.set_text("")
            else:
                self.image_preview.set_visible(False)
                self.preview_scroll.set_visible(True)
                self.preview.set_text("Image preview unavailable")
        else:
            self.image_preview.set_paintable(None)
            self.image_preview.set_visible(False)
            self.preview_scroll.set_visible(True)
            preview = util.clip_text(entry.text, 6000)
            self.preview.set_text(preview)
            if entry.is_code:
                self.preview.add_css_class("mono")
            else:
                self.preview.remove_css_class("mono")

        self._clear_actions()
        actions = actions_for(entry, self.config)
        self._actions = actions
        primary = primary_action(entry, self.config)
        for index, action in enumerate(actions[:6]):
            button = self._action_button(action, entry, primary, index)
            self.actions_flow.append(button)
            self._action_buttons.append(button)

    def _entry_title(self, entry: Entry) -> str:
        meta = entry.meta
        if entry.kind == "url":
            return str(meta.get("domain") or meta.get("url") or "URL")
        if entry.kind == "file":
            return str(meta.get("path") or "File")
        if entry.kind == "directory":
            return str(meta.get("path") or "Folder")
        if entry.kind == "file-list":
            return f"{meta.get('count', 0)} paths"
        if entry.kind == "git-repo":
            return f"{meta.get('host', '')}/{meta.get('path', '')}".strip("/")
        if entry.kind == "command":
            command = str(meta.get("command") or entry.text)
            return command if len(command) <= 64 else command[:63] + "…"
        if entry.kind == "ip":
            return str(meta.get("cidr") or meta.get("ip") or "IP")
        if entry.kind == "email":
            return str(meta.get("address") or "Email")
        if entry.kind == "color":
            return str(meta.get("hex") or "Colour")
        if entry.kind == "image":
            width = meta.get("width")
            height = meta.get("height")
            mime = (entry.mime or meta.get("mime") or "image").split("/")[-1].upper()
            if width and height:
                return f"Image · {width}×{height} · {mime}"
            return f"Image · {mime}"
        if entry.kind == "jwt":
            return "JSON Web Token"
        if entry.kind == "json":
            return str(meta.get("detail") or "JSON")
        if entry.kind == "diff":
            return f"{meta.get('files', 0)} files changed"
        if entry.kind == "code":
            lang = entry.language or meta.get("language")
            return f"{lang or 'Code'} snippet"
        return "Plain text"

    def _action_button(self, action, entry: Entry, primary, index: int) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("action")
        if primary is not None and action.id == primary.id:
            button.add_css_class("primary")
        button.set_tooltip_text(f"{action.label}  Ctrl+{index + 1}")
        button.set_focus_on_click(False)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        if _icon_available(action.icon):
            icon = Gtk.Image.new_from_icon_name(action.icon)
            icon.set_pixel_size(15)
            content.append(icon)
        label = Gtk.Label(label=action.label)
        content.append(label)
        button.set_child(content)
        button.connect(
            "clicked",
            lambda _b: self._run_action(entry.id, action.id),
        )
        return button

    # -- interactions ---------------------------------------------------

    def _on_search_changed(self, _entry) -> None:
        self._refresh(select_first=True)

    def _on_filter_clicked(self, button: Gtk.ToggleButton, key: str) -> None:
        if not button.get_active():
            button.set_active(True)
            return
        self._filter = key
        for name, other in self._filter_buttons.items():
            other.set_active(name == key)
        self._refresh(select_first=True)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for name, button in self._filter_buttons.items():
            button.set_active(name == key)
        self._refresh(select_first=True)

    def _cycle_filter(self, delta: int) -> None:
        keys = [key for key, _label in FILTER_KINDS]
        try:
            index = keys.index(self._filter)
        except ValueError:
            index = 0
        self._set_filter(keys[(index + delta) % len(keys)])

    def select_row(self, row: EntryRow) -> None:
        for other in self._visible_rows:
            other.remove_css_class("active")
            if other is not row:
                other.more_button.set_opacity(0.0)
        row.add_css_class("active")
        row.more_button.set_opacity(1.0)
        self._selected_id = row.entry_id
        self._update_detail(row.entry_id)
        self._ensure_visible(row)

    def restore_entry(self, entry_id: int) -> None:
        self._restore(entry_id)

    def _selected_row(self) -> EntryRow | None:
        if self._selected_id is None:
            return None
        return next(
            (row for row in self._visible_rows if row.entry_id == self._selected_id), None
        )

    def _select_delta(self, delta: int) -> None:
        if not self._visible_rows:
            return
        row = self._selected_row()
        index = 0
        if row is not None:
            try:
                index = self._visible_rows.index(row)
            except ValueError:
                index = 0
        index = max(0, min(len(self._visible_rows) - 1, index + delta))
        target = self._visible_rows[index]
        self.select_row(target)

    def _select_first_last(self, last: bool) -> None:
        if not self._visible_rows:
            return
        target = self._visible_rows[-1] if last else self._visible_rows[0]
        self.select_row(target)

    def _ensure_visible(self, row: EntryRow) -> None:
        def adjust() -> bool:
            adj = self.list_scroll.get_vadjustment()
            if adj is None:
                return False
            alloc = row.get_allocation()
            top = adj.get_value()
            bottom = top + adj.get_page_size()
            if alloc.y < top:
                adj.set_value(alloc.y)
            elif alloc.y + alloc.height > bottom:
                adj.set_value(alloc.y + alloc.height - adj.get_page_size())
            return False

        GLib.idle_add(adjust)

    def _primary_activate(self) -> None:
        if self.config.ui.primary_action == "context":
            self._run_context()
        else:
            self._restore_selected()

    def _restore_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._restore(row.entry_id)

    def _restore(self, entry_id: int) -> None:
        self.daemon.restore(entry_id)

    def _run_context(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        entry = self.daemon.store.get(row.entry_id)
        if entry is None:
            return
        action = primary_action(entry, self.config)
        if action is None:
            self._restore(row.entry_id)
            return
        self._run_action(row.entry_id, action.id)

    def _run_action(self, entry_id: int, action_id: str) -> None:
        response = self.daemon.run_action(entry_id, action_id)
        if not response.get("ok"):
            self.notify(str(response.get("error", "Action failed")))
        if self.window.get_visible() and not response.get("close"):
            self._refresh()

    def _action_at(self, index: int) -> None:
        if 0 <= index < len(self._actions):
            self._run_action(self._selected_id or 0, self._actions[index].id)

    def _pin_selected(self) -> None:
        if self._selected_id:
            self.daemon.pin(self._selected_id)

    def _delete_selected(self) -> None:
        if not self._selected_id:
            return
        self.daemon.delete(self._selected_id)
        self._selected_id = None
        self._refresh(select_first=True)

    def _toggle_pause(self) -> None:
        if self.daemon.paused:
            self.daemon.resume("manual")
            self.notify("Capture resumed")
        else:
            self.daemon.pause("manual")
            self.notify("Capture paused")
        self.paused_label.set_visible(self.daemon.paused)

    def _show_help(self) -> None:
        if self._help_popover is None:
            popover = Gtk.Popover()
            popover.set_has_arrow(False)
            popover.set_parent(self.search)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.set_margin_start(12)
            box.set_margin_end(12)
            title = Gtk.Label(label=f"clipd {__version__}")
            title.add_css_class("help-title")
            title.set_xalign(0.0)
            box.append(title)
            grid = Gtk.Grid(row_spacing=3, column_spacing=14)
            for i, (keys, desc) in enumerate(HELP_ROWS):
                key = Gtk.Label(label=keys)
                key.add_css_class("help-key")
                key.set_xalign(0.0)
                text = Gtk.Label(label=desc)
                text.add_css_class("help-text")
                text.set_xalign(0.0)
                grid.attach(key, 0, i, 1, 1)
                grid.attach(text, 1, i, 1, 1)
            box.append(grid)
            popover.set_child(box)
            self._help_popover = popover
        if self._help_popover.get_visible():
            self._help_popover.popdown()
        else:
            self._help_popover.popup()

    def show_row_menu(self, button: Gtk.Button, summary: Entry) -> None:
        if self._row_popover:
            self._row_popover.popdown()
        entry = self.daemon.store.get(summary.id) or summary
        popover = Gtk.Popover()
        popover.set_has_arrow(False)
        popover.set_parent(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        box.set_margin_start(5)
        box.set_margin_end(5)
        for action in actions_for(entry, self.config):
            item = Gtk.Button()
            item.add_css_class("popover-action")
            item.set_focus_on_click(False)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if _icon_available(action.icon):
                icon = Gtk.Image.new_from_icon_name(action.icon)
                icon.set_pixel_size(13)
                content.append(icon)
            content.append(Gtk.Label(label=action.label))
            item.set_child(content)
            item.connect("clicked", lambda _b, aid=action.id: self._popover_action(popover, entry.id, aid))
            box.append(item)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        pin_label = "Unpin" if entry.pinned else "Pin"
        pin = Gtk.Button(label=pin_label)
        pin.add_css_class("popover-action")
        pin.set_focus_on_click(False)
        pin.connect("clicked", lambda _b: self._popover_pin(popover, entry.id))
        box.append(pin)
        delete = Gtk.Button(label="Delete")
        delete.add_css_class("popover-action")
        delete.set_focus_on_click(False)
        delete.connect("clicked", lambda _b: self._popover_delete(popover, entry.id))
        box.append(delete)
        popover.set_child(box)
        self._row_popover = popover
        popover.connect("closed", lambda _p: setattr(self, "_row_popover", None))
        popover.popup()

    def _popover_action(self, popover: Gtk.Popover, entry_id: int, action_id: str) -> None:
        popover.popdown()
        GLib.idle_add(lambda: (self._run_action(entry_id, action_id), False)[1])

    def _popover_pin(self, popover: Gtk.Popover, entry_id: int) -> None:
        popover.popdown()
        GLib.idle_add(lambda: (self.daemon.pin(entry_id), False)[1])

    def _popover_delete(self, popover: Gtk.Popover, entry_id: int) -> None:
        popover.popdown()
        GLib.idle_add(lambda: (self.daemon.delete(entry_id), self._refresh(select_first=True), False)[2])

    # -- key handling ---------------------------------------------------

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if self._prompt_loop is not None:
            if keyval == Gdk.KEY_Escape:
                self._prompt_finish(None)
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._prompt_accept()
                return True
            return False

        if self._settings_visible():
            if keyval == Gdk.KEY_Escape:
                self._show_main()
                return True
            if ctrl and keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._save_settings()
                return True
            if ctrl and keyval == Gdk.KEY_comma:
                self._show_main()
                return True
            return False
        if ctrl and keyval == Gdk.KEY_comma:
            self._show_settings()
            return True
        if keyval == Gdk.KEY_Escape:
            if self._help_popover and self._help_popover.get_visible():
                self._help_popover.popdown()
            elif self._row_popover and self._row_popover.get_visible():
                self._row_popover.popdown()
            else:
                self.hide()
            return True
        if keyval == Gdk.KEY_F1:
            self._show_help()
            return True
        if ctrl and keyval in (Gdk.KEY_question, Gdk.KEY_slash):
            self._show_help()
            return True
        if ctrl and keyval == Gdk.KEY_l:
            self.search.grab_focus()
            self.search.select_region(0, -1)
            return True
        if ctrl and shift and keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self._toggle_pause()
            return True
        if ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self._pin_selected()
            return True
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.daemon.undo()
            self._refresh(select_first=True)
            return True
        if ctrl and keyval in (Gdk.KEY_d, Gdk.KEY_D):
            self._delete_selected()
            return True
        if ctrl and Gdk.KEY_1 <= keyval <= Gdk.KEY_6:
            self._action_at(keyval - Gdk.KEY_1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if ctrl:
                self._run_context()
            else:
                self._primary_activate()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_k):
            if keyval == Gdk.KEY_k and self._search_focused():
                return False
            self._select_delta(-1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_j):
            if keyval == Gdk.KEY_j and self._search_focused():
                return False
            self._select_delta(1)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self._select_delta(-6)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self._select_delta(6)
            return True
        if keyval == Gdk.KEY_Home:
            self._select_first_last(False)
            return True
        if keyval == Gdk.KEY_End:
            self._select_first_last(True)
            return True
        if keyval == Gdk.KEY_Tab:
            self._cycle_filter(-1 if shift else 1)
            return True
        if keyval == Gdk.KEY_Delete and not self._search_focused():
            self._delete_selected()
            return True
        return False

    # -- notifications --------------------------------------------------

    def notify(self, message: str) -> None:
        self.toast_label.set_text(message)
        self.toast_revealer.set_reveal_child(True)
        if self._toast_timeout is not None:
            try:
                GLib.source_remove(self._toast_timeout)
            except GLib.Error:
                pass
        self._toast_timeout = GLib.timeout_add(2400, self._hide_toast)

    def _hide_toast(self) -> bool:
        self.toast_revealer.set_reveal_child(False)
        self._toast_timeout = None
        return False

    # -- prompts --------------------------------------------------------

    def prompt_text(self, title: str, default: str) -> str | None:
        if self._prompt_loop is not None:
            return None
        self._prompt_result = None
        self._prompt_loop = GLib.MainLoop()
        self._dialog_open = True
        self.prompt_label.set_text(title)
        self.prompt_entry.set_text(default)
        self.prompt_bar.set_visible(True)
        self.prompt_entry.grab_focus()
        self.prompt_entry.select_region(0, -1)
        self._prompt_loop.run()
        result = self._prompt_result
        self._prompt_result = None
        self._prompt_loop = None
        self._dialog_open = False
        self.search.grab_focus()
        return result

    def _prompt_finish(self, value: str | None) -> None:
        if self._prompt_loop is None:
            return
        self._prompt_result = value
        self.prompt_bar.set_visible(False)
        loop = self._prompt_loop
        self._prompt_loop = None
        loop.quit()
