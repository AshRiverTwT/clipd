from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

_BIDI = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS = re.compile(r"\s+")


def config_dir() -> Path:
    override = os.environ.get("CLIPD_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base).expanduser() / "clipd"


def data_dir() -> Path:
    override = os.environ.get("CLIPD_DATA_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base).expanduser() / "clipd"


def runtime_dir() -> Path:
    override = os.environ.get("CLIPD_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "clipd"
    return Path("/tmp") / f"clipd-{os.getuid()}"


def config_path() -> Path:
    return config_dir() / "config.toml"


def db_path() -> Path:
    return data_dir() / "history.db"


def socket_path() -> Path:
    return runtime_dir() / "sock"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def set_private_umask() -> None:
    os.umask(0o077)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short_hash(text: str) -> str:
    return sha256_text(text)[:8]


def image_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Read dimensions from common clipboard image formats without Pillow."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return width, height
        if data.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                index += 2
                if marker in (0xD8, 0xD9):
                    continue
                if index + 2 > len(data):
                    break
                length = int.from_bytes(data[index:index + 2], "big")
                if length < 2:
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if index + 7 <= len(data):
                        height = int.from_bytes(data[index + 3:index + 5], "big")
                        width = int.from_bytes(data[index + 5:index + 7], "big")
                        return width, height
                    break
                index += length
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return (
                int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"),
            )
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if len(data) >= 30:
                chunk = data[12:16]
                if chunk == b"VP8X":
                    width = 1 + int.from_bytes(data[24:27], "little")
                    height = 1 + int.from_bytes(data[27:30], "little")
                    return width, height
    except (IndexError, ValueError):
        pass
    return None, None


def image_extension(mime: str | None) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get((mime or "").lower(), ".img")


def sanitize_display(text: str) -> str:
    """Remove control characters that can corrupt or spoof a terminal/UI."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BIDI.sub("", text)
    text = _CONTROL.sub("", text)
    return text


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def preview_text(text: str, limit: int = 320) -> str:
    clean = collapse_ws(sanitize_display(text))
    if len(clean) > limit:
        return clean[: limit - 1].rstrip() + "…"
    return clean


def clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if n < 10 else f"{n:.0f} {unit}"
    return f"{n} B"


def relative_time(ts: int | float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    delta = max(0, int(now - ts))
    if delta < 10:
        return "now"
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    if delta < 86400 * 7:
        return f"{delta // 86400}d"
    return time.strftime("%d %b", time.localtime(ts))


def slugify(text: str, limit: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:limit].strip("-") or "snippet")


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def which(name: str) -> str | None:
    return shutil.which(name)


def ingest_argv() -> list[str]:
    return [sys.executable, "-m", "clipd", "_ingest"]


def module_env() -> dict[str, str]:
    """Environment for child clipd processes (watcher / ingest)."""
    env = os.environ.copy()
    parent = str(Path(__file__).resolve().parent.parent)
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = parent + (os.pathsep + old if old else "")
    return env


def parse_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def running_as_root() -> bool:
    return os.geteuid() == 0
