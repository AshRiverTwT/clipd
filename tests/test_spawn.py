from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from unittest.mock import patch

from clipd import spawn, util
from clipd.clipboard import ClipboardWriter, ClipboardWatcher


class SpawnTests(unittest.TestCase):
    def test_argv_validation(self) -> None:
        with self.assertRaises(ValueError):
            spawn.run([])
        with self.assertRaises(ValueError):
            spawn.run(["echo", "a\x00b"])

    def test_timeout_kills_process(self) -> None:
        started = time.monotonic()
        result = spawn.run(["sleep", "5"], timeout=0.2)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 3)

    def test_environment_and_output(self) -> None:
        result = spawn.run(
            ["sh", "-c", "printf '%s' \"$CLIPD_TEST\""],
            timeout=2,
            env={"CLIPD_TEST": "ok"},
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "ok")

    def test_open_uri_rejects_nothing_here(self) -> None:
        # open_uri itself is not invoked; validation happens in actions.
        self.assertIsNone(spawn.terminal_argv('"'))
        self.assertEqual(spawn.terminal_argv("foot")[0], "foot")

    @patch("clipd.spawn.run")
    @patch("clipd.spawn.shutil.which")
    @patch("gi.repository.Gio.AppInfo.launch_default_for_uri", return_value=False)
    def test_open_uri_falls_back_to_xdg_open(self, _gio, which, run) -> None:
        which.side_effect = lambda name: f"/usr/bin/{name}"
        run.return_value = spawn.CommandResult(True, 0, "", "")
        self.assertTrue(spawn.open_uri("https://example.com"))
        run.assert_called_once_with(["xdg-open", "https://example.com"], timeout=8)

    def test_git_clone_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "existing"
            dest.mkdir()
            ok, message = spawn.git_clone("https://example.com/a/b.git", dest)
            self.assertFalse(ok)
            self.assertIn("exists", message)


class ClipboardTests(unittest.TestCase):
    def test_self_write_window(self) -> None:
        writer = ClipboardWriter()
        writer.last_hash = "abc"
        writer.last_time = time.monotonic()
        self.assertTrue(writer.was_recent("abc"))
        self.assertFalse(writer.was_recent("def"))
        writer.last_time = time.monotonic() - 5
        self.assertFalse(writer.was_recent("abc"))

    def test_watcher_command_contains_ingest(self) -> None:
        watcher = ClipboardWatcher()
        self.assertIn("--watch", watcher.command)
        self.assertIn("_ingest", watcher.command)
        self.assertFalse(watcher.is_alive())


class UtilTests(unittest.TestCase):
    def test_preview_strips_controls_and_bidi(self) -> None:
        text = "hello\x00 \u202e world\nnext"
        preview = util.preview_text(text)
        self.assertNotIn("\x00", preview)
        self.assertNotIn("\u202e", preview)
        self.assertIn("hello", preview)

    def test_preview_truncates(self) -> None:
        preview = util.preview_text("x" * 1000, limit=20)
        self.assertLessEqual(len(preview), 20)
        self.assertTrue(preview.endswith("…"))

    def test_human_size(self) -> None:
        self.assertEqual(util.human_size(10), "10 B")
        self.assertEqual(util.human_size(2048), "2.0 KB")

    def test_relative_time(self) -> None:
        now = 10000
        self.assertEqual(util.relative_time(now - 5, now), "now")
        self.assertEqual(util.relative_time(now - 120, now), "2m")

    def test_slugify(self) -> None:
        self.assertEqual(util.slugify("Hello, World!"), "hello-world")
        self.assertEqual(util.slugify("!!!"), "snippet")

    def test_unique_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = util.unique_path(Path(tmp), "x", ".txt")
            first.write_text("a")
            second = util.unique_path(Path(tmp), "x", ".txt")
            self.assertNotEqual(first, second)
            self.assertTrue(second.name.startswith("x-2"))


if __name__ == "__main__":
    unittest.main()
