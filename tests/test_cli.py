from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from clipd import cli, util
from clipd.cli import _looks_like_html_image
from clipd.classify import detect
from clipd.store import Store


class SetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.hypr = Path(self.tmp.name) / "hypr"
        (self.hypr / "config").mkdir(parents=True)
        (self.hypr / "hyprland.lua").write_text(
            'require("config/keybinds")\nrequire("config/autostart")\n', encoding="utf-8"
        )
        (self.hypr / "config" / "autostart.lua").write_text(
            'hl.exec_cmd("wl-paste --type text --watch cliphist store")\n'
            'hl.exec_cmd("other")\n',
            encoding="utf-8",
        )
        (self.hypr / "config" / "keybinds.lua").write_text(
            'hl.bind(mainMod .. " + SHIFT + V", hl.dsp.exec_cmd("volume"))\n',
            encoding="utf-8",
        )
        self.env = patch.dict(os.environ, {"CLIPD_HYPR_CONFIG_DIR": str(self.hypr)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def run_setup(self, *args: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["setup", *args]), 0)
        return output.getvalue()

    def test_dry_run_does_not_write(self) -> None:
        root_before = (self.hypr / "hyprland.lua").read_text()
        output = self.run_setup("--dry-run")
        self.assertIn("write", output)
        self.assertFalse((self.hypr / "config" / "clipd.lua").exists())
        self.assertEqual((self.hypr / "hyprland.lua").read_text(), root_before)

    def test_apply_is_idempotent_enough(self) -> None:
        with patch("clipd.cli._kill_cliphist") as killer:
            self.run_setup("--apply")
            killer.assert_called_once_with(True)
        self.assertTrue((self.hypr / "config" / "clipd.lua").exists())
        lua = (self.hypr / "config" / "clipd.lua").read_text()
        self.assertIn("clipd toggle", lua)
        self.assertIn('require("config/clipd")', (self.hypr / "hyprland.lua").read_text())
        autostart = (self.hypr / "config" / "autostart.lua").read_text()
        self.assertIn("-- disabled by clipd: hl.exec_cmd", autostart)
        self.assertTrue(any(p.name.startswith("hyprland.lua.bak.") for p in self.hypr.iterdir()))

    def test_existing_super_v_is_preserved(self) -> None:
        (self.hypr / "config" / "keybinds.lua").write_text(
            'hl.bind(mainMod .. " + V", hl.dsp.exec_cmd("other"))\n', encoding="utf-8"
        )
        with patch("clipd.cli._kill_cliphist"):
            self.run_setup("--apply")
        lua = (self.hypr / "config" / "clipd.lua").read_text()
        self.assertNotIn('hl.bind("SUPER + V"', lua)

    def test_has_clipd_bind(self) -> None:
        self.assertFalse(cli._has_clipd_bind(self.hypr))
        (self.hypr / "config" / "clipd.lua").write_text(
            'hl.bind("SUPER + V", hl.dsp.exec_cmd("clipd toggle"))\n', encoding="utf-8"
        )
        self.assertTrue(cli._has_clipd_bind(self.hypr))


class IngestMetadataTests(unittest.TestCase):
    def test_browser_image_metadata_is_recognized(self) -> None:
        self.assertTrue(
            _looks_like_html_image(
                '<meta http-equiv="content-type" content="text/html; charset=utf-8">'
                '<img height="10" width="10" src="https://example.com/a.png">'
            )
        )
        self.assertTrue(_looks_like_html_image('<IMG SRC="https://example.com/a.jpg">'))

    def test_normal_html_is_not_image_metadata(self) -> None:
        self.assertFalse(_looks_like_html_image('<div class="x"><img src="a.png"></div>'))
        self.assertFalse(_looks_like_html_image("just text"))


class ListTests(unittest.TestCase):
    def test_list_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = patch.dict(
                os.environ,
                {
                    "CLIPD_DATA_DIR": tmp,
                    "CLIPD_CONFIG_DIR": str(Path(tmp) / "cfg"),
                    "CLIPD_RUNTIME_DIR": str(Path(tmp) / "run"),
                },
            )
            with env:
                store = Store(util.data_dir() / "history.db", max_entries=50)
                store.add("hello world", detect.classify("hello world"))
                store.close()
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.main(["list", "--json"]), 0)
                data = json.loads(output.getvalue())
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["preview"], "hello world")


if __name__ == "__main__":
    unittest.main()
