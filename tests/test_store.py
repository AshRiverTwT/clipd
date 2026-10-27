from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from clipd.classify import detect
from clipd.models import Detection
from clipd.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.db"
        self.store = Store(self.path, max_entries=5)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def add(self, text: str, kind: str = "plain-text") -> int:
        result = self.store.add(text, Detection(kind, 0.8, {}))
        return result.entry.id

    def test_dedupe_moves_to_top(self) -> None:
        first = self.store.add("hello", detect.classify("hello"))
        second = self.store.add("hello", detect.classify("hello"))
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.entry.use_count, 1)
        self.assertEqual(len(self.store.list_summaries()), 1)

    def test_retention_keeps_newest(self) -> None:
        for i in range(12):
            self.add(f"entry {i}")
        entries = self.store.list_summaries()
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0].preview, "entry 11")
        self.assertEqual(entries[-1].preview, "entry 7")

    def test_pinned_exempt_from_trim(self) -> None:
        pinned = self.add("keep me")
        self.store.set_pinned(pinned, True)
        for i in range(12):
            self.add(f"entry {i}")
        entries = self.store.list_summaries()
        self.assertEqual(len(entries), 6)
        self.assertTrue(entries[0].pinned)
        self.assertEqual(entries[0].id, pinned)

    def test_touch_order(self) -> None:
        a = self.add("a")
        self.add("b")
        self.store.touch(a)
        self.assertEqual(self.store.list_summaries()[0].id, a)

    def test_delete_and_restore(self) -> None:
        entry_id = self.add("recover me")
        entry = self.store.delete(entry_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.text, "recover me")
        self.assertEqual(len(self.store.list_summaries()), 0)
        restored = self.store.restore(entry)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.text, "recover me")

    def test_clear_keep_pinned(self) -> None:
        pinned = self.add("pin")
        self.store.set_pinned(pinned, True)
        self.add("drop")
        self.assertEqual(self.store.clear(keep_pinned=True), 1)
        self.assertEqual(len(self.store.list_summaries()), 1)
        self.assertEqual(self.store.clear(keep_pinned=False), 1)
        self.assertEqual(len(self.store.list_summaries()), 0)

    def test_summaries_do_not_load_text(self) -> None:
        self.add("body")
        summary = self.store.list_summaries()[0]
        self.assertEqual(summary.text, "")
        full = self.store.get(summary.id)
        self.assertEqual(full.text, "body")

    def test_stats_and_permissions(self) -> None:
        self.add("x")
        self.store.bump_stat("skipped_sensitive")
        stats = self.store.stats()
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["skipped_sensitive"], 1)
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0)

    def test_pin_toggle(self) -> None:
        entry_id = self.add("x")
        self.store.set_pinned(entry_id, True)
        self.assertTrue(self.store.get(entry_id).pinned)
        self.store.set_pinned(entry_id, False)
        self.assertFalse(self.store.get(entry_id).pinned)

    def test_image_round_trip(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
        result = self.store.add(
            "",
            Detection("image", 1.0, {"mime": "image/png", "width": 4, "height": 3}),
            image_data=data,
            mime="image/png",
        )
        self.assertEqual(result.entry.kind, "image")
        self.assertEqual(result.entry.size, len(data))
        self.assertEqual(result.entry.mime, "image/png")
        summary = self.store.list_summaries()[0]
        self.assertEqual(summary.mime, "image/png")
        full = self.store.get(summary.id)
        self.assertEqual(full.data, data)
        self.assertEqual(full.mime, "image/png")

    def test_meta_round_trip(self) -> None:
        self.store.add(
            "url",
            Detection("url", 0.9, {"domain": "example.com", "url": "https://example.com"}),
        )
        entry = self.store.list_summaries()[0]
        self.assertEqual(entry.meta["domain"], "example.com")
        self.assertEqual(entry.confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
