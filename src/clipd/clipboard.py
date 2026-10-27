from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import spawn, util


class ClipboardError(RuntimeError):
    pass


class ClipboardWriter:
    """Writes text to the Wayland clipboard and suppresses self-ingest."""

    def __init__(self) -> None:
        self.last_hash: str | None = None
        self.last_time: float = 0.0
        self.last_text: str = ""

    def _run_wl_copy(self, argv: list[str], input_text: str | bytes | None = None) -> bool:
        # wl-copy forks a serving child. Redirect the child's descriptors to
        # /dev/null, otherwise communicate() waits for inherited pipes forever.
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            return False
        try:
            proc.communicate(
                input=(
                    input_text.encode("utf-8")
                    if isinstance(input_text, str)
                    else input_text
                ),
                timeout=3,
            )
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            return False
        return proc.returncode == 0

    def copy(self, text: str) -> bool:
        if not text:
            return False
        digest = util.sha256_text(text)
        previous = (self.last_hash, self.last_time, self.last_text)
        self.last_hash = digest
        self.last_time = time.monotonic()
        self.last_text = text
        if not self._run_wl_copy(
            ["wl-copy", "--type", "text/plain;charset=utf-8"], input_text=text
        ):
            self.last_hash, self.last_time, self.last_text = previous
            return False
        return True

    def copy_image(self, data: bytes, mime: str = "image/png") -> bool:
        if not data:
            return False
        mime = mime if mime.startswith("image/") else "image/png"
        digest = util.sha256_bytes(data)
        previous = (self.last_hash, self.last_time, self.last_text)
        self.last_hash = digest
        self.last_time = time.monotonic()
        self.last_text = ""
        if not self._run_wl_copy(["wl-copy", "--type", mime], input_text=data):
            self.last_hash, self.last_time, self.last_text = previous
            return False
        return True

    def clear(self) -> bool:
        self.last_hash = None
        self.last_time = 0.0
        self.last_text = ""
        return self._run_wl_copy(["wl-copy", "--clear"])

    def was_recent(self, digest: str, window: float = 2.0) -> bool:
        return bool(
            digest
            and digest == self.last_hash
            and (time.monotonic() - self.last_time) <= window
        )


class ClipboardWatcher:
    """Supervises ``wl-paste --watch clipd _ingest`` as a child process."""

    def __init__(self, command: list[str] | None = None) -> None:
        self.command = command or ["wl-paste", "--watch", *util.ingest_argv()]
        self.process: subprocess.Popen | None = None

    def start(self) -> bool:
        if self.is_alive():
            return True
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=util.module_env(),
                start_new_session=True,
                close_fds=True,
            )
            return True
        except OSError:
            self.process = None
            return False

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        proc = self.process
        self.process = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass

    def restart(self) -> bool:
        self.stop()
        return self.start()


IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def preferred_image_type(types: list[str]) -> str | None:
    normalized = {item.strip().lower() for item in types}
    for mime in IMAGE_MIMES:
        if mime in normalized:
            return mime
    return None


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_image(
    mime: str,
    timeout: float = 4.0,
    max_bytes: int = 8 * 1024 * 1024,
) -> bytes | None:
    if mime not in IMAGE_MIMES:
        return None
    ok, data, _err = spawn.run_bytes(
        ["wl-paste", "--no-newline", "--type", mime],
        timeout=timeout,
        max_output=max_bytes + 1,
    )
    if not ok or not data or len(data) > max_bytes:
        return None
    if image_mime(data) is None:
        return None
    return data


def list_types(timeout: float = 1.5) -> list[str]:
    result = spawn.run(["wl-paste", "--list-types"], timeout=timeout)
    if not result.ok:
        return []
    types: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("nothing is copied"):
            continue
        types.append(line)
    return types


def clipboard_available() -> bool:
    return util.which("wl-paste") is not None and util.which("wl-copy") is not None


def clear_clipboard() -> bool:
    return ClipboardWriter().clear()


def read_current(timeout: float = 2.0, max_bytes: int = 1024 * 1024) -> str | None:
    result = spawn.run(
        ["wl-paste", "--no-newline", "--type", "text/plain;charset=utf-8"],
        timeout=timeout,
    )
    if not result.ok:
        return None
    return result.stdout[:max_bytes]


def socket_owner(path: Path) -> int | None:
    try:
        return path.stat().st_uid
    except OSError:
        return None
