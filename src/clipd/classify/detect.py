from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..models import Detection
from . import validators

_LANG_EXT = {
    "python": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "rust": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "go": ".go",
    "sql": ".sql",
    "html": ".html",
    "css": ".css",
    "yaml": ".yaml",
    "toml": ".toml",
    "bash": ".sh",
    "dockerfile": ".Dockerfile",
    "markdown": ".md",
    "diff": ".patch",
    "json": ".json",
}


@dataclass(slots=True)
class Language:
    id: str
    label: str
    extension: str


def language_info(lang_id: str | None) -> Language | None:
    if not lang_id:
        return None
    labels = {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "rust": "Rust",
        "c": "C",
        "cpp": "C++",
        "go": "Go",
        "sql": "SQL",
        "html": "HTML",
        "css": "CSS",
        "yaml": "YAML",
        "toml": "TOML",
        "bash": "Shell",
        "dockerfile": "Dockerfile",
        "markdown": "Markdown",
        "diff": "Diff",
        "json": "JSON",
    }
    return Language(lang_id, labels.get(lang_id, lang_id.title()), _LANG_EXT.get(lang_id, ".txt"))


def _first_lines(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[:n])


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{" or len(stripped) > 2_000_000:
        return False
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def detect_language(text: str) -> str | None:
    sample = _first_lines(text, 60)
    stripped = sample.strip()
    low = stripped.lower()
    if not stripped:
        return None
    if stripped.startswith("#!"):
        first = stripped.splitlines()[0].lower()
        if "python" in first:
            return "python"
        if any(sh in first for sh in ("bash", "/sh", "zsh", "dash")):
            return "bash"
        if "node" in first:
            return "javascript"
        if "deno" in first:
            return "typescript"
    if re.search(r"^\s*diff --git ", sample, re.M) or re.search(r"^@@ -\d+", sample, re.M):
        return "diff"
    if re.search(r"^\s*FROM\s+\S+", sample, re.M) and re.search(
        r"^\s*(RUN|COPY|ADD|CMD|ENTRYPOINT)\s", sample, re.M
    ):
        return "dockerfile"
    if re.search(r"<!doctype html|<html[\s>]|<div[\s>]|<body[\s>]", low):
        return "html"
    if re.search(r"^\s*#include\s*[<\"]", sample, re.M):
        return "cpp" if re.search(r"std::|iostream|using namespace", sample) else "c"
    if re.search(r"^\s*package\s+main\b", sample, re.M) or re.search(
        r"^\s*func\s+\w+\s*\(", sample, re.M
    ):
        return "go"
    if re.search(r"^\s*(use\s+std|fn\s+main|impl\s+\w+|let\s+mut)\b", sample, re.M):
        return "rust"
    if re.search(r"^\s*(def|class)\s+\w+.*:|^\s*(import|from)\s+\w+", sample, re.M):
        return "python"
    if re.search(r"\b(interface|type)\s+\w+\s*[={]|:\s*(string|number|boolean)\b", sample):
        return "typescript"
    if re.search(r"\b(const|let|var)\s+\w+\s*=|=>|console\.log|function\s+\w+\s*\(", sample):
        return "javascript"
    if re.search(
        r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE|ALTER\s+TABLE)\b",
        sample,
        re.M | re.I,
    ) and re.search(r"\b(FROM|INTO|SET|WHERE|VALUES)\b", sample, re.I):
        return "sql"
    if re.search(r"^\s*[A-Za-z_][\w.-]*:\s+\S", sample, re.M) and not re.search(
        r"[{};]", sample
    ):
        if re.search(r"^\s*---\s*$", sample, re.M) or re.search(
            r"^\s*[\w.-]+:\s*$", sample, re.M
        ):
            return "yaml"
    if re.search(r"^\s*\[[A-Za-z0-9_.-]+\]\s*$", sample, re.M) and re.search(
        r"^\s*[A-Za-z0-9_.-]+\s*=", sample, re.M
    ):
        return "toml"
    if re.search(r"^```|^#{1,6}\s+\S", sample, re.M):
        return "markdown"
    if re.search(r"[.#]?[A-Za-z][\w-]*\s*\{[^}]*:[^}]*;", sample):
        return "css"
    if re.search(r"^\s*(sudo\s+)?[\w./-]+\s+(-\S+|--\S+)", sample) and "\n" not in stripped:
        return "bash"
    return None


def _diff_stats(text: str) -> dict:
    added = sum(1 for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
    files = len(re.findall(r"^diff --git ", text, re.M))
    return {"files": files, "added": added, "removed": removed}


def _classify_single(line: str) -> Detection | None:
    # Git remotes are URLs too; prefer the more specific interpretation.
    git = validators.parse_git(line)
    if git:
        return Detection(
            "git-repo",
            0.95,
            {
                "host": git.host,
                "path": git.path,
                "repo": git.repo,
                "page_url": git.page_url,
                "clone_url": git.clone_url,
                "ssh_url": git.ssh_url,
            },
        )
    url = validators.parse_url(line)
    if url:
        return Detection(
            "url",
            0.98,
            {"url": url.url, "domain": url.domain, "scheme": url.scheme},
        )
    email = validators.parse_email(line)
    if email:
        return Detection("email", 0.95, {"address": email})
    ip = validators.parse_ip(line)
    if ip:
        return Detection("ip", 0.9, ip)
    color = validators.parse_color(line)
    if color:
        return Detection("color", 0.92, color)
    jwt = validators.parse_jwt(line)
    if jwt:
        return Detection("jwt", 0.9, {"header": jwt["header"], "payload_keys": list(jwt["payload"].keys())})
    path = validators.parse_path(line)
    if path:
        kind = "directory" if path.is_dir() else "file"
        return Detection(kind, 0.96, {"path": str(path), "name": path.name})
    command = validators.parse_command(line)
    if command:
        return Detection("command", 0.78, command)
    lang = detect_language(line)
    if lang and (len(line) > 12):
        return Detection("code", 0.7, {"language": lang}, language=lang)
    return None


def classify(text: str) -> Detection:
    """Classify clipboard text without executing or resolving it."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = text.strip()
    if not stripped:
        return Detection("empty", 1.0, {})

    if re.search(r"^\s*diff --git ", text, re.M) or re.search(r"^@@ -\d+", text, re.M):
        return Detection("diff", 0.95, _diff_stats(text), language="diff")

    if _looks_like_json(stripped):
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                detail = f"{len(value)} keys"
            elif isinstance(value, list):
                detail = f"{len(value)} items"
            else:
                detail = "scalar"
        except (json.JSONDecodeError, ValueError):
            detail = "JSON"
        return Detection("json", 0.97, {"detail": detail}, language="json")

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        single = _classify_single(lines[0].strip())
        if single:
            return single
    else:
        paths = validators.parse_paths(text)
        if paths and len(paths) >= 2:
            return Detection(
                "file-list",
                0.9,
                {
                    "paths": [str(p) for p in paths],
                    "count": len(paths),
                    "dirs": sum(1 for p in paths if p.is_dir()),
                },
            )
        lang = detect_language(text)
        if lang:
            return Detection("code", 0.82, {"language": lang}, language=lang)
        if validators.looks_pathish(text):
            return Detection("plain-text", 0.4, {"pathish": True})

    lang = detect_language(text)
    if lang and len(stripped) > 12:
        return Detection("code", 0.65, {"language": lang}, language=lang)

    return Detection(
        "plain-text",
        0.5,
        {
            "chars": len(text),
            "lines": len(text.splitlines()),
            "words": len(text.split()),
        },
    )
