from __future__ import annotations

from dataclasses import dataclass, field

KIND_LABELS: dict[str, str] = {
    "url": "URL",
    "file": "File",
    "directory": "Folder",
    "file-list": "Files",
    "git-repo": "Git repo",
    "command": "Command",
    "code": "Code",
    "json": "JSON",
    "diff": "Diff",
    "ip": "IP address",
    "email": "Email",
    "color": "Colour",
    "jwt": "JWT",
    "plain-text": "Text",
    "image": "Image",
    "empty": "Empty",
}

# Order used by filter chips and keyboard cycling.
KINDS: tuple[str, ...] = (
    "plain-text",
    "code",
    "json",
    "diff",
    "url",
    "file",
    "directory",
    "file-list",
    "git-repo",
    "command",
    "ip",
    "email",
    "color",
    "jwt",
    "image",
)

FILTER_KINDS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("plain-text", "Text"),
    ("code", "Code"),
    ("url", "URL"),
    ("file", "File"),
    ("git-repo", "Git"),
    ("command", "Cmd"),
    ("ip", "IP"),
    ("image", "Images"),
)

KIND_ICONS: dict[str, str] = {
    "url": "web-browser-symbolic",
    "file": "text-x-generic-symbolic",
    "directory": "folder-symbolic",
    "file-list": "folder-symbolic",
    "git-repo": "network-server-symbolic",
    "command": "utilities-terminal-symbolic",
    "code": "text-x-generic-symbolic",
    "json": "text-x-generic-symbolic",
    "diff": "text-x-generic-symbolic",
    "ip": "network-wired-symbolic",
    "email": "mail-unread-symbolic",
    "color": "color-select-symbolic",
    "jwt": "dialog-password-symbolic",
    "plain-text": "text-x-generic-symbolic",
    "image": "image-x-generic-symbolic",
}


@dataclass(slots=True)
class Detection:
    kind: str
    confidence: float = 0.5
    meta: dict = field(default_factory=dict)
    language: str | None = None

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass(slots=True)
class Entry:
    id: int
    hash: str
    kind: str
    text: str
    preview: str
    size: int
    created_at: int
    last_used_at: int
    use_count: int = 0
    pinned: bool = False
    truncated: bool = False
    confidence: float = 0.5
    language: str | None = None
    meta: dict = field(default_factory=dict)
    data: bytes | None = None
    mime: str | None = None

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def is_code(self) -> bool:
        return self.kind in {"code", "json", "diff"} or bool(self.language)

    def with_text(self, text: str) -> "Entry":
        clone = Entry(**{f: getattr(self, f) for f in self.__dataclass_fields__})
        clone.text = text
        return clone


@dataclass(slots=True)
class AddResult:
    entry: Entry
    created: bool
