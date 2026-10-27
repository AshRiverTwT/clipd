from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gi.repository import GLib

from clipd.ipc import AlreadyRunning, ClientError, IPCServer, daemon_running, send_command


class IPCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sock"
        self.requests: list[dict] = []
        self.server = IPCServer(self.handler, path=self.path)
        self.loop = GLib.MainLoop()
        self.thread = threading.Thread(target=self.loop.run, daemon=True)
        self.server.start()
        self.thread.start()

    def tearDown(self) -> None:
        self.loop.quit()
        self.thread.join(timeout=2)
        self.server.stop()
        self.tmp.cleanup()

    def handler(self, payload: dict) -> dict:
        self.requests.append(payload)
        return {"ok": True, "echo": payload.get("cmd"), "status": {"entries": 1}}

    def test_round_trip(self) -> None:
        response = send_command({"cmd": "ping"}, path=self.path)
        self.assertTrue(response["ok"])
        self.assertEqual(response["echo"], "ping")
        self.assertTrue(daemon_running(self.path))

    def test_second_server_rejected(self) -> None:
        other = IPCServer(self.handler, path=self.path)
        with self.assertRaises(AlreadyRunning):
            other.start()

    def test_bad_json(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(str(self.path))
        sock.sendall(b"{bad json}\n")
        data = sock.recv(4096)
        sock.close()
        response = json.loads(data.decode())
        self.assertFalse(response["ok"])

    def test_unknown_uid_is_not_testable_here(self) -> None:
        # Placeholder: SO_PEERCRED is checked in the server accept path.
        self.assertTrue(self.path.exists())

    def test_client_error_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ClientError):
                send_command({"cmd": "ping"}, path=Path(tmp) / "missing")

    def test_protocol_size_limit(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(str(self.path))
        try:
            sock.sendall(b"x" * (2 * 1024 * 1024 + 10))
        except OSError:
            pass
        sock.close()
        # The daemon must still be alive for subsequent requests.
        time.sleep(0.1)
        response = send_command({"cmd": "ping"}, path=self.path)
        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()
