from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import spawn, util
from .classify import detect as classify_mod
from .classify import validators
from .config import Config
from .models import Entry
from .store import Store

ClipboardLike = object


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str = ""
    changed: bool = False
    close: bool = False


@dataclass(slots=True)
class ActionContext:
    config: Config
    store: Store
    clipboard: object
    notify: Callable[[str], None] = lambda _msg: None
    prompt_text: Callable[[str, str], str | None] | None = None
    run_async: Callable[
        [Callable[[], tuple[bool, str]], Callable[[bool, str], None]], None
    ] | None = None
    on_changed: Callable[[], None] = lambda: None


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    label: str
    icon: str
    category: str
    safety: str
    run: Callable[[Entry, ActionContext], ActionResult]
    shortcut: str | None = None
    tooltip: str = ""


PRIMARY_CONTEXT: dict[str, str] = {
    "url": "copy",
    "file": "open-path",
    "directory": "open-path",
    "file-list": "open-all",
    "git-repo": "clone",
    "command": "copy",
    "code": "save-snippet",
    "json": "format-json",
    "diff": "save-patch",
    "ip": "lookup-ip",
    "email": "compose-email",
    "color": "copy",
    "jwt": "decode-jwt",
    "plain-text": "copy",
    "image": "copy",
}


def _fail(message: str) -> ActionResult:
    return ActionResult(False, message)


def _ok(message: str, *, close: bool = False, changed: bool = False) -> ActionResult:
    return ActionResult(True, message, changed=changed, close=close)


def _copy_text(entry: Entry, ctx: ActionContext, text: str, message: str = "Copied") -> ActionResult:
    if not ctx.clipboard.copy(text):  # type: ignore[attr-defined]
        return _fail("Clipboard write failed")
    ctx.store.touch(entry.id)
    return _ok(message, close=True)


def _copy(entry: Entry, ctx: ActionContext) -> ActionResult:
    if entry.kind == "image" and entry.data:
        if not ctx.clipboard.copy_image(entry.data, entry.mime or "image/png"):  # type: ignore[attr-defined]
            return _fail("Clipboard write failed")
        ctx.store.touch(entry.id)
        return _ok("Image copied", close=True)
    return _copy_text(entry, ctx, entry.text)


def _copy_path(entry: Entry, ctx: ActionContext, quoted: bool = False) -> ActionResult:
    path = entry.meta.get("path")
    if not path:
        return _fail("No path")
    if quoted:
        path = validators.shell_quote(str(path))
    return _copy_text(entry, ctx, str(path), "Path copied")


def _copy_paths(entry: Entry, ctx: ActionContext) -> ActionResult:
    paths = entry.meta.get("paths") or []
    if not paths:
        return _fail("No paths")
    text = "\n".join(validators.shell_quote(str(path)) for path in paths)
    return _copy_text(entry, ctx, text, "Paths copied")


def _open_path(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open"):
        return _fail("Open actions are disabled")
    path = validators.parse_path(str(entry.meta.get("path", "")))
    if path is None:
        return _fail("Path no longer exists")
    if not spawn.open_path(path):
        return _fail("Could not open path")
    return _ok("Opened", close=True)


def _open_all(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open"):
        return _fail("Open actions are disabled")
    paths = entry.meta.get("paths") or []
    opened = 0
    for raw in paths[:10]:
        path = validators.parse_path(str(raw))
        if path and spawn.open_path(path):
            opened += 1
    if not opened:
        return _fail("Nothing could be opened")
    return _ok(f"Opened {opened} item{'s' if opened != 1 else ''}", close=True)


def _reveal(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open"):
        return _fail("Open actions are disabled")
    raw = entry.meta.get("path")
    if not raw:
        return _fail("No path")
    path = Path(str(raw))
    parent = path.parent if path.is_file() else path
    if not parent.exists() or not spawn.open_path(parent):
        return _fail("Could not open containing folder")
    return _ok("Opened folder", close=True)


def _copy_markdown(entry: Entry, ctx: ActionContext) -> ActionResult:
    url = validators.parse_url(str(entry.meta.get("url", "")))
    if url is None:
        return _fail("Invalid URL")
    return _copy_text(entry, ctx, f"[{url.domain}]({url.url})", "Markdown link copied")


def _copy_domain(entry: Entry, ctx: ActionContext) -> ActionResult:
    domain = entry.meta.get("domain")
    if not domain:
        return _fail("No domain")
    return _copy_text(entry, ctx, str(domain), "Domain copied")


def _clone(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("clone"):
        return _fail("Clone is disabled")
    remote = validators.parse_git(str(entry.meta.get("clone_url", "")))
    if remote is None:
        return _fail("Invalid repository URL")
    if util.which("git") is None:
        return _fail("git is not installed")
    default = ctx.config.actions.clone_dir / remote.repo
    dest_text = str(default)
    if ctx.prompt_text and ctx.config.actions.confirm_writes:
        prompted = ctx.prompt_text("Clone repository", dest_text)
        if prompted is None:
            return _ok("Clone cancelled")
        dest_text = prompted
    dest = validators.validate_clone_dest(dest_text)
    if dest is None:
        return _fail("Invalid destination path")
    if dest.exists():
        return _fail(f"{dest} already exists")

    def work() -> tuple[bool, str]:
        ok, message = spawn.git_clone(remote.clone_url, dest)
        return ok, f"Cloned to {dest}" if ok else f"Clone failed: {message}"

    def done(ok: bool, message: str) -> None:
        ctx.notify(message)
        spawn.desktop_notify(message[:180])
        if ok:
            spawn.open_path(dest)

    if ctx.run_async:
        ctx.run_async(work, done)
        return _ok("Cloning…", close=True)
    ok, message = work()
    return _ok(message, close=ok) if ok else _fail(message)


def _open_repo_page(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open") or not ctx.config.actions.network:
        return _fail("Open actions are disabled")
    url = validators.parse_url(str(entry.meta.get("page_url", "")))
    if url is None:
        return _fail("Invalid repository URL")
    if not spawn.open_url(url.url):
        return _fail("Could not open URL")
    return _ok("Opened repository", close=True)


def _copy_clone_command(entry: Entry, ctx: ActionContext) -> ActionResult:
    url = entry.meta.get("clone_url")
    if not url:
        return _fail("No clone URL")
    return _copy_text(entry, ctx, f"git clone {validators.shell_quote(str(url))}", "Clone command copied")


def _copy_slug(entry: Entry, ctx: ActionContext) -> ActionResult:
    host = entry.meta.get("host")
    path = entry.meta.get("path")
    if not host or not path:
        return _fail("No repository")
    return _copy_text(entry, ctx, f"{host}/{path}", "Repository name copied")


def _save(
    entry: Entry,
    ctx: ActionContext,
    *,
    text: str | None = None,
    suffix: str | None = None,
    stem: str | None = None,
    mode: int = 0o600,
    label: str = "Saved snippet",
) -> ActionResult:
    if not ctx.config.action_enabled("save"):
        return _fail("Save actions are disabled")
    content = entry.text if text is None else text
    if suffix is None:
        lang = classify_mod.language_info(entry.language)
        suffix = lang.extension if lang else ".txt"
    if stem is None:
        first = content.strip().splitlines()[0] if content.strip() else entry.kind
        stem = f"{time.strftime('%Y-%m-%d')}-{util.slugify(first, 40)}"
    path = util.unique_path(ctx.config.actions.snippet_dir, stem, suffix)
    if not spawn.write_file(path, content, mode=mode):
        return _fail(f"Could not write {path}")
    ctx.notify(f"{label}: {path}")
    return _ok(str(path))


def _save_snippet(entry: Entry, ctx: ActionContext) -> ActionResult:
    return _save(entry, ctx)


def _save_script(entry: Entry, ctx: ActionContext) -> ActionResult:
    command = str(entry.meta.get("command", entry.text)).strip()
    content = "#!/bin/sh\nset -eu\n" + command + "\n"
    suffix = ".sh"
    stem = f"{time.strftime('%Y-%m-%d')}-{util.slugify(command.split()[0], 24)}"
    return _save(
        entry,
        ctx,
        text=content,
        suffix=suffix,
        stem=stem,
        mode=0o700,
        label="Script saved",
    )


def _save_image(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not entry.data:
        return _fail("No image data")
    if not ctx.config.action_enabled("save"):
        return _fail("Save actions are disabled")
    suffix = util.image_extension(entry.mime)
    stem = f"{time.strftime('%Y-%m-%d')}-clipboard-image"
    path = util.unique_path(ctx.config.actions.snippet_dir, stem, suffix)
    if not spawn.write_bytes(path, entry.data, mode=0o600):
        return _fail(f"Could not write {path}")
    ctx.notify(f"Image saved: {path}")
    return _ok(str(path))


def _save_patch(entry: Entry, ctx: ActionContext) -> ActionResult:
    return _save(entry, ctx, suffix=".patch", stem=f"{time.strftime('%Y-%m-%d')}-changes")


def _copy_prompted(entry: Entry, ctx: ActionContext) -> ActionResult:
    command = str(entry.meta.get("command", entry.text))
    return _copy_text(entry, ctx, f"$ {command}", "Command copied with prompt")


def _open_terminal(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open"):
        return _fail("Open actions are disabled")
    argv = spawn.terminal_argv(ctx.config.general.terminal)
    if not argv:
        return _fail("Invalid terminal command")
    command = str(entry.meta.get("command", entry.text)).strip()
    if not ctx.clipboard.copy(command):  # type: ignore[attr-defined]
        return _fail("Clipboard write failed")
    ctx.store.touch(entry.id)
    if not spawn.launch(argv):
        return _fail("Could not launch terminal")
    return _ok("Terminal opened — paste to run", close=True)


def _lint(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("lint"):
        return _fail("Lint is disabled")
    if util.which("shellcheck") is None:
        return _fail("shellcheck is not installed")
    command = str(entry.meta.get("command", entry.text)).strip()
    handle = tempfile.NamedTemporaryFile(
        mode="w", prefix="clipd-lint-", suffix=".sh", delete=False, encoding="utf-8"
    )
    try:
        handle.write("#!/bin/sh\n" + command + "\n")
        handle.close()
        tmp = Path(handle.name)
        result = spawn.shellcheck(tmp)
    finally:
        try:
            Path(handle.name).unlink()
        except OSError:
            pass
    if result.code == 0:
        return _ok("shellcheck: no issues", close=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    first = lines[0] if lines else "shellcheck reported issues"
    return _ok(first[:160], close=False)


def _format_json(entry: Entry, ctx: ActionContext) -> ActionResult:
    try:
        value = json.loads(entry.text)
        formatted = json.dumps(value, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return _fail("Invalid JSON")
    return _apply(entry, ctx, formatted, "JSON formatted")


def _minify_json(entry: Entry, ctx: ActionContext) -> ActionResult:
    try:
        value = json.loads(entry.text)
        formatted = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return _fail("Invalid JSON")
    return _apply(entry, ctx, formatted, "JSON minified")


def _apply(entry: Entry, ctx: ActionContext, text: str, message: str) -> ActionResult:
    if not ctx.config.action_enabled("format"):
        return _fail("Format actions are disabled")
    detection = classify_mod.classify(text)
    ctx.store.add(text, detection)
    if not ctx.clipboard.copy(text):  # type: ignore[attr-defined]
        return _fail("Clipboard write failed")
    ctx.on_changed()
    ctx.notify(message)
    return _ok(message, changed=True)


def _trim(entry: Entry, ctx: ActionContext) -> ActionResult:
    text = "\n".join(line.rstrip() for line in entry.text.strip().splitlines())
    return _apply(entry, ctx, text, "Whitespace trimmed")


def _case(entry: Entry, ctx: ActionContext, mode: str) -> ActionResult:
    if mode == "upper":
        text = entry.text.upper()
    elif mode == "lower":
        text = entry.text.lower()
    elif mode == "title":
        text = entry.text.title()
    else:
        text = entry.text
    return _apply(entry, ctx, text, "Case changed")


def _copy_fenced(entry: Entry, ctx: ActionContext) -> ActionResult:
    lang = entry.language or classify_mod.detect_language(entry.text) or ""
    fence = f"```{lang}\n{entry.text.rstrip()}\n```"
    return _copy_text(entry, ctx, fence, "Fenced code copied")


def _lookup_ip(entry: Entry, ctx: ActionContext) -> ActionResult:
    if not ctx.config.action_enabled("open") or not ctx.config.actions.network:
        return _fail("Network actions are disabled")
    ip = validators.parse_ip(str(entry.meta.get("ip", "")))
    if ip is None:
        return _fail("Invalid IP address")
    if not spawn.open_url(f"https://ipinfo.io/{ip['ip']}"):
        return _fail("Could not open lookup")
    return _ok("Lookup opened", close=True)


def _compose_email(entry: Entry, ctx: ActionContext) -> ActionResult:
    address = validators.parse_email(str(entry.meta.get("address", "")))
    if address is None:
        return _fail("Invalid email address")
    if not spawn.open_uri(f"mailto:{address}"):
        return _fail("No mail client available")
    return _ok("Composer opened", close=True)


def _decode_jwt(entry: Entry, ctx: ActionContext) -> ActionResult:
    data = validators.parse_jwt(entry.text)
    if data is None:
        return _fail("Invalid JWT")
    text = json.dumps(
        {"header": data["header"], "payload": data["payload"]},
        indent=2,
        ensure_ascii=False,
    )
    return _copy_text(entry, ctx, text, "JWT decoded and copied")


def _copy_hex(entry: Entry, ctx: ActionContext) -> ActionResult:
    value = validators.parse_color(entry.text)
    if value is None:
        return _fail("Invalid colour")
    return _copy_text(entry, ctx, value["hex"], "Hex colour copied")


def _copy_rgb(entry: Entry, ctx: ActionContext) -> ActionResult:
    value = validators.parse_color(entry.text)
    if value is None:
        return _fail("Invalid colour")
    return _copy_text(entry, ctx, value["rgb"], "RGB colour copied")


def _copy_stats(entry: Entry, ctx: ActionContext) -> ActionResult:
    meta = entry.meta
    text = (
        f"{meta.get('files', 0)} files, "
        f"+{meta.get('added', 0)} / -{meta.get('removed', 0)}"
    )
    return _copy_text(entry, ctx, text, "Diff stats copied")


def actions_for(entry: Entry, config: Config) -> list[Action]:
    actions: list[Action] = [
        Action("copy", "Copy", "edit-copy-symbolic", "copy", "clipboard", _copy),
    ]
    kind = entry.kind
    if kind == "url":
        # URL entries intentionally have only the shared Copy action.
        pass
    elif kind in {"file", "directory"}:
        label = "Reveal" if kind == "file" else "Open folder"
        actions += [
            Action("open-path", "Open", "folder-symbolic", "open", "launch", _open_path),
            Action("reveal", label, "folder-open-symbolic", "open", "launch", _reveal),
            Action("copy-path", "Copy path", "edit-copy-symbolic", "copy", "clipboard", _copy_path),
            Action(
                "copy-quoted",
                "Copy quoted",
                "edit-copy-symbolic",
                "copy",
                "clipboard",
                lambda e, c: _copy_path(e, c, quoted=True),
            ),
        ]
    elif kind == "file-list":
        actions += [
            Action("open-all", "Open all", "folder-symbolic", "open", "launch", _open_all),
            Action("copy-paths", "Copy paths", "edit-copy-symbolic", "copy", "clipboard", _copy_paths),
            Action("reveal", "Open folder", "folder-open-symbolic", "open", "launch", _reveal),
        ]
    elif kind == "git-repo":
        actions += [
            Action("clone", "Clone", "folder-download-symbolic", "clone", "exec", _clone),
            Action("open-repo", "Open page", "web-browser-symbolic", "open", "network", _open_repo_page),
            Action(
                "copy-clone-command",
                "Copy git clone",
                "edit-copy-symbolic",
                "copy",
                "clipboard",
                _copy_clone_command,
            ),
            Action("copy-slug", "Copy name", "edit-copy-symbolic", "copy", "clipboard", _copy_slug),
        ]
    elif kind == "image":
        actions += [
            Action("save-image", "Save image", "document-save-symbolic", "save", "disk", _save_image),
        ]
    elif kind == "command":
        actions += [
            Action("copy-prompted", "Copy with $", "edit-copy-symbolic", "copy", "clipboard", _copy_prompted),
            Action("save-script", "Save script", "document-save-symbolic", "save", "disk", _save_script),
            Action("open-terminal", "Open terminal", "utilities-terminal-symbolic", "open", "launch", _open_terminal),
        ]
        if util.which("shellcheck"):
            actions.append(
                Action("lint", "Lint", "dialog-information-symbolic", "lint", "exec", _lint)
            )
    elif kind == "code":
        actions += [
            Action("save-snippet", "Save snippet", "document-save-symbolic", "save", "disk", _save_snippet),
            Action("copy-fenced", "Copy fenced", "edit-copy-symbolic", "copy", "clipboard", _copy_fenced),
        ]
    elif kind == "json":
        actions += [
            Action("format-json", "Format", "format-justify-left-symbolic", "format", "internal", _format_json),
            Action("minify-json", "Minify", "format-justify-fill-symbolic", "format", "internal", _minify_json),
            Action("save-snippet", "Save .json", "document-save-symbolic", "save", "disk", _save_snippet),
        ]
    elif kind == "diff":
        actions += [
            Action("save-patch", "Save patch", "document-save-symbolic", "save", "disk", _save_patch),
            Action("copy-stats", "Copy stats", "edit-copy-symbolic", "copy", "clipboard", _copy_stats),
        ]
    elif kind == "ip":
        actions += [
            Action("lookup-ip", "Look up", "web-browser-symbolic", "open", "network", _lookup_ip),
        ]
    elif kind == "email":
        actions += [
            Action("compose-email", "Compose", "mail-message-new-symbolic", "open", "launch", _compose_email),
        ]
    elif kind == "color":
        actions += [
            Action("copy-hex", "Copy hex", "edit-copy-symbolic", "copy", "clipboard", _copy_hex),
            Action("copy-rgb", "Copy rgb", "edit-copy-symbolic", "copy", "clipboard", _copy_rgb),
        ]
    elif kind == "jwt":
        actions += [
            Action("decode-jwt", "Decode", "dialog-password-symbolic", "format", "internal", _decode_jwt),
        ]
    else:
        actions += [
            Action("save-note", "Save note", "document-save-symbolic", "save", "disk", _save_snippet),
            Action("upper", "UPPER", "format-text-uppercase-symbolic", "format", "internal", lambda e, c: _case(e, c, "upper")),
            Action("lower", "lower", "format-text-lowercase-symbolic", "format", "internal", lambda e, c: _case(e, c, "lower")),
            Action("title", "Title Case", "format-text-uppercase-symbolic", "format", "internal", lambda e, c: _case(e, c, "title")),
        ]

    # Command/code actions are useful on any entry that happens to parse.
    return [action for action in actions if config.action_enabled(action.category)]


def primary_action(entry: Entry, config: Config) -> Action | None:
    action_id = PRIMARY_CONTEXT.get(entry.kind, "copy")
    for action in actions_for(entry, config):
        if action.id == action_id:
            return action
    return None


def action_by_id(entry: Entry, config: Config, action_id: str) -> Action | None:
    for action in actions_for(entry, config):
        if action.id == action_id:
            return action
    return None
