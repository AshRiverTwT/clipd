from __future__ import annotations

import unittest
from pathlib import Path

from clipd.ui import fallback
from clipd.models import Entry


def entry(i: int, kind: str, preview: str) -> Entry:
    return Entry(
        id=i,
        hash=f"hash{i:04d}",
        kind=kind,
        text=preview,
        preview=preview,
        size=len(preview),
        created_at=0,
        last_used_at=0,
        meta={},
    )


class FallbackTests(unittest.TestCase):
    def test_rows_have_icon_and_unique_mapping(self) -> None:
        entries = [entry(1, "url", "https://example.com"), entry(2, "command", "git status")]
        rows, mapping = fallback._rows(entries, with_icon=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(mapping), 2)
        self.assertIn("\x00icon\x1f", rows[0])
        self.assertEqual(mapping[rows[1].split("\x00", 1)[0]].id, 2)

    def test_duplicate_preview_gets_unique_key(self) -> None:
        entries = [entry(1, "plain-text", "same"), entry(2, "plain-text", "same")]
        rows, mapping = fallback._rows(entries, with_icon=False)
        self.assertEqual(len(set(rows)), 2)
        self.assertEqual(len(mapping), 2)
        self.assertTrue(rows[1].endswith("hash0002"))

    def test_pick_last_line(self) -> None:
        entries = [entry(1, "url", "https://example.com")]
        rows, mapping = fallback._rows(entries, with_icon=False)
        picked = fallback._pick("noise\n" + rows[0] + "\n", mapping, entries)
        self.assertEqual(picked.id, 1)

    def test_theme_exists_and_parses_roughly(self) -> None:
        path = fallback.theme_path()
        self.assertIsNotNone(path)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("#bb9af7", text)
        self.assertNotIn("solid", text)

    def test_backend_availability_helpers(self) -> None:
        self.assertIsInstance(fallback.rofi_available(), bool)
        self.assertIsInstance(fallback.fuzzel_available(), bool)


if __name__ == "__main__":
    unittest.main()
