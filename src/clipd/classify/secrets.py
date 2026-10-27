from __future__ import annotations

import re
from dataclasses import dataclass

# Deliberately high-confidence patterns. Generic "password = ..." matches are
# left to user-configured exclude_patterns to avoid eating documentation/code.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    ),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "slack-token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "openai-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    ),
    (
        "stripe-key",
        re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    ),
    (
        "google-api-key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "anthropic-key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "npm-token",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    ),
)


@dataclass(frozen=True, slots=True)
class SecretMatch:
    name: str
    start: int
    end: int

    @property
    def preview(self) -> str:
        return f"{self.name} at {self.start}"


def find_secret(text: str) -> SecretMatch | None:
    for name, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return SecretMatch(name=name, start=match.start(), end=match.end())
    return None


def secret_names() -> list[str]:
    return [name for name, _ in _PATTERNS]
