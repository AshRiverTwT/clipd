from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, clipboard, spawn, util
from .classify import detect as classify_mod
from .classify import secrets
from .config import load_config
from .daemon import Daemon, open_store
from .ipc import ClientError, daemon_running, send_command
from .store import Store

log = logging.getLogger("clipd")


def _print_status(daemon_status: dict) -> None:
    print(f"clipd {daemon_status.get('version', __version__)}")
    print(f"  running   : yes")
    print(f"  frontend  : {daemon_status.get('frontend')}")
    print(f"  paused    : {'yes' if daemon_status.get('paused') else 'no'}")
    print(f"  entries   : {daemon_status.get('entries', 0)} ({daemon_status.get('pinned', 0)} pinned)")
    print(f"  database  : {daemon_status.get('db')}")
    print(f"  socket    : {daemon_status.get('socket')}")
    print(
        "  skipped   : "
        f"{daemon_status.get('skipped_sensitive', 0)} sensitive, "
        f"{daemon_status.get('skipped_large', 0)} large, "
        f"{daemon_status.get('skipped_binary', 0)} binary, "
        f"{daemon_status.get('skipped_excluded', 0)} excluded"
    )
    warnings = daemon_status.get("warnings") or []
    for warning in warnings:
        print(f"  warning   : {warning}")


def _connect(payload: dict, quiet: bool = False) -> dict | None:
    try:
        response = send_command(payload)
    except ClientError as exc:
        if not quiet:
            print(f"clipd: {exc}", file=sys.stderr)
        return None
    if not response.get("ok"):
        if not quiet:
            print(f"clipd: {response.get('error', 'request failed')}", file=sys.stderr)
        return None
    return response


def _ensure_daemon() -> bool:
    if daemon_running():
        return True
    if os.environ.get("CLIPD_NO_START"):
        return False
    if shutil.which("systemctl") is None:
        return False
    try:
        subprocess.run(
            ["systemctl", "--user", "start", "clipd", "--no-block"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    for _ in range(12):
        time.sleep(0.1)
        if daemon_running():
            return True
    return False


def _ensure_preload(argv: list[str]) -> None:
    """Re-exec the daemon with gtk4-layer-shell preloaded (required on Arch)."""
    if os.environ.get("CLIPD_NO_PRELOAD"):
        return
    if "libgtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return
    if not _gtk_layer_shell_lib():
        return
    env = os.environ.copy()
    env["LD_PRELOAD"] = _gtk_layer_shell_lib() + (
        ":" + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else ""
    )
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    os.execvpe(sys.executable, [sys.executable, "-m", "clipd", *argv], env)


def _gtk_layer_shell_lib() -> str | None:
    candidates = [
        "/usr/lib/libgtk4-layer-shell.so",
        "/usr/lib64/libgtk4-layer-shell.so",
        "/usr/lib/libgtk4-layer-shell.so.0",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def cmd_daemon(args) -> int:
    _ensure_preload(sys.argv[1:])
    config = load_config(args.config, create=True)
    try:
        daemon = Daemon(config, headless=args.headless)
        return daemon.run()
    except Exception as exc:
        print(f"clipd: cannot start: {exc}", file=sys.stderr)
        return 1


def cmd_toggle(_args) -> int:
    if not _ensure_daemon():
        print("clipd: daemon is not running (systemctl --user start clipd)", file=sys.stderr)
        return 1
    return 0 if _connect({"cmd": "toggle"}) else 1


def cmd_show(_args) -> int:
    if not _ensure_daemon():
        return 1
    return 0 if _connect({"cmd": "show"}) else 1


def cmd_hide(_args) -> int:
    return 0 if _connect({"cmd": "hide"}, quiet=True) else 0


def cmd_pause(_args) -> int:
    return 0 if _connect({"cmd": "pause"}) else 1


def cmd_resume(_args) -> int:
    return 0 if _connect({"cmd": "resume"}) else 1


def cmd_reload(_args) -> int:
    response = _connect({"cmd": "reload"})
    if response is None:
        return 1
    for warning in response.get("warnings") or []:
        print(f"clipd: warning: {warning}", file=sys.stderr)
    print("reloaded")
    return 0


def cmd_status(args) -> int:
    response = _connect({"cmd": "status"}, quiet=True)
    if response:
        status = response["status"]
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            _print_status(status)
        return 0
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    stats = store.stats()
    store.close()
    status = {
        "version": __version__,
        "running": False,
        "paused": False,
        "frontend": "none",
        "db": str(util.db_path()),
        "socket": str(util.socket_path()),
        "entries": stats.get("entries", 0),
        "pinned": stats.get("pinned", 0),
        "skipped_sensitive": stats.get("skipped_sensitive", 0),
        "skipped_large": stats.get("skipped_large", 0),
        "skipped_binary": stats.get("skipped_binary", 0),
        "skipped_excluded": stats.get("skipped_excluded", 0),
        "warnings": config.warnings,
    }
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"clipd {__version__}")
        print("  running   : no")
        print(f"  entries   : {status['entries']} ({status['pinned']} pinned)")
        print(f"  database  : {status['db']}")
    return 0


def cmd_list(args) -> int:
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    entries = store.list_summaries(kind=args.kind)
    if args.limit:
        entries = entries[: args.limit]
    if args.json:
        payload = []
        for entry in entries:
            payload.append(
                {
                    "id": entry.id,
                    "kind": entry.kind,
                    "preview": entry.preview,
                    "size": entry.size,
                    "pinned": entry.pinned,
                    "created_at": entry.created_at,
                    "last_used_at": entry.last_used_at,
                    "use_count": entry.use_count,
                    "hash": entry.hash,
                    "meta": entry.meta,
                    "mime": entry.mime,
                }
            )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for entry in entries:
            pin = "*" if entry.pinned else " "
            print(
                f"{entry.id:>3} {pin} {entry.kind:<10} "
                f"{util.human_size(entry.size):>7}  {entry.preview}"
            )
    store.close()
    return 0


def cmd_clear(args) -> int:
    payload = {"cmd": "clear", "keep_pinned": not args.all}
    if _connect(payload, quiet=True):
        print("cleared")
        return 0
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    count = store.clear(keep_pinned=not args.all)
    store.vacuum()
    store.close()
    print(f"cleared {count} entries")
    return 0


def cmd_restore(args) -> int:
    response = _connect({"cmd": "restore", "id": args.id}, quiet=True)
    if response:
        return 0
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    entry = store.get(args.id)
    if entry is None:
        print(f"clipd: no entry {args.id}", file=sys.stderr)
        store.close()
        return 1
    writer = clipboard.ClipboardWriter()
    if entry.kind == "image" and entry.data:
        copied = writer.copy_image(entry.data, entry.mime or "image/png")
    else:
        copied = writer.copy(entry.text)
    if not copied:
        print("clipd: could not write clipboard", file=sys.stderr)
        store.close()
        return 1
    store.touch(entry.id)
    store.close()
    print("copied")
    return 0


def cmd_action(args) -> int:
    return 0 if _connect({"cmd": "action", "id": args.id, "action": args.action}) else 1


def cmd_pin(args) -> int:
    response = _connect({"cmd": "pin", "id": args.id, "pinned": not args.unpin}, quiet=True)
    if response:
        return 0
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    store.set_pinned(args.id, not args.unpin)
    store.close()
    return 0


def cmd_delete(args) -> int:
    response = _connect({"cmd": "delete", "id": args.id}, quiet=True)
    if response:
        return 0
    config = load_config(args.config)
    store = open_store(util.db_path(), config.general.max_entries)
    entry = store.delete(args.id)
    store.close()
    return 0 if entry else 1


def cmd_undo(_args) -> int:
    return 0 if _connect({"cmd": "undo"}) else 1


# -- ingest -------------------------------------------------------------


def _read_stdin_capped(limit: int, timeout_ms: int) -> tuple[bytes, bool]:
    def on_alarm(_signum, _frame):
        raise TimeoutError("clipboard read timed out")

    old = signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, max(0.25, timeout_ms / 1000.0))
    try:
        data = sys.stdin.buffer.read(limit + 1)
        return data, len(data) > limit
    except TimeoutError:
        return b"", False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _looks_like_html_image(data: bytes | str) -> bool:
    """Recognize the HTML fragment browsers put beside copied images."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8", "replace")
        except Exception:
            return False
    else:
        text = data
    low = text.lstrip().lower()
    if low.startswith("<meta") and "http-equiv" in low and "<img" in low:
        return True
    # Some browsers hand over just the image tag with its source URL.
    return low.startswith("<img") and "src=" in low and len(low) < 8192


def _send_image(image_data: bytes, mime: str) -> None:
    payload = {
        "cmd": "ingest_image",
        "data": base64.b64encode(image_data).decode("ascii"),
        "mime": mime,
        "hash": util.sha256_bytes(image_data),
    }
    try:
        send_command(payload, timeout=10)
    except ClientError:
        pass


def cmd_ingest(_args) -> int:
    config = load_config(create=False)
    mime_types = clipboard.list_types()
    image_type = clipboard.preferred_image_type(mime_types)
    max_read = max(config.general.max_entry_bytes, config.general.max_image_bytes)
    data, oversized = _read_stdin_capped(max_read, config.watch.read_timeout_ms)

    # The watcher supplies the event bytes on stdin. Use those first so a
    # queued text event cannot accidentally read a newer image selection (or
    # vice versa). Fall back to the current selection only for image events
    # that arrive without usable stdin data.
    if image_type and data and clipboard.image_mime(data):
        if len(data) > config.general.max_image_bytes:
            _connect({"cmd": "notice", "kind": "large"}, quiet=True)
            return 0
        _send_image(data, clipboard.image_mime(data) or image_type)
        return 0
    # Browsers often hand wl-paste an HTML fragment for an image selection.
    # Prefer the actual image payload from the current selection in that case.
    if image_type and (not data or _looks_like_html_image(data)):
        image_data = clipboard.read_image(
            image_type,
            timeout=max(0.5, config.watch.read_timeout_ms / 1000.0),
            max_bytes=config.general.max_image_bytes,
        )
        if image_data:
            _send_image(image_data, clipboard.image_mime(image_data) or image_type)
            return 0
        if data:
            _connect({"cmd": "notice", "kind": "binary"}, quiet=True)
            return 0
    if oversized or len(data) > config.general.max_entry_bytes:
        _connect({"cmd": "notice", "kind": "large"}, quiet=True)
        return 0
    if not data:
        return 0
    if b"\x00" in data:
        _connect({"cmd": "notice", "kind": "binary"}, quiet=True)
        return 0
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _connect({"cmd": "notice", "kind": "binary"}, quiet=True)
        return 0
    if _looks_like_html_image(text):
        _connect({"cmd": "notice", "kind": "binary"}, quiet=True)
        return 0
    if any(m.lower() in {m2.lower() for m2 in config.privacy.exclude_mime} for m in mime_types):
        _connect({"cmd": "notice", "kind": "sensitive"}, quiet=True)
        return 0
    if secrets.find_secret(text):
        _connect({"cmd": "notice", "kind": "sensitive"}, quiet=True)
        return 0
    if config.is_excluded(text, mime_types):
        _connect({"cmd": "notice", "kind": "excluded"}, quiet=True)
        return 0
    detection = classify_mod.classify(text)
    payload = {
        "cmd": "ingest",
        "text": text,
        "mime": mime_types,
        "hash": util.sha256_text(text),
        "detection": {
            "kind": detection.kind,
            "confidence": detection.confidence,
            "meta": detection.meta,
            "language": detection.language,
        },
    }
    try:
        send_command(payload, timeout=5)
    except ClientError:
        return 0
    return 0

# -- doctor -------------------------------------------------------------


def _check(label: str, ok: bool, detail: str = "", warn: bool = False) -> bool:
    mark = "✓" if ok else ("!" if warn else "✗")
    suffix = f"  {detail}" if detail else ""
    print(f"  {mark} {label}{suffix}")
    return ok or warn


def _conflicting_cliphist() -> list[int]:
    pids: list[int] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\x00")
            text = b" ".join(argv).decode("utf-8", "replace")
        except (OSError, PermissionError):
            continue
        if "wl-paste" in text and "cliphist" in text and "store" in text:
            pids.append(int(proc.name))
    return pids


def _hypr_config_dir() -> Path:
    override = os.environ.get("CLIPD_HYPR_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "hypr"


def cmd_doctor(args) -> int:
    print(f"clipd {__version__} doctor")
    critical = True
    critical &= _check("wayland session", os.environ.get("XDG_SESSION_TYPE") == "wayland",
                       os.environ.get("XDG_SESSION_TYPE", "unknown"))
    critical &= _check("WAYLAND_DISPLAY", bool(os.environ.get("WAYLAND_DISPLAY")),
                       os.environ.get("WAYLAND_DISPLAY", ""))
    critical &= _check("wl-clipboard", clipboard.clipboard_available())
    critical &= _check("hyprctl", shutil.which("hyprctl") is not None)
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell  # noqa: F401

        gtk_ok = True
    except Exception:
        gtk_ok = False
    _check("GTK4 + gtk4-layer-shell", gtk_ok, "" if gtk_ok else "install gtk4 gtk4-layer-shell")
    lib = _gtk_layer_shell_lib()
    preload_ok = bool(lib and "libgtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""))
    _check(
        "layer-shell preload",
        preload_ok,
        "daemon re-execs with LD_PRELOAD automatically" if lib else "library not found",
        warn=True,
    )
    _check("rofi fallback", shutil.which("rofi") is not None, warn=True)
    _check("fuzzel fallback", shutil.which("fuzzel") is not None, warn=True)
    _check("git clone action", shutil.which("git") is not None, warn=True)
    _check("shellcheck action", shutil.which("shellcheck") is not None, warn=True)

    config = load_config(args.config)
    _check(
        "config parses",
        not config.warnings,
        "; ".join(config.warnings) if config.warnings else str(config.path),
    )
    if util.db_path().exists():
        mode = util.db_path().stat().st_mode & 0o777
        _check("database permissions", mode & 0o077 == 0, oct(mode))
    else:
        _check("database", True, f"{util.db_path()} will be created")
    if util.socket_path().exists():
        _check("daemon socket", daemon_running(), str(util.socket_path()))
    else:
        _check("daemon", False, "not running", warn=True)
    pids = _conflicting_cliphist()
    _check(
        "no cliphist watcher conflict",
        not pids,
        f"PIDs {', '.join(map(str, pids))}" if pids else "",
        warn=True,
    )
    hypr = _hypr_config_dir()
    if hypr.exists():
        keybind = _has_clipd_bind(hypr)
        _check(
            "Hyprland SUPER+V binding",
            keybind,
            "run: clipd setup --dry-run" if not keybind else "",
            warn=True,
        )
    else:
        _check("Hyprland config", False, str(hypr), warn=True)
    print("  done")
    return 0 if critical else 1


def _has_clipd_bind(hypr: Path) -> bool:
    for path in hypr.rglob("*"):
        if path.is_file() and path.suffix in {".lua", ".conf"}:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "clipd toggle" in text:
                return True
    return False


# -- setup --------------------------------------------------------------


CLIPD_LUA = """\
-- Generated by clipd setup. Safe to edit.
hl.bind("SUPER + V", hl.dsp.exec_cmd("clipd toggle"))

hl.layer_rule({
  match = { namespace = "clipd" },
  blur = true,
  ignore_alpha = 0.35,
  no_anim = true,
})

hl.on("hyprland.start", function()
  hl.exec_cmd("systemctl --user start clipd")
end)
"""


def _backup(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    backup = path.with_name(path.name + f".bak.{stamp}")
    if not backup.exists():
        shutil.copy2(path, backup)


def _write(path: Path, text: str, apply: bool, stamp: str) -> bool:
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(path, stamp)
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o644)
    return True


def _kill_cliphist(apply: bool) -> None:
    pids = _conflicting_cliphist()
    if not pids:
        return
    print(f"  cliphist watcher PIDs: {', '.join(map(str, pids))}")
    if not apply:
        print("  would stop those watchers")
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    print("  stopped cliphist watchers (database left untouched)")


def cmd_setup(args) -> int:
    apply = bool(args.apply)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    hypr = _hypr_config_dir()
    print(f"clipd setup{' (apply)' if apply else ' (dry run)'}  {hypr}")
    if not hypr.exists():
        print("  Hyprland config directory not found", file=sys.stderr)
        return 1
    lua_mode = (hypr / "hyprland.lua").exists()
    if lua_mode:
        config_dir = hypr / "config"
        clipd_lua = config_dir / "clipd.lua"
        keybinds = config_dir / "keybinds.lua"
        autostart = config_dir / "autostart.lua"
        has_v = False
        if keybinds.exists():
            try:
                has_v = bool(
                    re.search(
                        r'mainMod\s*\.\.\s*["\']\s*\+\s*V["\']|bind\s*=\s*.*,\s*V\s*,',
                        keybinds.read_text(encoding="utf-8", errors="replace"),
                    )
                )
            except OSError:
                pass
        content = CLIPD_LUA
        if has_v:
            content = "\n".join(
                line
                for line in CLIPD_LUA.splitlines()
                if 'hl.bind("SUPER + V"' not in line
            )
            print("  ! SUPER+V already bound outside clipd; leaving the existing binding")
        print(f"  write {clipd_lua}")
        _write(clipd_lua, content, apply, stamp)

        root = hypr / "hyprland.lua"
        try:
            root_text = root.read_text(encoding="utf-8", errors="replace")
        except OSError:
            root_text = ""
        require_line = 'require("config/clipd")'
        if require_line not in root_text:
            print(f"  add {require_line} to hyprland.lua")
            new_root = root_text.rstrip() + "\n\n" + require_line + "\n"
            _write(root, new_root, apply, stamp)
        if autostart.exists():
            try:
                original = autostart.read_text(encoding="utf-8", errors="replace")
            except OSError:
                original = ""
            changed_lines = []
            changed = False
            for line in original.splitlines():
                if "cliphist" in line and "wl-paste" in line and not line.lstrip().startswith("--"):
                    changed_lines.append("-- disabled by clipd: " + line.strip())
                    changed = True
                else:
                    changed_lines.append(line)
            if changed:
                print("  disable cliphist watchers in autostart.lua")
                _write(autostart, "\n".join(changed_lines) + "\n", apply, stamp)
        _kill_cliphist(apply)
    else:
        conf = hypr / "hyprland.conf"
        try:
            original = conf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            original = ""
        if "clipd toggle" not in original:
            snippet = (
                "\n# clipd\n"
                "bind = SUPER, V, exec, clipd toggle\n"
                "exec-once = systemctl --user start clipd\n"
                "layerrule = blur on, match:namespace clipd\n"
                "layerrule = ignore_alpha 0.35, match:namespace clipd\n"
            )
            print(f"  add clipd block to {conf}")
            _write(conf, original.rstrip() + snippet, apply, stamp)
        _kill_cliphist(apply)

    print("  note: SUPER+C / Quickshell clipboard is left untouched")
    if apply:
        print("  backups use the suffix .bak." + stamp)
        print("  reload Hyprland: hyprctl reload")
        print("  start now: systemctl --user daemon-reload && systemctl --user start clipd")
    else:
        print("  nothing changed; re-run with --apply")
    return 0


# -- import cliphist ----------------------------------------------------


def cmd_import(args) -> int:
    if shutil.which("cliphist") is None:
        print("cliphist is not installed", file=sys.stderr)
        return 1
    config = load_config(args.config)
    result = spawn.run(["cliphist", "list"], timeout=20)
    if not result.ok:
        print("could not read cliphist history", file=sys.stderr)
        return 1
    candidates: list[str] = []
    skipped = {"binary": 0, "sensitive": 0, "large": 0, "empty": 0}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        ident, preview = line.split("\t", 1)
        if "[[ binary data" in preview.lower():
            skipped["binary"] += 1
            continue
        decoded = spawn.run(["cliphist", "decode", ident], timeout=10)
        if not decoded.ok:
            skipped["binary"] += 1
            continue
        text = decoded.stdout
        if not text.strip():
            skipped["empty"] += 1
            continue
        if "\x00" in text:
            skipped["binary"] += 1
            continue
        if _looks_like_html_image(text):
            skipped["binary"] += 1
            continue
        if len(text.encode("utf-8", "replace")) > config.general.max_entry_bytes:
            skipped["large"] += 1
            continue
        if secrets.find_secret(text):
            skipped["sensitive"] += 1
            continue
        if config.is_excluded(text):
            skipped["empty"] += 1
            continue
        candidates.append(text)
        if len(candidates) >= args.limit:
            break
    print(
        f"cliphist: {len(candidates)} text entries, skipped "
        f"{skipped['binary']} binary, {skipped['sensitive']} sensitive, "
        f"{skipped['large']} large, {skipped['empty']} empty"
    )
    if not args.apply:
        for text in candidates[:5]:
            detection = classify_mod.classify(text)
            print(f"  {detection.kind:<10} {util.preview_text(text, 80)}")
        print("dry run; re-run with --apply to import")
        return 0
    store = open_store(util.db_path(), config.general.max_entries)
    imported = 0
    for text in candidates:
        store.add(text, classify_mod.classify(text))
        imported += 1
    store.close()
    print(f"imported {imported} entries")
    return 0


# -- parser -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipd", description="context-aware clipboard manager")
    parser.add_argument("--version", action="version", version=f"clipd {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="config file")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("daemon", help="run the background daemon")
    p.add_argument("--headless", action="store_true", help="no popup frontend")
    p.set_defaults(func=cmd_daemon)

    for name, func, help_text in (
        ("toggle", cmd_toggle, "toggle the popup"),
        ("show", cmd_show, "show the popup"),
        ("hide", cmd_hide, "hide the popup"),
        ("pause", cmd_pause, "pause clipboard capture"),
        ("resume", cmd_resume, "resume clipboard capture"),
        ("reload", cmd_reload, "reload configuration"),
        ("undo", cmd_undo, "undo the last delete"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)

    p = sub.add_parser("status", help="show daemon status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list", help="list history")
    p.add_argument("--json", action="store_true")
    p.add_argument("--kind", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("clear", help="clear history")
    p.add_argument("--all", action="store_true", help="also remove pinned entries")
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("restore", help="copy an entry back to the clipboard")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("action", help="run an action on an entry")
    p.add_argument("id", type=int)
    p.add_argument("action")
    p.set_defaults(func=cmd_action)

    p = sub.add_parser("pin", help="pin an entry")
    p.add_argument("id", type=int)
    p.add_argument("--unpin", action="store_true")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("delete", help="delete an entry")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("doctor", help="check the installation")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("setup", help="install the Hyprland keybind and replace cliphist")
    p.add_argument("--apply", action="store_true", help="write changes (dry run by default)")
    p.add_argument("--dry-run", action="store_true", help="print changes only (default)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("import", help="import history from another manager")
    p.add_argument("source", choices=["cliphist"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("_ingest", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    util.set_private_umask()
    logging.basicConfig(
        level=os.environ.get("CLIPD_LOG", "WARNING").upper(),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
