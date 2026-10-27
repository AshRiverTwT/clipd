from __future__ import annotations

import base64
import binascii
import logging
import os
import shutil
import signal
import threading
import time
from pathlib import Path

from gi.repository import GLib

from . import __version__, spawn, util
from .actions import ActionContext, action_by_id
from .classify import detect as classify_mod
from .classify import secrets
from .clipboard import ClipboardWatcher, ClipboardWriter, image_mime
from .config import Config, load_config
from .ipc import IPCServer
from .models import KIND_LABELS, Detection, Entry
from .store import Store, StoreError

log = logging.getLogger("clipd")


def open_store(path: Path, max_entries: int) -> Store:
    try:
        return Store(path, max_entries=max_entries)
    except StoreError as exc:
        backup = Path(str(path) + f".corrupt-{int(time.time())}")
        try:
            if path.exists():
                shutil.move(str(path), str(backup))
            for suffix in ("-wal", "-shm"):
                extra = Path(str(path) + suffix)
                if extra.exists():
                    shutil.move(str(extra), str(backup) + suffix)
            log.error("database was corrupt (%s); moved to %s", exc, backup)
        except OSError:
            raise
        return Store(path, max_entries=max_entries)


class Daemon:
    def __init__(
        self,
        config: Config | None = None,
        *,
        config_path: Path | None = None,
        headless: bool = False,
    ) -> None:
        self.config = config or load_config(config_path, create=True)
        if headless:
            self.config.ui.frontend = "none"
        self.store = open_store(util.db_path(), self.config.general.max_entries)
        self.writer = ClipboardWriter()
        self.watcher = ClipboardWatcher()
        self.ipc = IPCServer(self.handle_command)
        self.loop = GLib.MainLoop()
        self.ui = None
        self.paused = False
        self._paused_for_lock = False
        self._manually_paused = False
        self._last_active: dict | None = None
        self._deleted: list[Entry] = []
        self._watch_attempts = 0
        self._watch_next = 0.0
        self._watcher_started_at = 0.0
        self._ignore_initial = False
        self._started_at = time.time()
        self.last_event = 0.0
        self._lock_proxy = None
        self._shutting_down = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        self.ipc.start()
        if not self.paused:
            self._start_watcher()
        from .ui.manager import UIManager

        self.ui = UIManager(self)
        self.ui.start()
        GLib.timeout_add_seconds(2, self._watchdog)
        if self.config.privacy.pause_on_lock:
            GLib.idle_add(self._setup_lock_listener)
        log.info("clipd %s started (frontend=%s)", __version__, self.ui.frontend)
        for warning in self.config.warnings:
            log.warning("config: %s", warning)

    def run(self) -> int:
        self.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
        try:
            self.loop.run()
        finally:
            self.shutdown()
        return 0

    def _on_signal(self) -> bool:
        self.loop.quit()
        return False

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        try:
            self.watcher.stop()
        finally:
            self.ipc.stop()
            self.store.close()

    def _watchdog(self) -> bool:
        if self.paused:
            return True
        now = time.monotonic()
        if not self.watcher.is_alive() and now >= self._watch_next:
            self._watch_attempts += 1
            delay = min(10.0, 0.5 * (2 ** min(self._watch_attempts, 4)))
            self._watch_next = now + delay
            if self._start_watcher():
                log.warning("clipboard watcher restarted")
            else:
                log.error("could not start clipboard watcher")
        return True

    def _start_watcher(self) -> bool:
        started = self.watcher.start()
        if started:
            self._watcher_started_at = time.monotonic()
            self._ignore_initial = True
        return started

    # -- commands -------------------------------------------------------

    def handle_command(self, payload: dict) -> dict:
        cmd = payload.get("cmd")
        if not isinstance(cmd, str):
            return {"ok": False, "error": "missing command"}
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "ingest":
            return self.ingest(payload)
        if cmd == "ingest_image":
            return self.ingest_image(payload)
        if cmd == "notice":
            return self.notice(payload)
        if cmd == "toggle":
            return self.toggle()
        if cmd == "show":
            return self.toggle(force_show=True)
        if cmd == "hide":
            if self.ui:
                self.ui.hide()
            return {"ok": True}
        if cmd == "status":
            return {"ok": True, "status": self.status()}
        if cmd == "pause":
            self.pause("manual")
            return {"ok": True, "paused": True}
        if cmd == "resume":
            self.resume("manual")
            return {"ok": True, "paused": False}
        if cmd == "reload":
            return self.reload()
        if cmd == "restore":
            return self.restore(int(payload.get("id", 0)), paste=bool(payload.get("paste")))
        if cmd == "pin":
            entry_id = int(payload.get("id", 0))
            value = bool(payload.get("pinned"))
            self.store.set_pinned(entry_id, value)
            self.refresh()
            return {"ok": True, "pinned": value}
        if cmd == "delete":
            return self.delete(int(payload.get("id", 0)))
        if cmd == "undo":
            return self.undo()
        if cmd == "clear":
            count = self.store.clear(keep_pinned=bool(payload.get("keep_pinned", True)))
            self.refresh()
            return {"ok": True, "cleared": count}
        if cmd == "action":
            return self.run_action(int(payload.get("id", 0)), str(payload.get("action", "")))
        if cmd == "quit":
            GLib.idle_add(self._quit)
            return {"ok": True}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    def _quit(self) -> bool:
        self.loop.quit()
        return False

    # -- ingest ---------------------------------------------------------

    def ingest(self, payload: dict) -> dict:
        if self.paused:
            return {"ok": True, "skipped": "paused"}
        if self._ignore_initial:
            if time.monotonic() - self._watcher_started_at < 0.75:
                self._ignore_initial = False
                return {"ok": True, "skipped": "initial selection"}
            self._ignore_initial = False
        text = payload.get("text")
        if not isinstance(text, str):
            return {"ok": False, "error": "invalid text"}
        raw = text.encode("utf-8", "replace")
        if len(raw) > self.config.general.max_entry_bytes:
            self.store.bump_stat("skipped_large")
            self.notify("Skipped a large clipboard entry")
            return {"ok": True, "skipped": "large"}
        if "\x00" in text:
            self.store.bump_stat("skipped_binary")
            return {"ok": True, "skipped": "binary"}
        digest = payload.get("hash") if isinstance(payload.get("hash"), str) else util.sha256_text(text)
        if self.writer.was_recent(digest, window=2.0):
            return {"ok": True, "skipped": "self-write"}
        mime = payload.get("mime") if isinstance(payload.get("mime"), list) else []
        mime = [str(m) for m in mime][:32]
        if secrets.find_secret(text):
            self.store.bump_stat("skipped_sensitive")
            log.info("sensitive entry not stored (%s bytes)", len(raw))
            return {"ok": True, "skipped": "sensitive"}
        reason = self.config.is_excluded(text, mime)
        if reason:
            self.store.bump_stat("skipped_excluded")
            log.info("entry not stored: %s", reason)
            return {"ok": True, "skipped": reason}

        detection = self._detection_from_payload(payload, text)
        result = self.store.add(
            text,
            detection,
            max_entry_bytes=self.config.general.max_entry_bytes,
        )
        self.last_event = time.time()
        self.refresh()
        return {
            "ok": True,
            "created": result.created,
            "id": result.entry.id,
        }

    def ingest_image(self, payload: dict) -> dict:
        if self.paused:
            return {"ok": True, "skipped": "paused"}
        if self._ignore_initial:
            if time.monotonic() - self._watcher_started_at < 0.75:
                self._ignore_initial = False
                return {"ok": True, "skipped": "initial selection"}
            self._ignore_initial = False
        encoded = payload.get("data")
        if not isinstance(encoded, str):
            return {"ok": False, "error": "invalid image payload"}
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "invalid image data"}
        if not data or len(data) > self.config.general.max_image_bytes:
            self.store.bump_stat("skipped_large")
            self.notify("Skipped a large image")
            return {"ok": True, "skipped": "large"}
        mime = image_mime(data)
        if mime is None:
            self.store.bump_stat("skipped_binary")
            return {"ok": True, "skipped": "binary"}
        if mime.lower() in {m.lower() for m in self.config.privacy.exclude_mime}:
            self.store.bump_stat("skipped_excluded")
            return {"ok": True, "skipped": "excluded MIME type"}
        digest = payload.get("hash") if isinstance(payload.get("hash"), str) else util.sha256_bytes(data)
        if self.writer.was_recent(digest, window=2.0):
            return {"ok": True, "skipped": "self-write"}
        width, height = util.image_dimensions(data)
        meta = {
            "mime": mime,
            "bytes": len(data),
            "width": width,
            "height": height,
        }
        detection = Detection("image", 1.0, meta, language=None)
        result = self.store.add("", detection, image_data=data, mime=mime)
        self.last_event = time.time()
        self.refresh()
        return {"ok": True, "created": result.created, "id": result.entry.id, "kind": "image"}

    def _detection_from_payload(self, payload: dict, text: str) -> Detection:
        raw = payload.get("detection")
        if isinstance(raw, dict):
            kind = raw.get("kind")
            meta = raw.get("meta")
            language = raw.get("language")
            if isinstance(kind, str) and kind in KIND_LABELS and isinstance(meta, dict):
                try:
                    confidence = float(raw.get("confidence", 0.5))
                except (TypeError, ValueError):
                    confidence = 0.5
                return Detection(
                    kind=kind,
                    confidence=max(0.0, min(1.0, confidence)),
                    meta=meta,
                    language=language if isinstance(language, str) else None,
                )
        return classify_mod.classify(text)

    def notice(self, payload: dict) -> dict:
        kind = payload.get("kind")
        mapping = {
            "large": "skipped_large",
            "binary": "skipped_binary",
            "sensitive": "skipped_sensitive",
            "excluded": "skipped_excluded",
        }
        if kind in mapping:
            self.store.bump_stat(mapping[kind])
            if kind in {"large", "binary"} and self.ui and self.ui.is_visible():
                self.notify(
                    "Skipped a large clipboard entry"
                    if kind == "large"
                    else "Binary entry ignored"
                )
        return {"ok": True}

    # -- UI actions -----------------------------------------------------

    def toggle(self, force_show: bool = False) -> dict:
        if self.ui is None:
            return {"ok": False, "error": "no UI frontend available"}
        self._last_active = spawn.capture_active_window()
        self.ui.toggle(force_show=force_show)
        return {"ok": True, "frontend": self.ui.frontend}

    def restore(self, entry_id: int, paste: bool = False) -> dict:
        entry = self.store.get(entry_id)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        if entry.kind == "image" and entry.data:
            copied = self.writer.copy_image(entry.data, entry.mime or "image/png")
        else:
            copied = self.writer.copy(entry.text)
        if not copied:
            return {"ok": False, "error": "clipboard write failed"}
        self.store.touch(entry_id)
        if self.ui:
            self.ui.hide()
        self.notify("Copied")
        method = self.config.general.auto_paste
        if method != "off" and self._last_active and self._last_active.get("address"):
            address = str(self._last_active["address"])
            GLib.timeout_add(
                max(0, int(self.config.general.paste_delay_ms)),
                self._do_paste,
                address,
                method,
            )
        self.refresh()
        return {"ok": True, "id": entry_id}

    def _do_paste(self, address: str, method: str) -> bool:
        if not spawn.send_paste(address, method):
            self.notify("Auto-paste failed — press Ctrl+V")
        return False

    def run_action(self, entry_id: int, action_id: str) -> dict:
        entry = self.store.get(entry_id)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        action = action_by_id(entry, self.config, action_id)
        if action is None:
            return {"ok": False, "error": "action unavailable"}
        ctx = ActionContext(
            config=self.config,
            store=self.store,
            clipboard=self.writer,
            notify=self.notify,
            prompt_text=self.prompt_text,
            run_async=self.run_async,
            on_changed=self.refresh,
        )
        try:
            result = action.run(entry, ctx)
        except Exception as exc:
            log.exception("action %s failed", action.id)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if result.message:
            self.notify(result.message)
        if result.changed:
            self.refresh()
        if result.close and self.ui:
            self.ui.hide()
        return {"ok": result.ok, "message": result.message, "changed": result.changed}

    def prompt_text(self, title: str, default: str) -> str | None:
        if self.ui:
            return self.ui.prompt_text(title, default)
        return None

    def run_async(self, work, done) -> None:
        def runner() -> None:
            try:
                ok, message = work()
            except Exception as exc:  # pragma: no cover - defensive
                ok, message = False, f"{type(exc).__name__}: {exc}"

            def finish() -> bool:
                done(ok, message)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=runner, daemon=True, name="clipd-action").start()

    def pin(self, entry_id: int, pinned: bool | None = None) -> dict:
        entry = self.store.get_summary(entry_id)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        value = (not entry.pinned) if pinned is None else bool(pinned)
        self.store.set_pinned(entry_id, value)
        self.refresh()
        self.notify("Pinned" if value else "Unpinned")
        return {"ok": True, "pinned": value}

    def delete(self, entry_id: int) -> dict:
        entry = self.store.delete(entry_id)
        if entry is None:
            return {"ok": False, "error": "entry not found"}
        self._deleted.append(entry)
        del self._deleted[:-10]
        self.refresh()
        self.notify("Deleted — Ctrl+Z to undo")
        return {"ok": True}

    def undo(self) -> dict:
        if not self._deleted:
            return {"ok": False, "error": "nothing to undo"}
        entry = self._deleted.pop()
        restored = self.store.restore(entry)
        self.refresh()
        self.notify("Restored")
        return {"ok": True, "id": restored.id if restored else None}

    def refresh(self) -> None:
        if self.ui:
            self.ui.refresh()

    def notify(self, message: str) -> None:
        log.info("%s", message)
        if self.ui and self.ui.is_visible():
            self.ui.notify(message)

    # -- pause / reload ------------------------------------------------

    def pause(self, reason: str = "manual") -> None:
        if reason == "lock":
            self._paused_for_lock = True
        else:
            self._manually_paused = True
        self.paused = True
        self.watcher.stop()
        log.info("clipboard capture paused (%s)", reason)

    def resume(self, reason: str = "manual") -> None:
        if reason == "lock":
            self._paused_for_lock = False
        else:
            self._manually_paused = False
        self.paused = self._manually_paused or self._paused_for_lock
        if not self.paused:
            self._start_watcher()
            self._watch_attempts = 0
        log.info("clipboard capture resumed (%s)", reason)

    def reload(self) -> dict:
        cfg = load_config(self.config.path)
        self.config = cfg
        self.store.max_entries = cfg.general.max_entries
        self.store.trim()
        if self.ui:
            self.ui.reload(cfg)
        self.refresh()
        return {"ok": True, "warnings": cfg.warnings}

    def _setup_lock_listener(self) -> bool:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.login1",
                "/org/freedesktop/login1/session/auto",
                "org.freedesktop.login1.Session",
                None,
            )
            if proxy is None:
                return False
            self._lock_proxy = proxy

            def read_locked(value) -> bool:
                if value is None:
                    return False
                try:
                    return bool(value.get_boolean())
                except AttributeError:
                    return bool(value)

            try:
                initial = proxy.get_cached_property("LockedHint")
                if read_locked(initial):
                    self.pause("lock")
            except Exception:
                pass

            def on_props(_proxy, changed, _invalidated) -> None:
                try:
                    variant = changed.lookup_value("LockedHint", None)
                except Exception:
                    return
                if read_locked(variant):
                    self.pause("lock")
                else:
                    self.resume("lock")

            proxy.connect("g-properties-changed", on_props)
            return False
        except Exception as exc:
            log.debug("lock listener unavailable: %s", exc)
            return False

    # -- status ---------------------------------------------------------

    def status(self) -> dict:
        stats = self.store.stats()
        return {
            "version": __version__,
            "pid": os.getpid(),
            "paused": self.paused,
            "frontend": self.ui.frontend if self.ui else "none",
            "db": str(self.store.path),
            "socket": str(self.ipc.path),
            "entries": stats.get("entries", 0),
            "pinned": stats.get("pinned", 0),
            "skipped_sensitive": stats.get("skipped_sensitive", 0),
            "skipped_large": stats.get("skipped_large", 0),
            "skipped_binary": stats.get("skipped_binary", 0),
            "skipped_excluded": stats.get("skipped_excluded", 0),
            "uptime": int(time.time() - self._started_at),
            "last_event": int(self.last_event) if self.last_event else None,
            "warnings": self.config.warnings,
        }
