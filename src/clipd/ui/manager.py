from __future__ import annotations

import logging
import subprocess
import threading

from gi.repository import GLib

from ..actions import primary_action
from ..config import Config
from ..search import search
from . import fallback

log = logging.getLogger("clipd.ui")


def gtk_available() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gtk4LayerShell  # noqa: F401

        return Gdk.Display.get_default() is not None
    except Exception:
        return False


class UIManager:
    def __init__(self, daemon) -> None:
        self.daemon = daemon
        self.config: Config = daemon.config
        self.frontend = self._choose()
        self.gtk = None
        self._fallback_open = False
        self._fallback_proc: subprocess.Popen | None = None
        self._fallback_lock = threading.Lock()

    def _choose(self) -> str:
        requested = self.config.ui.frontend
        if requested == "none":
            return "none"
        if requested == "gtk":
            return "gtk" if gtk_available() else self._fallback_frontend()
        if requested == "rofi":
            return "rofi" if fallback.rofi_available() else "none"
        if requested == "fuzzel":
            return "fuzzel" if fallback.fuzzel_available() else "none"
        if gtk_available():
            return "gtk"
        return self._fallback_frontend()

    def _fallback_frontend(self) -> str:
        if fallback.rofi_available():
            return "rofi"
        if fallback.fuzzel_available():
            return "fuzzel"
        return "none"

    def start(self) -> None:
        if self.frontend == "gtk":
            try:
                from .gtk import GTKUI

                self.gtk = GTKUI(self.daemon)
                self.gtk.start()
                return
            except Exception as exc:
                log.warning("GTK frontend unavailable: %s", exc)
                self.gtk = None
                if self.config.ui.frontend == "auto":
                    self.frontend = self._fallback_frontend()
                else:
                    self.frontend = "none"
        if self.frontend == "none":
            log.warning("no clipboard popup frontend available")
            return
        if self.frontend == "rofi" and not fallback.rofi_available():
            self.frontend = "fuzzel" if fallback.fuzzel_available() else "none"
        if self.frontend == "fuzzel" and not fallback.fuzzel_available():
            self.frontend = "rofi" if fallback.rofi_available() else "none"

    def toggle(self, force_show: bool = False) -> None:
        if self.gtk is not None:
            self.gtk.toggle(force_show=force_show)
            return
        if self.frontend == "none":
            self.daemon.notify("No popup frontend available")
            return
        if self._fallback_open and not force_show:
            self.hide()
            return
        if self._fallback_open:
            return
        self._fallback_open = True
        thread = threading.Thread(target=self._fallback_worker, daemon=True, name="clipd-menu")
        thread.start()

    def show(self) -> None:
        self.toggle(force_show=True)

    def hide(self) -> None:
        if self.gtk is not None:
            self.gtk.hide()
            return
        with self._fallback_lock:
            proc = self._fallback_proc
            self._fallback_open = False
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def is_visible(self) -> bool:
        if self.gtk is not None:
            return self.gtk.is_visible()
        return self._fallback_open

    def notify(self, message: str) -> None:
        if self.gtk is not None and self.gtk.is_visible():
            self.gtk.notify(message)

    def refresh(self) -> None:
        if self.gtk is not None:
            self.gtk.refresh()

    def reload(self, config: Config) -> None:
        self.config = config
        if self.gtk is not None:
            self.gtk.reload(config)
        # Frontend changes need a restart; keep the current one for stability.

    def prompt_text(self, title: str, default: str) -> str | None:
        if self.gtk is not None:
            return self.gtk.prompt_text(title, default)
        if self.frontend == "rofi":
            return fallback.prompt_rofi(title, default)
        if self.frontend == "fuzzel":
            return fallback.prompt_fuzzel(title, default)
        return None

    # -- fallback -------------------------------------------------------

    def _set_proc(self, proc: subprocess.Popen | None) -> None:
        with self._fallback_lock:
            self._fallback_proc = proc

    def _fallback_worker(self) -> None:
        box: dict[str, list] = {"entries": []}
        ready = threading.Event()

        def fetch() -> bool:
            box["entries"] = search(self.daemon.store.list_summaries(), "")
            ready.set()
            return False

        GLib.idle_add(fetch)
        if not ready.wait(timeout=3.0):
            GLib.idle_add(self._fallback_empty)
            return
        entries = box["entries"]
        if not entries:
            GLib.idle_add(self._fallback_empty)
            return
        if self.frontend == "rofi":
            result = fallback.show_rofi(entries, on_proc=self._set_proc)
        else:
            result = fallback.show_fuzzel(entries, on_proc=self._set_proc)
        GLib.idle_add(self._fallback_done, result)

    def _fallback_empty(self) -> bool:
        self._fallback_open = False
        self.daemon.notify("Clipboard history is empty")
        return False

    def _fallback_done(self, result) -> bool:
        with self._fallback_lock:
            self._fallback_proc = None
            self._fallback_open = False
        if result is None or result.cancelled or result.entry is None:
            return False
        entry = self.daemon.store.get(result.entry.id) or result.entry
        if result.action == "pin":
            self.daemon.pin(entry.id)
        elif result.action == "delete":
            self.daemon.delete(entry.id)
        elif result.action == "context":
            action = primary_action(entry, self.daemon.config)
            if action is not None:
                self.daemon.run_action(entry.id, action.id)
            else:
                self.daemon.restore(entry.id)
        else:
            self.daemon.restore(entry.id)
        return False
