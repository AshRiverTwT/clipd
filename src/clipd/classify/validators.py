from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from ..util import which

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_COLOR = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+\s*)?\)$",
    re.IGNORECASE,
)
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_SCP_GIT = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>.+)$")
_REPO_PART = re.compile(r"^[A-Za-z0-9._~-]+$")

KNOWN_GIT_HOSTS = {
    "github.com",
    "gitlab.com",
    "codeberg.org",
    "bitbucket.org",
    "gitea.com",
    "git.sr.ht",
}
_BAD_URL_SCHEMES = {"javascript", "data", "vbscript", "file", "chrome", "about"}

# Bare links are common in clipboards. Keep this allow-list conservative:
# README.md, script.py and config.toml must remain files, not URLs.
_AUTOLINK_TLDS = {
    "com", "org", "net", "edu", "gov", "mil", "int", "io", "dev", "app",
    "ai", "co", "me", "gg", "tv", "xyz", "info", "biz", "cloud", "tech",
    "site", "online", "store", "blog", "page", "link", "live", "pro",
    "name", "mobi", "travel", "museum", "aero", "jobs", "cat", "tel",
    "asia", "news", "shop", "wiki", "zone", "club", "space", "website",
    "digital", "agency", "studio", "design", "software", "tools", "systems",
    "solutions", "network", "email", "social", "media", "video", "photo",
    "pics", "art", "music", "film", "games", "finance", "bank", "insurance",
    "law", "legal", "health", "care", "clinic", "doctor", "school",
    "academy", "university", "science", "engineering", "codes", "run",
    "build", "host", "domains", "global", "world", "today", "life", "one",
    "now", "new", "fan", "fans", "group", "company", "center", "city",
    "country", "earth", "eco", "energy", "green", "land", "farm", "food",
    "kitchen", "house", "home", "garden", "fashion", "style", "beauty",
    "fitness", "yoga", "coffee", "bar", "pub", "restaurant", "cafe",
    "recipes", "wine", "beer", "holiday", "hotel", "flight", "taxi",
    "car", "auto", "bike", "motor", "repair", "cleaning", "security",
    "support", "help", "guide", "review", "reviews", "deals", "sale",
    "shopping", "market", "auction", "bid", "trade", "exchange", "money",
    "capital", "fund", "invest", "credit", "loan", "mortgage", "tax",
    "accounting", "consulting", "management", "marketing", "advertising",
    "uk", "de", "fr", "jp", "cn", "au", "ca", "us", "eu", "in", "br",
    "nl", "se", "no", "fi", "dk", "pl", "ru", "ch", "at", "be", "cz",
    "es", "it", "mx", "nz", "sg", "hk", "kr", "tw", "za", "ie", "pt",
    "gr", "tr", "ar", "cl",
}
_FILE_EXTENSION_TLDS = {
    "py", "sh", "md", "js", "ts", "jsx", "tsx", "rs", "go", "c", "h",
    "cc", "cpp", "hpp", "rb", "php", "txt", "log", "png", "jpg", "jpeg",
    "gif", "svg", "webp", "ico", "pdf", "json", "toml", "yaml", "yml",
    "xml", "html", "css", "scss", "conf", "cfg", "ini", "db", "sqlite",
    "sql", "lock", "zip", "tar", "gz", "xz", "bz2", "7z", "rar", "mp3",
    "mp4", "mkv", "avi", "mov", "wav", "flac", "ogg", "webm", "doc",
    "docx", "xls", "xlsx", "ppt", "pptx", "pyc", "so", "dll", "exe",
    "bin", "iso", "img", "dmg", "deb", "rpm", "pkg", "apk", "local",
    "test", "invalid", "example",
}
_URL_TRAILING_PUNCT = ".,;:!?)]}'\""


@dataclass(slots=True)
class UrlInfo:
    url: str
    scheme: str
    host: str
    port: int | None
    path: str
    query: str

    @property
    def domain(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host


@dataclass(slots=True)
class GitRemote:
    host: str
    path: str
    repo: str
    page_url: str
    clone_url: str
    ssh_url: str


def _has_bad_chars(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def valid_host(host: str) -> bool:
    host = host.rstrip(".").lower()
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        host.encode("idna")
    except UnicodeError:
        return False
    labels = host.split(".")
    if len(labels) == 1 and host != "localhost":
        return False
    return all(_HOST_LABEL.match(label) for label in labels)


def _autolink(text: str) -> str | None:
    """Normalize a scheme-less host/path such as www.google.com to https."""
    candidate = text.rstrip(_URL_TRAILING_PUNCT)
    if not candidate or "://" in candidate or "@" in candidate:
        return None
    if candidate.startswith(("/", ".", "-")):
        return None
    if any(ch.isspace() for ch in candidate) or "\\" in candidate:
        return None
    try:
        split = urlsplit("https://" + candidate)
    except ValueError:
        return None
    host = (split.hostname or "").lower()
    if not valid_host(host):
        return None
    if not candidate.lower().startswith("www."):
        tld = host.rsplit(".", 1)[-1]
        if tld in _FILE_EXTENSION_TLDS:
            return None
        if tld not in _AUTOLINK_TLDS and not (len(tld) == 2 and tld.isalpha()):
            return None
    return "https://" + candidate


def parse_url(raw: str) -> UrlInfo | None:
    text = raw.strip()
    if not text or len(text) > 4096 or _has_bad_chars(text):
        return None
    if any(ch.isspace() for ch in text) or "\\" in text:
        return None
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
        text = _autolink(text) or ""
        if not text:
            return None
    try:
        split = urlsplit(text)
    except ValueError:
        return None
    scheme = split.scheme.lower()
    if scheme not in {"http", "https"} or scheme in _BAD_URL_SCHEMES:
        return None
    if not split.netloc or split.username is not None or split.password is not None:
        return None
    host = (split.hostname or "").lower()
    if not valid_host(host):
        return None
    try:
        port = split.port
    except ValueError:
        return None
    if port is not None and not (0 < port < 65536):
        return None
    if any(ch in split.path for ch in '<>"{}|^`'):
        return None
    netloc = split.netloc
    normalized = urlunsplit((scheme, netloc, split.path, split.query, split.fragment))
    return UrlInfo(
        url=normalized,
        scheme=scheme,
        host=host,
        port=port,
        path=split.path,
        query=split.query,
    )


def _repo_parts(path: str) -> list[str] | None:
    path = path.strip("/")
    if not path or any(ch.isspace() for ch in path) or _has_bad_chars(path):
        return None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) < 2 or len(parts) > 8:
        return None
    for part in parts:
        if part in {"", ".", ".."} or part.startswith("-"):
            return None
        if not _REPO_PART.fullmatch(part):
            return None
    return parts


def parse_git(raw: str) -> GitRemote | None:
    text = raw.strip()
    if not text or len(text) > 2048 or _has_bad_chars(text) or any(ch.isspace() for ch in text):
        return None

    scp = _SCP_GIT.match(text)
    if scp:
        user = scp.group("user")
        host = scp.group("host").lower()
        if not valid_host(host):
            return None
        parts = _repo_parts(scp.group("path"))
        if not parts:
            return None
        repo = parts[-1]
        path = "/".join(parts)
        return GitRemote(
            host=host,
            path=path,
            repo=repo,
            page_url=f"https://{host}/{path}",
            clone_url=f"https://{host}/{path}.git",
            ssh_url=f"{user}@{host}:{path}.git",
        )

    if "://" not in text:
        return None
    try:
        split = urlsplit(text)
    except ValueError:
        return None
    scheme = split.scheme.lower()
    if scheme not in {"http", "https", "ssh"}:
        return None
    if split.password is not None or not split.hostname:
        return None
    if split.query or split.fragment:
        return None
    host = split.hostname.lower()
    if not valid_host(host):
        return None
    if scheme in {"http", "https"}:
        if split.username is not None:
            return None
        raw_path = split.path.rstrip("/")
        if (
            host not in KNOWN_GIT_HOSTS
            and not raw_path.endswith(".git")
            and not host.startswith(("git.", "gitlab.", "gitea.", "code."))
        ):
            return None
    if scheme == "ssh" and split.username not in {None, "git"}:
        return None
    parts = _repo_parts(split.path)
    if not parts:
        return None
    if host == "github.com" and len(parts) != 2:
        return None
    repo = parts[-1]
    path = "/".join(parts)
    port = split.port
    if port:
        ssh_prefix = f"ssh://git@{host}:{port}"
    else:
        ssh_prefix = f"git@{host}"
    return GitRemote(
        host=host,
        path=path,
        repo=repo,
        page_url=f"https://{host}/{path}",
        clone_url=f"https://{host}/{path}.git",
        ssh_url=f"{ssh_prefix}:{path}.git" if not port else f"{ssh_prefix}/{path}.git",
    )


def _parse_file_uri(text: str) -> Path | None:
    try:
        split = urlsplit(text)
    except ValueError:
        return None
    if split.scheme.lower() != "file":
        return None
    if split.netloc not in {"", "localhost"}:
        return None
    return Path(unquote(split.path))


def path_from_line(line: str) -> Path | None:
    text = line.strip()
    if not text or len(text) > 4096 or _has_bad_chars(text):
        return None
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1]
    if text.startswith("file://"):
        candidate = _parse_file_uri(text)
        if candidate is None:
            return None
    elif text.startswith(("/", "~/")):
        candidate = Path(text).expanduser()
    elif text.startswith("./") or text.startswith("../"):
        candidate = Path(text)
    else:
        return None
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    return resolved


def parse_path(raw: str, must_exist: bool = True) -> Path | None:
    if "\n" in raw or "\r" in raw:
        return None
    path = path_from_line(raw)
    if path is None:
        return None
    if must_exist and not path.exists():
        return None
    return path


def parse_paths(raw: str, must_exist: bool = True, limit: int = 20) -> list[Path] | None:
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines or len(lines) > limit:
        return None
    paths: list[Path] = []
    for line in lines:
        path = parse_path(line, must_exist=must_exist)
        if path is None:
            return None
        paths.append(path)
    return paths


def looks_pathish(raw: str) -> bool:
    text = raw.strip()
    if "\n" in text:
        return all(
            looks_pathish(line) for line in text.splitlines() if line.strip()
        )
    return text.startswith(("/", "~/", "./", "../", "file://"))


def parse_ip(raw: str) -> dict | None:
    text = raw.strip()
    if not text or len(text) > 100 or any(ch.isspace() for ch in text):
        return None
    try:
        addr = ipaddress.ip_address(text)
        return {"ip": str(addr), "version": addr.version, "network": False}
    except ValueError:
        pass
    try:
        net = ipaddress.ip_network(text, strict=False)
        if net.prefixlen == net.max_prefixlen and "/" not in text:
            return None
        return {
            "ip": str(net.network_address),
            "version": net.version,
            "network": True,
            "cidr": str(net),
        }
    except ValueError:
        return None


def parse_email(raw: str) -> str | None:
    text = raw.strip()
    if len(text) > 254 or text.count("@") != 1 or ".." in text:
        return None
    if _EMAIL.fullmatch(text):
        return text
    return None


def hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    text = hex_color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def parse_color(raw: str) -> dict | None:
    text = raw.strip()
    if _HEX_COLOR.fullmatch(text):
        rgb = hex_to_rgb(text)
        if rgb is None:
            return None
        raw = text.lstrip("#").lower()
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        return {
            "hex": "#" + raw,
            "rgb": f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})",
            "r": rgb[0],
            "g": rgb[1],
            "b": rgb[2],
        }
    match = _RGB_COLOR.fullmatch(text)
    if match:
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if max(r, g, b) > 255:
            return None
        return {
            "hex": f"#{r:02x}{g:02x}{b:02x}",
            "rgb": f"rgb({r}, {g}, {b})",
            "r": r,
            "g": g,
            "b": b,
        }
    return None


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def parse_jwt(raw: str) -> dict | None:
    text = raw.strip()
    if len(text) > 8192 or not _JWT.fullmatch(text):
        return None
    parts = text.split(".")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None
    if "alg" not in header and "typ" not in header:
        return None
    return {"header": header, "payload": payload, "signature": parts[2]}


def looks_like_color(raw: str) -> bool:
    return parse_color(raw) is not None


COMMAND_HEADS = {
    "alias", "apt", "awk", "bash", "bat", "bundle", "cargo", "cat", "cd", "chmod",
    "chown", "chroot", "clear", "cliphist", "cmake", "code", "cp", "curl", "cut",
    "date", "dd", "deno", "df", "diff", "dig", "docker", "du", "echo", "env", "exec",
    "export", "fd", "ffmpeg", "find", "flatpak", "foot", "fzf", "gcc", "git", "go",
    "grep", "gzip", "head", "helix", "history", "htop", "hyprctl", "ip", "java",
    "journalctl", "jq", "kill", "kubectl", "less", "ln", "ls", "make", "man", "mkdir",
    "mount", "mv", "nc", "ncat", "nmap", "node", "npm", "nvim", "pacman", "paru",
    "pgrep", "ping", "pip", "pipx", "pkill", "pnpm", "podman", "ps", "python",
    "python3", "rg", "rm", "rsync", "rustc", "scp", "sed", "sh", "shellcheck", "shuf",
    "sleep", "sort", "ssh", "stat", "sudo", "systemctl", "tail", "tar", "tee", "time",
    "tmux", "touch", "tr", "tree", "uname", "unzip", "uv", "vim", "watch", "wc",
    "wget", "which", "wl-copy", "wl-paste", "xargs", "yay", "yq", "zsh", "zip",
}
_WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "command", "exec", "xargs", "watch"}


def parse_command(raw: str) -> dict | None:
    text = raw.strip()
    if not text or "\n" in text or len(text) > 300 or _has_bad_chars(text):
        return None
    prompt = False
    if text.startswith("$ "):
        text = text[2:].strip()
        prompt = True
    if not text:
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if not tokens:
        return None
    first = tokens[0]
    head = first.rsplit("/", 1)[-1]
    pos = 0
    while head in _WRAPPERS and pos + 1 < len(tokens):
        pos += 1
        while pos < len(tokens) and "=" in tokens[pos] and not tokens[pos].startswith("-"):
            pos += 1
        if pos >= len(tokens):
            return None
        head = tokens[pos].rsplit("/", 1)[-1]
    command_like = head in COMMAND_HEADS
    if not command_like and ("/" in first):
        command_like = Path(first).is_absolute() and Path(first).exists()
    if not command_like and which(head):
        command_like = True
    if not command_like:
        return None
    # Reject prose that merely happens to start with a common word.
    if text.endswith((".", "?", "!")) and " " in text and not any(
        ch in text for ch in "|&;<>$()={`"
    ):
        return None
    return {"command": text, "head": head, "prompt": prompt}


def validate_clone_dest(raw: str) -> Path | None:
    text = raw.strip()
    if not text or len(text) > 4096 or "\n" in text or "\x00" in text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if resolved == Path("/") or resolved == Path.home():
        return None
    return resolved


def shell_quote(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+,-]+", text):
        return text
    return shlex.quote(text)
