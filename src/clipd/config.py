from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python < 3.11
    import tomli as tomllib  # type: ignore

from . import util

DEFAULT_TOML = """\
# clipd configuration
# Reload with: clipd reload

[general]
max_entries = 50
max_entry_bytes = 1048576      # 1 MiB; larger text copies are skipped
max_image_bytes = 8388608      # 8 MiB; larger images are skipped
auto_paste = "off"             # off | hyprland | wtype
paste_delay_ms = 160
terminal = "foot"

[ui]
frontend = "auto"              # auto | gtk | rofi | fuzzel | none
width = 875
height = 552
primary_action = "copy"        # copy | context
show_preview = true

[privacy]
# Python regular expressions; matching entries are never stored.
exclude_patterns = []
exclude_mime = [
  "x-kde-passwordManagerHint",
  "application/x-kde-passwordManagerHint",
]
pause_on_lock = true
ignore_whitespace = true

[actions]
enabled = ["copy", "open", "clone", "save", "format", "lint"]
clone_dir = "~/src"
snippet_dir = "~/Snippets"
confirm_writes = true
network = true

[watch]
read_timeout_ms = 4000
"""

DEFAULT_EXCLUDES = [
    "x-kde-passwordManagerHint",
    "application/x-kde-passwordManagerHint",
]

_FRONTENDS = {"auto", "gtk", "rofi", "fuzzel", "none"}
_AUTOPASTE = {"off", "hyprland", "wtype"}
_PRIMARY = {"copy", "context"}


@dataclass(slots=True)
class GeneralConfig:
    max_entries: int = 50
    max_entry_bytes: int = 1024 * 1024
    max_image_bytes: int = 8 * 1024 * 1024
    auto_paste: str = "off"
    paste_delay_ms: int = 160
    terminal: str = "foot"


@dataclass(slots=True)
class UIConfig:
    frontend: str = "auto"
    width: int = 875
    height: int = 552
    primary_action: str = "copy"
    show_preview: bool = True


@dataclass(slots=True)
class PrivacyConfig:
    exclude_patterns: list[str] = field(default_factory=list)
    exclude_mime: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    pause_on_lock: bool = True
    ignore_whitespace: bool = True
    compiled_patterns: list[re.Pattern[str]] = field(default_factory=list, repr=False)


@dataclass(slots=True)
class ActionsConfig:
    enabled: list[str] = field(
        default_factory=lambda: ["copy", "open", "clone", "save", "format", "lint"]
    )
    clone_dir: Path = field(default_factory=lambda: Path.home() / "src")
    snippet_dir: Path = field(default_factory=lambda: Path.home() / "Snippets")
    confirm_writes: bool = True
    network: bool = True


@dataclass(slots=True)
class WatchConfig:
    read_timeout_ms: int = 4000


@dataclass(slots=True)
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    path: Path = field(default_factory=util.config_path)
    warnings: list[str] = field(default_factory=list)

    def is_excluded(self, text: str, mime_types: list[str] | tuple[str, ...] = ()) -> str | None:
        if self.privacy.ignore_whitespace and not text.strip():
            return "empty"
        mimes = {m.strip().lower() for m in mime_types}
        blocked = {m.strip().lower() for m in self.privacy.exclude_mime}
        if mimes & blocked:
            return "excluded MIME type"
        for pattern in self.privacy.compiled_patterns:
            if pattern.search(text):
                return f"excluded pattern ({pattern.pattern[:32]})"
        return None

    def action_enabled(self, category: str) -> bool:
        return category in self.actions.enabled


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _int(value: object, default: int, lo: int, hi: int, name: str, warnings: list[str]) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        warnings.append(f"{name}: expected an integer, using {default}")
        return default
    if n < lo or n > hi:
        clipped = max(lo, min(hi, n))
        warnings.append(f"{name}: {n} out of range {lo}..{hi}, using {clipped}")
        return clipped
    return n


def _bool(value: object, default: bool, name: str, warnings: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    warnings.append(f"{name}: expected true/false, using {default}")
    return default


def _str(value: object, default: str, name: str, warnings: list[str]) -> str:
    if isinstance(value, str) and value.strip():
        return value
    warnings.append(f"{name}: expected a non-empty string, using {default!r}")
    return default


def _strlist(value: object, default: list[str], name: str, warnings: list[str]) -> list[str]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value if v.strip()]
    warnings.append(f"{name}: expected a list of strings, using defaults")
    return list(default)


def _path(value: object, default: Path, name: str, warnings: list[str]) -> Path:
    if isinstance(value, str) and value.strip():
        return Path(os.path.expandvars(value)).expanduser()
    warnings.append(f"{name}: expected a path, using {default}")
    return default


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump_config(config: Config) -> str:
    """Serialize the supported configuration fields in a stable TOML shape."""
    patterns = ", ".join(_toml_string(p) for p in config.privacy.exclude_patterns)
    mimes = ", ".join(_toml_string(m) for m in config.privacy.exclude_mime)
    enabled = ", ".join(_toml_string(a) for a in config.actions.enabled)
    return f"""# clipd configuration
# Rewritten by the settings page. Reload with: clipd reload

[general]
max_entries = {config.general.max_entries}
max_entry_bytes = {config.general.max_entry_bytes}
max_image_bytes = {config.general.max_image_bytes}
auto_paste = {_toml_string(config.general.auto_paste)}
paste_delay_ms = {config.general.paste_delay_ms}
terminal = {_toml_string(config.general.terminal)}

[ui]
frontend = {_toml_string(config.ui.frontend)}
width = {config.ui.width}
height = {config.ui.height}
primary_action = {_toml_string(config.ui.primary_action)}
show_preview = {"true" if config.ui.show_preview else "false"}

[privacy]
exclude_patterns = [{patterns}]
exclude_mime = [{mimes}]
pause_on_lock = {"true" if config.privacy.pause_on_lock else "false"}
ignore_whitespace = {"true" if config.privacy.ignore_whitespace else "false"}

[actions]
enabled = [{enabled}]
clone_dir = {_toml_string(str(config.actions.clone_dir))}
snippet_dir = {_toml_string(str(config.actions.snippet_dir))}
confirm_writes = {"true" if config.actions.confirm_writes else "false"}
network = {"true" if config.actions.network else "false"}

[watch]
read_timeout_ms = {config.watch.read_timeout_ms}
"""


def save_config(config: Config, path: Path | None = None) -> None:
    target = Path(path or config.path).expanduser()
    util.ensure_private_dir(target.parent)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(dump_config(config), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def load_config(path: Path | None = None, create: bool = False) -> Config:
    path = Path(path).expanduser() if path else util.config_path()
    warnings: list[str] = []
    data: dict = {}
    if not path.exists():
        if create:
            try:
                util.ensure_private_dir(path.parent)
                path.write_text(DEFAULT_TOML, encoding="utf-8")
                os.chmod(path, 0o600)
            except OSError as exc:
                warnings.append(f"could not write default config: {exc}")
    else:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"could not read config ({exc}); using defaults")

    g = _section(data, "general")
    u = _section(data, "ui")
    p = _section(data, "privacy")
    a = _section(data, "actions")
    w = _section(data, "watch")

    general = GeneralConfig(
        max_entries=_int(g.get("max_entries", 50), 50, 1, 1000, "general.max_entries", warnings),
        max_entry_bytes=_int(
            g.get("max_entry_bytes", 1024 * 1024),
            1024 * 1024,
            1024,
            16 * 1024 * 1024,
            "general.max_entry_bytes",
            warnings,
        ),
        max_image_bytes=_int(
            g.get("max_image_bytes", 8 * 1024 * 1024),
            8 * 1024 * 1024,
            64 * 1024,
            10 * 1024 * 1024,
            "general.max_image_bytes",
            warnings,
        ),
        auto_paste=_str(g.get("auto_paste", "off"), "off", "general.auto_paste", warnings).lower(),
        paste_delay_ms=_int(
            g.get("paste_delay_ms", 160), 160, 0, 3000, "general.paste_delay_ms", warnings
        ),
        terminal=_str(g.get("terminal", "foot"), "foot", "general.terminal", warnings),
    )
    if general.auto_paste not in _AUTOPASTE:
        warnings.append(f"general.auto_paste: unknown value {general.auto_paste!r}, using 'off'")
        general.auto_paste = "off"

    ui = UIConfig(
        frontend=_str(u.get("frontend", "auto"), "auto", "ui.frontend", warnings).lower(),
        width=_int(u.get("width", 875), 875, 520, 1600, "ui.width", warnings),
        height=_int(u.get("height", 552), 552, 320, 1200, "ui.height", warnings),
        primary_action=_str(
            u.get("primary_action", "copy"), "copy", "ui.primary_action", warnings
        ).lower(),
        show_preview=_bool(u.get("show_preview", True), True, "ui.show_preview", warnings),
    )
    if ui.frontend not in _FRONTENDS:
        warnings.append(f"ui.frontend: unknown value {ui.frontend!r}, using 'auto'")
        ui.frontend = "auto"
    if ui.primary_action not in _PRIMARY:
        warnings.append("ui.primary_action: expected 'copy' or 'context', using 'copy'")
        ui.primary_action = "copy"

    privacy = PrivacyConfig(
        exclude_patterns=_strlist(
            p.get("exclude_patterns", []), [], "privacy.exclude_patterns", warnings
        ),
        exclude_mime=_strlist(
            p.get("exclude_mime", list(DEFAULT_EXCLUDES)),
            list(DEFAULT_EXCLUDES),
            "privacy.exclude_mime",
            warnings,
        ),
        pause_on_lock=_bool(p.get("pause_on_lock", True), True, "privacy.pause_on_lock", warnings),
        ignore_whitespace=_bool(
            p.get("ignore_whitespace", True), True, "privacy.ignore_whitespace", warnings
        ),
    )
    for raw in privacy.exclude_patterns:
        try:
            privacy.compiled_patterns.append(re.compile(raw))
        except re.error as exc:
            warnings.append(f"privacy.exclude_patterns: invalid regex {raw!r}: {exc}")

    enabled = _strlist(
        a.get("enabled", ["copy", "open", "clone", "save", "format", "lint"]),
        ["copy", "open", "clone", "save", "format", "lint"],
        "actions.enabled",
        warnings,
    )
    actions = ActionsConfig(
        enabled=enabled,
        clone_dir=_path(
            a.get("clone_dir", "~/src"), Path.home() / "src", "actions.clone_dir", warnings
        ),
        snippet_dir=_path(
            a.get("snippet_dir", "~/Snippets"),
            Path.home() / "Snippets",
            "actions.snippet_dir",
            warnings,
        ),
        confirm_writes=_bool(
            a.get("confirm_writes", True), True, "actions.confirm_writes", warnings
        ),
        network=_bool(a.get("network", True), True, "actions.network", warnings),
    )

    watch = WatchConfig(
        read_timeout_ms=_int(
            w.get("read_timeout_ms", 4000), 4000, 250, 30000, "watch.read_timeout_ms", warnings
        ),
    )

    return Config(
        general=general,
        ui=ui,
        privacy=privacy,
        actions=actions,
        watch=watch,
        path=path,
        warnings=warnings,
    )
