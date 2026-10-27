from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from clipd import util
from clipd.ipc import send_command


class DaemonIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(
            {
                "CLIPD_CONFIG_DIR": str(root / "config"),
                "CLIPD_DATA_DIR": str(root / "data"),
                "CLIPD_RUNTIME_DIR": str(root / "run"),
                "CLIPD_NO_PRELOAD": "1",
                "CLIPD_NO_START": "1",
                "CLIPD_LOG": "ERROR",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            }
        )
        self.root = root
        self.bin = str(Path(__file__).resolve().parents[1] / "bin" / "clipd")
        self.proc = subprocess.Popen(
            [self.bin, "daemon", "--headless"],
            env=self.env,
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.sock = root / "run" / "sock"
        for _ in range(60):
            if self.sock.exists():
                try:
                    if send_command({"cmd": "ping"}, path=self.sock)["ok"]:
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        else:
            self.proc.kill()
            self.fail("daemon did not start")
        # The watcher deliberately ignores its initial selection for 0.75s.
        time.sleep(0.8)

    def tearDown(self) -> None:
        try:
            send_command({"cmd": "quit"}, path=self.sock, timeout=1)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.tmp.cleanup()

    def run_cli(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.bin, *args],
            env=self.env,
            cwd=str(self.root),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=20,
        )

    def ingest(self, text: str) -> None:
        result = self.run_cli("_ingest", input_text=text)
        self.assertEqual(result.returncode, 0, result.stderr)

    def list_entries(self) -> list[dict]:
        result = self.run_cli("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_ingest_classify_pin_delete_undo(self) -> None:
        self.ingest("https://github.com/hyprwm/Hyprland")
        self.ingest("git status --short")
        time.sleep(0.3)
        entries = self.list_entries()
        previews = {entry["preview"] for entry in entries}
        self.assertIn("git status --short", previews)
        self.assertTrue(any(entry["kind"] == "git-repo" for entry in entries))
        target = next(entry for entry in entries if entry["preview"] == "git status --short")

        result = self.run_cli("pin", str(target["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(next(e for e in self.list_entries() if e["id"] == target["id"])["pinned"])

        result = self.run_cli("delete", str(target["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(e["id"] == target["id"] for e in self.list_entries()))

        result = self.run_cli("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(e["preview"] == "git status --short" for e in self.list_entries()))

    def test_sensitive_never_stored(self) -> None:
        token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        self.ingest(token)
        time.sleep(0.2)
        self.assertFalse(any(token in entry["preview"] for entry in self.list_entries()))
        result = self.run_cli("status", "--json")
        status = json.loads(result.stdout)
        self.assertGreaterEqual(status["skipped_sensitive"], 1)

    def test_clear(self) -> None:
        self.ingest("one")
        self.ingest("two")
        time.sleep(0.2)
        result = self.run_cli("clear", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.list_entries(), [])


if __name__ == "__main__":
    unittest.main()
