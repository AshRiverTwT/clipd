from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from clipd.config import load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config = load_config(path, create=True)
            self.assertTrue(path.exists())
            self.assertEqual(config.general.max_entries, 50)
            self.assertEqual(config.ui.frontend, "auto")
            self.assertEqual(config.ui.width, 875)
            self.assertEqual(config.ui.height, 552)
            self.assertEqual(config.general.max_image_bytes, 8 * 1024 * 1024)
            self.assertEqual(config.general.auto_paste, "off")
            self.assertEqual(config.warnings, [])

    def test_invalid_values_warn_and_clamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[general]
max_entries = 100000
auto_paste = "always"
[ui]
frontend = "kitty"
width = 10
[privacy]
exclude_patterns = ["(", "good"]
""",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.general.max_entries, 1000)
            self.assertEqual(config.general.auto_paste, "off")
            self.assertEqual(config.ui.frontend, "auto")
            self.assertEqual(config.ui.width, 520)
            self.assertTrue(any("exclude_patterns" in warning for warning in config.warnings))
            self.assertTrue(any("auto_paste" in warning for warning in config.warnings))
            self.assertEqual(len(config.privacy.compiled_patterns), 1)

    def test_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[privacy]
exclude_patterns = ["secret-\\\\d+"]
exclude_mime = ["x-kde-passwordManagerHint"]
""",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertIsNotNone(config.is_excluded("my secret-123"))
            self.assertIsNotNone(config.is_excluded("plain", ["x-kde-passwordManagerHint"]))
            self.assertIsNotNone(config.is_excluded("   "))
            self.assertIsNone(config.is_excluded("ordinary text"))

    def test_paths_expand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[actions]
clone_dir = "~/src"
snippet_dir = "~/Snippets"
""",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.actions.clone_dir, Path.home() / "src")
            self.assertEqual(config.actions.snippet_dir, Path.home() / "Snippets")

    def test_dump_and_save_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            config = load_config(path, create=True)
            config.general.max_image_bytes = 4 * 1024 * 1024
            config.ui.width = 900
            config.ui.height = 600
            config.actions.network = False
            config.privacy.exclude_patterns = ["secret-\\d+"]
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.general.max_image_bytes, 4 * 1024 * 1024)
            self.assertEqual(loaded.ui.width, 900)
            self.assertEqual(loaded.ui.height, 600)
            self.assertFalse(loaded.actions.network)
            self.assertEqual(loaded.privacy.exclude_patterns, ["secret-\\d+"])

    def test_parse_error_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("this is not toml ====", encoding="utf-8")
            config = load_config(path)
            self.assertTrue(config.warnings)
            self.assertEqual(config.general.max_entries, 50)


if __name__ == "__main__":
    unittest.main()
