from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from . import util
from .classify import detect as classify_mod
from .models import AddResult, Detection, Entry


class StoreError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id           INTEGER PRIMARY KEY,
    hash         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    preview      TEXT NOT NULL,
    size         INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 0,
    pinned       INTEGER NOT NULL DEFAULT 0,
    truncated    INTEGER NOT NULL DEFAULT 0,
    confidence   REAL NOT NULL DEFAULT 0.5,
    language     TEXT,
    meta         TEXT NOT NULL DEFAULT '{}',
    data         BLOB,
    mime         TEXT
);
CREATE INDEX IF NOT EXISTS idx_order
    ON entries(pinned, last_used_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
PRAGMA user_version = 2;
"""

_SUMMARY_COLS = (
    "id, hash, kind, preview, size, created_at, last_used_at, use_count, "
    "pinned, truncated, confidence, language, meta, mime"
)


def _row_to_entry(row: sqlite3.Row, text: str = "") -> Entry:
    try:
        meta = json.loads(row["meta"]) if row["meta"] else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    keys = row.keys()
    return Entry(
        id=row["id"],
        hash=row["hash"],
        kind=row["kind"],
        text=text,
        preview=row["preview"],
        size=row["size"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        use_count=row["use_count"],
        pinned=bool(row["pinned"]),
        truncated=bool(row["truncated"]),
        confidence=float(row["confidence"]),
        language=row["language"],
        meta=meta if isinstance(meta, dict) else {},
        data=row["data"] if "data" in keys else None,
        mime=row["mime"] if "mime" in keys else None,
    )


class Store:
    def __init__(self, path: Path | None = None, max_entries: int = 50):
        self.path = Path(path).expanduser() if path else util.db_path()
        self.max_entries = max(1, int(max_entries))
        util.ensure_private_dir(self.path.parent)
        try:
            self.conn = sqlite3.connect(str(self.path), timeout=2.0)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open database: {exc}") from exc
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=2000")
        self._init_schema()
        self._secure_permissions()

    def _init_schema(self) -> None:
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, 1, 2):
            raise StoreError(f"unsupported database version {version}")
        if version == 0:
            self.conn.executescript(_SCHEMA)
            self.conn.commit()
        elif version == 1:
            self.conn.execute("ALTER TABLE entries ADD COLUMN data BLOB")
            self.conn.execute("ALTER TABLE entries ADD COLUMN mime TEXT")
            self.conn.execute("PRAGMA user_version = 2")
            self.conn.commit()
        try:
            check = self.conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"database check failed: {exc}") from exc
        if not check or check[0] != "ok":
            raise StoreError(f"database check failed: {check[0] if check else 'unknown'}")

    def _secure_permissions(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                try:
                    os.chmod(candidate, 0o600)
                except OSError:
                    pass

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- writes ---------------------------------------------------------

    def _next_time(self, now: int | None = None) -> int:
        stamp = int(time.time()) if now is None else int(now)
        row = self.conn.execute("SELECT MAX(last_used_at) FROM entries").fetchone()
        latest = int(row[0]) if row and row[0] is not None else 0
        return max(stamp, latest + 1)

    def add(
        self,
        text: str,
        detection: Detection | None = None,
        now: int | None = None,
        max_entry_bytes: int | None = None,
        image_data: bytes | None = None,
        mime: str | None = None,
    ) -> AddResult:
        now = self._next_time(now)
        if image_data is not None:
            data = bytes(image_data)
            if not data:
                raise StoreError("empty image")
            text = ""
            digest = util.sha256_bytes(data)
            size = len(data)
            mime = mime or "image/png"
            if detection is None:
                detection = Detection("image", 1.0, {"mime": mime, "bytes": size})
            preview = f"Image · {util.human_size(size)} · {mime.split('/')[-1].upper()}"
            truncated = False
        else:
            data = None
            if detection is None:
                detection = classify_mod.classify(text)
            digest = util.sha256_text(text)
            size = len(text.encode("utf-8", "replace"))
            preview = util.preview_text(text)
            truncated = bool(max_entry_bytes and size > max_entry_bytes)
        meta = json.dumps(detection.meta or {}, ensure_ascii=False, separators=(",", ":"))

        existing = self.conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM entries WHERE hash = ?", (digest,)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE entries SET last_used_at = ?, use_count = use_count + 1, "
                "kind = ?, confidence = ?, language = ?, meta = ?, mime = COALESCE(?, mime) "
                "WHERE id = ?",
                (
                    now,
                    detection.kind,
                    float(detection.confidence),
                    detection.language,
                    meta,
                    mime,
                    existing["id"],
                ),
            )
            self.conn.commit()
            entry = _row_to_entry(existing)
            entry.last_used_at = now
            entry.use_count = int(existing["use_count"]) + 1
            entry.kind = detection.kind
            entry.meta = detection.meta or {}
            entry.confidence = detection.confidence
            entry.language = detection.language
            entry.data = data
            entry.mime = mime
            return AddResult(entry=entry, created=False)

        cursor = self.conn.execute(
            "INSERT INTO entries (hash, kind, text, preview, size, created_at, "
            "last_used_at, use_count, pinned, truncated, confidence, language, meta, data, mime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                detection.kind,
                text,
                preview,
                size,
                now,
                now,
                1 if truncated else 0,
                float(detection.confidence),
                detection.language,
                meta,
                data,
                mime,
            ),
        )
        self.conn.commit()
        new_id = int(cursor.lastrowid)
        self.trim()
        self._secure_permissions()
        row = self.conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM entries WHERE id = ?", (new_id,)
        ).fetchone()
        if row is None:
            raise StoreError("entry disappeared after insert")
        entry = _row_to_entry(row)
        entry.data = data
        entry.mime = mime
        return AddResult(entry=entry, created=True)

    def touch(self, entry_id: int, now: int | None = None) -> None:
        now = self._next_time(now)
        self.conn.execute(
            "UPDATE entries SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (now, entry_id),
        )
        self.conn.commit()
        self._secure_permissions()

    def set_pinned(self, entry_id: int, pinned: bool) -> None:
        self.conn.execute("UPDATE entries SET pinned = ? WHERE id = ?", (1 if pinned else 0, entry_id))
        self.conn.commit()

    def delete(self, entry_id: int) -> Entry | None:
        entry = self.get(entry_id)
        if entry is None:
            return None
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.conn.commit()
        return entry

    def restore(self, entry: Entry) -> Entry | None:
        """Re-insert a deleted entry (used for undo)."""
        digest = entry.hash or util.sha256_text(entry.text)
        existing = self.conn.execute("SELECT id FROM entries WHERE hash = ?", (digest,)).fetchone()
        if existing:
            self.touch(int(existing["id"]))
            return self.get(int(existing["id"]))
        meta = json.dumps(entry.meta or {}, ensure_ascii=False, separators=(",", ":"))
        cursor = self.conn.execute(
            "INSERT INTO entries (hash, kind, text, preview, size, created_at, last_used_at, "
            "use_count, pinned, truncated, confidence, language, meta, data, mime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                entry.kind,
                entry.text,
                entry.preview or util.preview_text(entry.text),
                entry.size or len(entry.text.encode("utf-8", "replace")),
                entry.created_at,
                entry.last_used_at,
                entry.use_count,
                1 if entry.pinned else 0,
                1 if entry.truncated else 0,
                entry.confidence,
                entry.language,
                meta,
                entry.data,
                entry.mime,
            ),
        )
        self.conn.commit()
        new_id = int(cursor.lastrowid)
        self.trim()
        return self.get(new_id)

    def trim(self) -> int:
        cursor = self.conn.execute(
            "DELETE FROM entries WHERE pinned = 0 AND id NOT IN ("
            "  SELECT id FROM entries WHERE pinned = 0 "
            "  ORDER BY last_used_at DESC, id DESC LIMIT ?)",
            (self.max_entries,),
        )
        self.conn.commit()
        return max(0, cursor.rowcount)

    def clear(self, keep_pinned: bool = True) -> int:
        if keep_pinned:
            cursor = self.conn.execute("DELETE FROM entries WHERE pinned = 0")
        else:
            cursor = self.conn.execute("DELETE FROM entries")
        self.conn.commit()
        return max(0, cursor.rowcount)

    def bump_stat(self, key: str, amount: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO stats(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
            (key, amount),
        )
        self.conn.commit()
        self._secure_permissions()

    def vacuum(self) -> None:
        self.conn.execute("VACUUM")
        self.conn.commit()

    # -- reads ----------------------------------------------------------

    def list_summaries(self, kind: str | None = None) -> list[Entry]:
        if kind:
            rows = self.conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM entries WHERE kind = ? "
                "ORDER BY pinned DESC, last_used_at DESC, id DESC",
                (kind,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM entries "
                "ORDER BY pinned DESC, last_used_at DESC, id DESC"
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    def get(self, entry_id: int) -> Entry | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        return _row_to_entry(row, text=row["text"])

    def get_summary(self, entry_id: int) -> Entry | None:
        row = self.conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def find_by_hash(self, digest: str) -> Entry | None:
        row = self.conn.execute("SELECT * FROM entries WHERE hash = ?", (digest,)).fetchone()
        return _row_to_entry(row, text=row["text"]) if row else None

    def stats(self) -> dict[str, int]:
        total = int(self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
        pinned = int(self.conn.execute("SELECT COUNT(*) FROM entries WHERE pinned = 1").fetchone()[0])
        stats = {"entries": total, "pinned": pinned}
        for row in self.conn.execute("SELECT key, value FROM stats"):
            stats[row["key"]] = int(row["value"])
        try:
            stats["db_bytes"] = self.path.stat().st_size
        except OSError:
            stats["db_bytes"] = 0
        return stats
