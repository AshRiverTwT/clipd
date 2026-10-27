from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import KIND_LABELS, Entry


@dataclass(slots=True)
class Query:
    text: str = ""
    kind: str | None = None
    pinned: bool | None = None
    raw: str = ""


def parse_query(raw: str) -> Query:
    parts: list[str] = []
    kind: str | None = None
    pinned: bool | None = None
    for token in raw.split():
        low = token.lower()
        if low in {"is:pinned", "pin:pinned"}:
            pinned = True
        elif low in {"is:unpinned", "pin:unpinned"}:
            pinned = False
        elif ":" in low:
            prefix, _, rest = low.partition(":")
            if prefix in {"kind", "type"} and rest:
                kind = rest
            elif prefix in KIND_LABELS and not rest:
                kind = prefix
            elif prefix in {"url", "code", "file", "git", "cmd", "text", "ip", "image"}:
                mapping = {
                    "url": "url",
                    "code": "code",
                    "file": "file",
                    "git": "git-repo",
                    "cmd": "command",
                    "text": "plain-text",
                    "ip": "ip",
                    "image": "image",
                }
                kind = mapping[prefix]
            else:
                parts.append(token)
        else:
            parts.append(token)
    return Query(text=" ".join(parts).strip(), kind=kind, pinned=pinned, raw=raw)


def _subsequence(query: str, haystack: str) -> tuple[bool, float]:
    if not query:
        return True, 0.0
    pos = 0
    gaps = 0
    last = -1
    for ch in query:
        found = haystack.find(ch, pos)
        if found < 0:
            return False, 0.0
        if last >= 0:
            gaps += max(0, found - last - 1)
        last = found
        pos = found + 1
    return True, max(0.0, 45.0 - gaps * 1.5)


def score(query: str, entry: Entry) -> float:
    if not query:
        return 0.0
    q = query.lower()
    hay = " ".join(
        [
            entry.preview,
            KIND_LABELS.get(entry.kind, entry.kind),
            entry.language or "",
            str(entry.meta.get("domain", "")),
            str(entry.meta.get("path", "")),
            str(entry.meta.get("repo", "")),
            str(entry.meta.get("address", "")),
        ]
    ).lower()
    idx = hay.find(q)
    value = 0.0
    if idx >= 0:
        value = 100.0 - min(idx, 80) * 0.25
        if idx == 0 or hay[idx - 1] in " \t\n/.:-_":
            value += 20.0
        if len(q) == len(entry.preview):
            value += 10.0
    else:
        ok, sub = _subsequence(q, hay)
        if not ok:
            return 0.0
        value = sub
    if entry.pinned:
        value += 8.0
    value += min(entry.use_count, 20) * 0.2
    return value


def _is_git_command(entry: Entry) -> bool:
    command = str(entry.meta.get("command") or entry.preview or entry.text).strip()
    command = command.removeprefix("$ ").strip()
    for prefix in ("sudo ", "doas ", "command ", "env "):
        while command.startswith(prefix):
            command = command[len(prefix):].strip()
    return command.startswith("git ") or command == "git"


def _matches_kind(entry: Entry, kind: str | None) -> bool:
    if kind is None or kind == "all":
        return True
    if kind == "git-repo":
        return entry.kind == "git-repo" or (
            entry.kind == "command" and _is_git_command(entry)
        )
    return entry.kind == kind


def search(entries: list[Entry], raw: str, kind: str | None = None) -> list[Entry]:
    query = parse_query(raw)
    effective_kind = query.kind or (None if kind in {None, "all"} else kind)
    pinned = query.pinned
    filtered = [
        entry
        for entry in entries
        if _matches_kind(entry, effective_kind)
        and (pinned is None or entry.pinned == pinned)
    ]
    if not query.text:
        return filtered
    scored = [(score(query.text, entry), index, entry) for index, entry in enumerate(filtered)]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored]


def highlight_spans(query: str, text: str) -> list[tuple[int, int]]:
    """Simple contiguous match spans for callers that want to highlight text."""
    if not query:
        return []
    q = re.escape(query)
    return [(m.start(), m.end()) for m in re.finditer(q, text, re.IGNORECASE)]
