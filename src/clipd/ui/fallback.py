from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import util
from ..models import KIND_ICONS, Entry


@dataclass(slots=True)
class MenuResult:
    entry: Entry | None = None
    action: str | None = None
    cancelled: bool = False


def rofi_available() -> bool:
    return shutil.which("rofi") is not None


def fuzzel_available() -> bool:
    return shutil.which("fuzzel") is not None


def theme_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[3] / "data" / "clipd.rasi",
        Path(__file__).resolve().parents[1] / "data" / "clipd.rasi",
        Path("/usr/share/clipd/clipd.rasi"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _row(entry: Entry, with_icon: bool) -> str:
    label = entry.label
    preview = entry.preview or "(empty)"
    if len(preview) > 120:
        preview = preview[:119].rstrip() + "…"
    meta = f"{util.human_size(entry.size)} · {util.relative_time(entry.last_used_at)}"
    icon = KIND_ICONS.get(entry.kind, "text-x-generic-symbolic")
    line = f"{label:<9}  {preview}   ·  {meta}"
    if with_icon:
        line += f"\x00icon\x1f{icon}"
    return line


def _rows(entries: list[Entry], with_icon: bool) -> tuple[list[str], dict[str, Entry]]:
    rows: list[str] = []
    mapping: dict[str, Entry] = {}
    seen: dict[str, int] = {}
    for entry in entries:
        row = _row(entry, with_icon)
        visible = row.split("\x00", 1)[0]
        count = seen.get(visible, 0) + 1
        seen[visible] = count
        if count > 1:
            visible = f"{visible}  #{entry.hash[:8]}"
            row = visible + (row[len(row.split("\x00", 1)[0]) :] if with_icon else "")
        rows.append(row)
        mapping[visible] = entry
    return rows, mapping


def _pick(stdout: str, mapping: dict[str, Entry], entries: list[Entry]) -> Entry | None:
    line = stdout.rstrip("\n").splitlines()[-1] if stdout.strip() else ""
    if line in mapping:
        return mapping[line]
    for visible, entry in mapping.items():
        if visible == line:
            return entry
    # Rofi may return the visible text with different spacing; match by prefix.
    compact = " ".join(line.split())
    for visible, entry in mapping.items():
        if " ".join(visible.split()) == compact:
            return entry
    return None


def _run_menu(
    cmd: list[str],
    rows: list[str],
    on_proc=None,
) -> subprocess.CompletedProcess | None:
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None
    if on_proc:
        on_proc(proc)
    try:
        out, err = proc.communicate(input="\n".join(rows) + "\n", timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    finally:
        if on_proc:
            on_proc(None)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def show_rofi(entries: list[Entry], prompt: str = "clipboard", on_proc=None) -> MenuResult:
    if not entries:
        return MenuResult(cancelled=True)
    rows, mapping = _rows(entries, with_icon=True)
    base = [
        "rofi",
        "-no-config",
        "-dmenu",
        "-i",
        "-no-custom",
        "-no-fixed-num-lines",
        "-p",
        prompt,
        "-show-icons",
        "-kb-custom-1",
        "Alt+Return",
        "-kb-custom-2",
        "Alt+p",
        "-kb-custom-3",
        "Alt+d",
    ]
    theme = theme_path()
    variants = [base + (["-theme", str(theme)] if theme else [])]
    if theme:
        variants.append(base)
    result = None
    for cmd in variants:
        result = _run_menu(cmd, rows, on_proc=on_proc)
        if result is None:
            return MenuResult(cancelled=True)
        if result.returncode != 1 or result.stdout.strip() or "theme" not in result.stderr.lower():
            break
    if result is None or result.returncode not in {0, 10, 11, 12}:
        return MenuResult(cancelled=True)
    entry = _pick(result.stdout, mapping, entries)
    if entry is None:
        return MenuResult(cancelled=True)
    action = {10: "context", 11: "pin", 12: "delete"}.get(result.returncode)
    return MenuResult(entry=entry, action=action)


def show_fuzzel(entries: list[Entry], prompt: str = "clipboard", on_proc=None) -> MenuResult:
    if not entries:
        return MenuResult(cancelled=True)
    rows, mapping = _rows(entries, with_icon=False)
    cmd = [
        "fuzzel",
        "--dmenu",
        "--index",
        "--lines=12",
        "--width=90",
        "--background-color=16161eff",
        "--text-color=c0caf5ff",
        "--prompt-color=bb9af7ff",
        "--placeholder-color=565f89ff",
        "--selection-color=24283bff",
        "--selection-text-color=ffffffff",
        "--border-color=2f334dff",
        "--border-width=1",
        "--border-radius=0",
        "--selection-radius=0",
        f"--prompt={prompt}  ",
    ]
    result = _run_menu(cmd, rows, on_proc=on_proc)
    if result is None or result.returncode != 0:
        return MenuResult(cancelled=True)
    output = result.stdout.strip()
    entry = None
    if output.isdigit():
        index = int(output)
        if 0 <= index < len(entries):
            entry = entries[index]
    if entry is None:
        entry = _pick(result.stdout, mapping, entries)
    if entry is None:
        return MenuResult(cancelled=True)
    return MenuResult(entry=entry)


def prompt_rofi(title: str, default: str) -> str | None:
    theme = theme_path()
    argv = ["rofi", "-no-config", "-dmenu", "-i", "-p", title]
    if theme:
        argv += ["-theme", str(theme)]
    try:
        result = subprocess.run(
            argv,
            input=default + "\n",
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def prompt_fuzzel(title: str, default: str) -> str | None:
    try:
        result = subprocess.run(
            ["fuzzel", "--dmenu", "--prompt", title + "  "],
            input=default + "\n",
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None
