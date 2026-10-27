from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from . import util

MAX_LINE = 32 * 1024 * 1024


class IPCError(RuntimeError):
    pass


class AlreadyRunning(IPCError):
    pass


class ClientError(IPCError):
    pass


def _encode(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def send_command(payload: dict, timeout: float = 2.0, path: Path | None = None) -> dict:
    path = path or util.socket_path()
    data = _encode(payload)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        sock.sendall(data)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_LINE:
                raise ClientError("response too large")
    except (OSError, TimeoutError) as exc:
        raise ClientError(f"cannot talk to clipd: {exc}") from exc
    finally:
        sock.close()
    raw = b"".join(chunks).strip()
    if not raw:
        raise ClientError("empty response from clipd")
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError(f"invalid response from clipd: {exc}") from exc
    if not isinstance(response, dict):
        raise ClientError("invalid response from clipd")
    return response


class IPCServer:
    """Small one-request-per-connection JSON server on the GLib main loop."""

    def __init__(
        self,
        handler: Callable[[dict], dict],
        path: Path | None = None,
    ) -> None:
        self.handler = handler
        self.path = path or util.socket_path()
        self.sock: socket.socket | None = None
        self.source_id: int | None = None
        self.clients: dict[int, dict] = {}

    def start(self) -> None:
        util.ensure_private_dir(self.path.parent)
        if self.path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.3)
            try:
                probe.connect(str(self.path))
                raise AlreadyRunning("another clipd instance is running")
            except (ConnectionRefusedError, FileNotFoundError, TimeoutError, OSError):
                pass
            finally:
                probe.close()
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setblocking(False)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.listen(16)
        self.source_id = GLib.io_add_watch(
            self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_accept
        )

    def _on_accept(self, _fd: int, _condition: int) -> bool:
        if self.sock is None:
            return False
        while True:
            try:
                conn, _addr = self.sock.accept()
            except BlockingIOError:
                break
            except OSError:
                return True
            try:
                creds = conn.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                )
                _pid, uid, _gid = struct.unpack("3i", creds)
            except (OSError, struct.error):
                conn.close()
                continue
            if uid != os.getuid():
                conn.close()
                continue
            conn.setblocking(False)
            state = {"buffer": b"", "source": None, "conn": conn}
            self.clients[conn.fileno()] = state
            state["source"] = GLib.io_add_watch(
                conn.fileno(),
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._on_readable,
                conn,
            )
        return True

    def _close_client(self, conn: socket.socket, state: dict) -> None:
        source = state.get("source")
        if source is not None:
            try:
                GLib.source_remove(source)
            except GLib.Error:
                pass
        self.clients.pop(conn.fileno(), None)
        try:
            conn.close()
        except OSError:
            pass

    def _on_readable(self, _fd: int, _condition: int, conn: socket.socket) -> bool:
        state = self.clients.get(conn.fileno())
        if state is None:
            return False
        try:
            chunk = conn.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            self._close_client(conn, state)
            return False
        if not chunk:
            self._close_client(conn, state)
            return False
        state["buffer"] += chunk
        if len(state["buffer"]) > MAX_LINE:
            self._respond(conn, state, {"ok": False, "error": "request too large"})
            return False
        if b"\n" not in state["buffer"]:
            return True
        line, _, _rest = state["buffer"].partition(b"\n")
        try:
            payload = json.loads(line.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request must be an object")
            response = self.handler(payload)
            if not isinstance(response, dict):
                response = {"ok": True, "result": response}
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            response = {"ok": False, "error": f"bad request: {exc}"}
        except Exception as exc:  # keep the daemon alive on handler bugs
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._respond(conn, state, response)
        return False

    def _respond(self, conn: socket.socket, state: dict, response: dict) -> None:
        try:
            conn.setblocking(True)
            conn.settimeout(1.0)
            conn.sendall(_encode(response))
        except OSError:
            pass
        finally:
            self._close_client(conn, state)

    def stop(self) -> None:
        for conn_fd, state in list(self.clients.items()):
            source = state.get("source")
            if source is not None:
                try:
                    GLib.source_remove(source)
                except GLib.Error:
                    pass
            try:
                state["conn"].close()
            except OSError:
                pass
        self.clients.clear()
        if self.source_id is not None:
            try:
                GLib.source_remove(self.source_id)
            except GLib.Error:
                pass
            self.source_id = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        try:
            self.path.unlink()
        except OSError:
            pass


def daemon_running(path: Path | None = None) -> bool:
    path = path or util.socket_path()
    if not path.exists():
        return False
    try:
        response = send_command({"cmd": "ping"}, timeout=0.5, path=path)
        return bool(response.get("ok"))
    except ClientError:
        return False
