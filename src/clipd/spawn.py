from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


_MAX_OUTPUT = 64 * 1024


@dataclass(slots=True)
class CommandResult:
    ok: bool
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _validate_argv(argv: list[str]) -> None:
    if not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("argv must be a non-empty list of strings")
    for arg in argv:
        if "\x00" in arg:
            raise ValueError("argv contains NUL")


def run(
    argv: list[str],
    timeout: float = 10.0,
    input_text: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    max_output: int = _MAX_OUTPUT,
) -> CommandResult:
    _validate_argv(argv)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            start_new_session=True,
            text=True,
        )
    except (OSError, ValueError) as exc:
        return CommandResult(False, 127, "", str(exc))
    timed_out = False
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            out, err = "", ""
    out = (out or "")[:max_output]
    err = (err or "")[:max_output]
    return CommandResult(proc.returncode == 0, proc.returncode, out, err, timed_out)


def run_bytes(
    argv: list[str],
    timeout: float = 5.0,
    max_output: int = 32 * 1024 * 1024,
) -> tuple[bool, bytes, str]:
    """Run a binary-safe command (used for clipboard images)."""
    _validate_argv(argv)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return False, b"", str(exc)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    return (
        proc.returncode == 0 and not timed_out,
        (out or b"")[:max_output],
        (err or b"").decode("utf-8", "replace")[:4096],
    )


def launch(argv: list[str], env: dict[str, str] | None = None) -> bool:
    _validate_argv(argv)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=merged_env,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except (OSError, ValueError):
        return False


def desktop_notify(message: str, timeout_ms: int = 3000) -> bool:
    """Best-effort desktop notification without adding a dependency."""
    if shutil.which("notify-send"):
        return run(
            ["notify-send", "--app-name=clipd", "--icon=edit-copy", "clipd", message],
            timeout=2,
        ).ok
    if shutil.which("hyprctl"):
        return run(
            ["hyprctl", "notify", "1", str(timeout_ms), "0", message],
            timeout=2,
        ).ok
    return False


def open_uri(uri: str) -> bool:
    # Gio is preferred because it follows the desktop's default handler, but
    # some Hyprland/portal setups return False even when a handler exists.
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        if bool(Gio.AppInfo.launch_default_for_uri(uri, None)):
            return True
    except Exception:
        pass
    for name, argv in (
        ("xdg-open", ["xdg-open", uri]),
        ("gio", ["gio", "open", uri]),
    ):
        if shutil.which(name) and run(argv, timeout=8).ok:
            return True
    return False


def open_path(path: Path) -> bool:
    try:
        return open_uri(path.expanduser().resolve().as_uri())
    except (OSError, ValueError):
        return False


_BROWSER_HINTS = (
    "zen",
    "firefox",
    "librewolf",
    "chromium",
    "chrome",
    "brave",
    "vivaldi",
    "opera",
    "qutebrowser",
    "epiphany",
    "falkon",
    "floorp",
    "waterfox",
)


def _focus_browser() -> bool:
    if not shutil.which("hyprctl"):
        return False
    result = run(["hyprctl", "clients", "-j"], timeout=2, max_output=256 * 1024)
    if not result.ok:
        return False
    try:
        clients = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(clients, list):
        return False
    candidates = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        ident = f"{client.get('class', '')} {client.get('title', '')}".lower()
        if any(hint in ident for hint in _BROWSER_HINTS):
            address = client.get("address")
            if isinstance(address, str) and address.startswith("0x"):
                candidates.append((int(client.get("focusHistoryID", 999)), address))
    for _priority, address in sorted(candidates):
        if _focus(address):
            return True
    return False


def open_url(url: str) -> bool:
    ok = open_uri(url)
    if ok:
        # xdg-open/Gio may open a tab without raising the browser window.
        # Give the compositor a moment, then focus the existing browser.
        time.sleep(0.16)
        _focus_browser()
    return ok


def write_file(path: Path, text: str, mode: int = 0o600) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except OSError:
        return False


def write_bytes(path: Path, data: bytes, mode: int = 0o600) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return True
    except OSError:
        return False


def capture_active_window() -> dict | None:
    result = run(["hyprctl", "activewindow", "-j"], timeout=1.5)
    if not result.ok:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    address = data.get("address")
    if not isinstance(address, str) or not address.startswith("0x"):
        return None
    return {
        "address": address,
        "class": data.get("class", ""),
        "title": data.get("title", ""),
    }


def _focus(address: str) -> bool:
    candidates = [
        ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
        [
            "hyprctl",
            "eval",
            f'hl.dispatch(hl.dsp.focus({{ window = "address:{address}" }}))',
        ],
    ]
    return any(run(argv, timeout=2).ok for argv in candidates)


def _send_shortcut(address: str) -> bool:
    candidates = [
        [
            "hyprctl",
            "eval",
            'hl.dispatch(hl.dsp.send_shortcut({ mods = "CTRL", key = "V", '
            f'window = "address:{address}" }}))',
        ],
        [
            "hyprctl",
            "dispatch",
            "sendshortcut",
            "CTRL",
            "V",
            f"address:{address}",
        ],
    ]
    return any(run(argv, timeout=2).ok for argv in candidates)


def send_paste(address: str, method: str = "hyprland") -> bool:
    """Send Ctrl+V to a window. Never sends clipboard content as keystrokes."""
    if not address.startswith("0x"):
        return False
    _focus(address)
    if method == "hyprland":
        if _send_shortcut(address):
            return True
    if method in {"hyprland", "wtype"} and shutil.which("wtype"):
        return run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], timeout=3).ok
    return False


def terminal_argv(command: str) -> list[str] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or "\x00" in command:
        return None
    return argv


def git_clone(url: str, dest: Path, timeout: float = 180.0) -> tuple[bool, str]:
    if dest.exists():
        return False, f"{dest} already exists"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(tempfile.mkdtemp(prefix=".clipd-clone-", dir=dest.parent))
    except OSError as exc:
        return False, f"cannot create destination: {exc}"
    target = tmp_root / "repo"
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
    }
    result = run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--",
            url,
            str(target),
        ],
        timeout=timeout,
        env=env,
        max_output=16 * 1024,
    )
    if not result.ok:
        shutil.rmtree(tmp_root, ignore_errors=True)
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1] if detail else "git clone failed"
    try:
        if dest.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
            return False, f"{dest} already exists"
        os.replace(target, dest)
    except OSError as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return False, str(exc)
    shutil.rmtree(tmp_root, ignore_errors=True)
    return True, str(dest)


def shellcheck(path: Path, timeout: float = 8.0) -> CommandResult:
    return run(["shellcheck", "--format=plain", str(path)], timeout=timeout, max_output=16 * 1024)
