from __future__ import annotations

import unittest

from clipd import search
from clipd.models import Entry


def entry(i: int, kind: str, preview: str, pinned: bool = False) -> Entry:
    return Entry(
        id=i,
        hash=f"h{i}",
        kind=kind,
        text=preview,
        preview=preview,
        size=len(preview),
        created_at=0,
        last_used_at=100 - i,
        pinned=pinned,
        meta={},
    )


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = [
            entry(1, "url", "https://github.com/hyprwm/Hyprland", pinned=True),
            entry(2, "code", "def fib(n): return n"),
            entry(3, "command", "git status --short"),
            entry(4, "plain-text", "a note about git"),
            entry(5, "file", "/home/ash/src/app.py"),
        ]

    def test_empty_query_preserves_order(self) -> None:
        result = search.search(self.entries, "")
        self.assertEqual([e.id for e in result], [1, 2, 3, 4, 5])

    def test_kind_filter(self) -> None:
        result = search.search(self.entries, "kind:url")
        self.assertEqual([e.id for e in result], [1])

    def test_git_tab_includes_git_commands(self) -> None:
        entries = [
            entry(1, "git-repo", "https://github.com/hyprwm/Hyprland"),
            entry(2, "command", "git status --short"),
            entry(3, "command", "ls -la"),
        ]
        self.assertEqual([e.id for e in search.search(entries, "", kind="git-repo")], [1, 2])
        self.assertEqual([e.id for e in search.search(entries, "", kind="command")], [2, 3])

    def test_pinned_filter(self) -> None:
        result = search.search(self.entries, "is:pinned")
        self.assertEqual([e.id for e in result], [1])

    def test_text_search(self) -> None:
        result = search.search(self.entries, "git")
        self.assertIn(3, [e.id for e in result])
        self.assertIn(4, [e.id for e in result])

    def test_fuzzy_subsequence(self) -> None:
        result = search.search(self.entries, "gss")
        self.assertIn(3, [e.id for e in result])

    def test_kind_chip_filter(self) -> None:
        result = search.search(self.entries, "", kind="code")
        self.assertEqual([e.id for e in result], [2])

    def test_pin_boosts(self) -> None:
        result = search.search(self.entries, "hyprland")
        self.assertEqual(result[0].id, 1)

    def test_no_match(self) -> None:
        self.assertEqual(search.search(self.entries, "zzzzzz"), [])

    def test_parse_query_prefix(self) -> None:
        query = search.parse_query("is:pinned kind:url hypr")
        self.assertEqual(query.text, "hypr")
        self.assertEqual(query.kind, "url")
        self.assertTrue(query.pinned)


if __name__ == "__main__":
    unittest.main()
