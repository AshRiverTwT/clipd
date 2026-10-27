from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipd.actions import (
    ActionContext,
    ActionResult,
    action_by_id,
    actions_for,
    primary_action,
)
from clipd.classify import detect
from clipd.config import Config
from clipd.models import AddResult, Entry


def make_entry(kind: str, text: str, meta: dict | None = None, language: str | None = None) -> Entry:
    return Entry(
        id=1,
        hash="h",
        kind=kind,
        text=text,
        preview=text[:80],
        size=len(text),
        created_at=0,
        last_used_at=0,
        language=language,
        meta=meta or {},
    )


class FakeClipboard:
    def __init__(self) -> None:
        self.text = ""
        self.values: list[str] = []
        self.image: tuple[bytes, str] | None = None

    def copy(self, text: str) -> bool:
        self.text = text
        self.values.append(text)
        return True

    def copy_image(self, data: bytes, mime: str = "image/png") -> bool:
        self.image = (data, mime)
        return True


class FakeStore:
    def __init__(self) -> None:
        self.items: list[Entry] = []
        self.touched: list[int] = []
        self.next_id = 2

    def add(self, text: str, detection=None) -> AddResult:
        entry = make_entry(
            detection.kind if detection else "plain-text",
            text,
            detection.meta if detection else {},
            detection.language if detection else None,
        )
        entry.id = self.next_id
        self.next_id += 1
        self.items.append(entry)
        return AddResult(entry, True)

    def touch(self, entry_id: int) -> None:
        self.touched.append(entry_id)


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config()
        self.config.actions.snippet_dir = Path(self.tmp.name) / "Snippets"
        self.clipboard = FakeClipboard()
        self.store = FakeStore()
        self.messages: list[str] = []
        self.ctx = ActionContext(
            config=self.config,
            store=self.store,
            clipboard=self.clipboard,
            notify=self.messages.append,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_copy_restores_text(self) -> None:
        entry = make_entry("plain-text", "hello")
        action = action_by_id(entry, self.config, "copy")
        result = action.run(entry, self.ctx)
        self.assertTrue(result.ok)
        self.assertEqual(self.clipboard.text, "hello")
        self.assertEqual(self.store.touched, [1])

    def test_url_actions_are_minimal(self) -> None:
        entry = make_entry(
            "url", "https://example.com", {"url": "https://example.com", "domain": "example.com"}
        )
        ids = [a.id for a in actions_for(entry, self.config)]
        self.assertEqual(ids, ["copy"])
        self.assertEqual(primary_action(entry, self.config).id, "copy")

    def test_git_actions_and_primary(self) -> None:
        entry = make_entry(
            "git-repo",
            "https://github.com/a/b",
            {
                "clone_url": "https://github.com/a/b.git",
                "page_url": "https://github.com/a/b",
                "host": "github.com",
                "path": "a/b",
                "repo": "b",
            },
        )
        self.assertEqual(primary_action(entry, self.config).id, "clone")
        self.assertIn("copy-clone-command", [a.id for a in actions_for(entry, self.config)])

    def test_command_has_no_run_action(self) -> None:
        entry = make_entry("command", "rm -rf /", {"command": "rm -rf /"})
        actions = actions_for(entry, self.config)
        ids = {a.id for a in actions}
        self.assertIn("save-script", ids)
        self.assertNotIn("run", ids)
        self.assertFalse(any("execute" in a.label.lower() for a in actions))

    def test_format_json(self) -> None:
        entry = make_entry("json", '{"b":2,"a":1}', language="json")
        action = action_by_id(entry, self.config, "format-json")
        result = action.run(entry, self.ctx)
        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual(json.loads(self.clipboard.text), {"b": 2, "a": 1})
        self.assertIn("\n", self.clipboard.text)
        self.assertEqual(len(self.store.items), 1)

    def test_invalid_json_fails(self) -> None:
        entry = make_entry("json", "{bad", language="json")
        result = action_by_id(entry, self.config, "format-json").run(entry, self.ctx)
        self.assertFalse(result.ok)
        self.assertEqual(self.clipboard.text, "")

    def test_save_snippet(self) -> None:
        entry = make_entry("code", "print('hi')", language="python")
        result = action_by_id(entry, self.config, "save-snippet").run(entry, self.ctx)
        self.assertTrue(result.ok)
        path = Path(result.message)
        self.assertTrue(path.exists())
        self.assertTrue(path.name.endswith(".py"))
        self.assertEqual(path.read_text(), "print('hi')")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_script_never_executes(self) -> None:
        entry = make_entry("command", "echo hi", {"command": "echo hi"})
        result = action_by_id(entry, self.config, "save-script").run(entry, self.ctx)
        self.assertTrue(result.ok)
        path = Path(result.message)
        self.assertTrue(path.name.endswith(".sh"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertIn("echo hi", path.read_text())

    @patch("clipd.actions.spawn.git_clone", return_value=(True, "cloned"))
    def test_clone_prompt_and_validation(self, clone) -> None:
        entry = make_entry(
            "git-repo",
            "https://github.com/a/b",
            {"clone_url": "https://github.com/a/b.git"},
        )
        target = Path(self.tmp.name) / "repo"
        self.ctx.prompt_text = lambda _title, default: str(target)
        with patch("clipd.actions.validators.parse_git") as parse_git:
            parse_git.return_value = type(
                "R", (), {"clone_url": "https://github.com/a/b.git", "repo": "b"}
            )()
            result = action_by_id(entry, self.config, "clone").run(entry, self.ctx)
        self.assertTrue(result.ok)
        clone.assert_called_once()

    def test_clone_rejects_bad_url(self) -> None:
        entry = make_entry("git-repo", "x", {"clone_url": "javascript:alert(1)"})
        result = action_by_id(entry, self.config, "clone").run(entry, self.ctx)
        self.assertFalse(result.ok)

    def test_disabled_category_removes_actions(self) -> None:
        self.config.actions.enabled = ["copy"]
        entry = make_entry(
            "url", "https://example.com", {"url": "https://example.com", "domain": "example.com"}
        )
        ids = [a.id for a in actions_for(entry, self.config)]
        self.assertEqual(ids, ["copy"])

    def test_image_actions_copy_and_save(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        entry = make_entry("image", "", {"mime": "image/png", "width": 1, "height": 1})
        entry.data = data
        entry.mime = "image/png"
        ids = [a.id for a in actions_for(entry, self.config)]
        self.assertEqual(ids, ["copy", "save-image"])
        result = action_by_id(entry, self.config, "copy").run(entry, self.ctx)
        self.assertTrue(result.ok)
        self.assertEqual(self.clipboard.image, (data, "image/png"))
        result = action_by_id(entry, self.config, "save-image").run(entry, self.ctx)
        self.assertTrue(result.ok)
        self.assertTrue(Path(result.message).read_bytes().startswith(b"\x89PNG"))

    def test_plain_text_has_no_trim_action(self) -> None:
        entry = make_entry("plain-text", "hello")
        self.assertNotIn("trim", [a.id for a in actions_for(entry, self.config)])

    def test_stats_action(self) -> None:
        entry = make_entry("diff", "diff", {"files": 2, "added": 3, "removed": 1})
        result = action_by_id(entry, self.config, "copy-stats").run(entry, self.ctx)
        self.assertTrue(result.ok)
        self.assertIn("2 files", self.clipboard.text)


class SafetyScanTests(unittest.TestCase):
    def test_no_shell_true_or_os_system(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "clipd"
        forbidden = ("shell=True", "os.system(", "os.popen(", "eval(", "exec(")
        for path in root.rglob("*.py"):
            if path.name == "spawn.py":
                continue
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{needle} in {path}")

    def test_all_actions_have_safety(self) -> None:
        config = Config()
        entry = make_entry("url", "https://example.com", {"url": "https://example.com"})
        for action in actions_for(entry, config):
            self.assertIn(action.safety, {"internal", "clipboard", "launch", "network", "disk", "exec"})


if __name__ == "__main__":
    unittest.main()
