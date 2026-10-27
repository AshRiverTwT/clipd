from __future__ import annotations

import unittest

from clipd.classify import detect, secrets


class ClassifyTests(unittest.TestCase):
    CASES = [
        ("https://example.com/a?b=1", "url"),
        ("www.google.com", "url"),
        ("google.com/search?q=clipd", "url"),
        ("https://github.com/hyprwm/Hyprland", "git-repo"),
        ("git@github.com:hyprwm/Hyprland.git", "git-repo"),
        ("/etc/hosts", "file"),
        ("/tmp", "directory"),
        ("/etc/hosts\n/tmp", "file-list"),
        ("git status --short", "command"),
        ("sudo systemctl restart clipd", "command"),
        ("192.168.1.10", "ip"),
        ("10.0.0.0/8", "ip"),
        ("dev@example.com", "email"),
        ("#d9b978", "color"),
        ('{"a": 1, "b": [2, 3]}', "json"),
        ("diff --git a/a b/a\n@@ -1 +1 @@\n-a\n+b", "diff"),
        ("def fib(n):\n    return n\n", "code"),
        ("hello, this is a note", "plain-text"),
        (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjMifQ."
            "sig",
            "jwt",
        ),
    ]

    def test_table(self) -> None:
        for text, kind in self.CASES:
            with self.subTest(text=text[:30]):
                detection = detect.classify(text)
                self.assertEqual(detection.kind, kind, detection.meta)

    def test_git_repo_is_not_plain_url(self) -> None:
        detection = detect.classify("https://github.com/hyprwm/Hyprland")
        self.assertEqual(detection.kind, "git-repo")
        self.assertEqual(detection.meta["repo"], "Hyprland")

    def test_wiki_url_stays_url(self) -> None:
        detection = detect.classify("https://wiki.hypr.land/Configuring/Basics/Binds/")
        self.assertEqual(detection.kind, "url")
        self.assertEqual(detection.meta["domain"], "wiki.hypr.land")

    def test_json_invalid_falls_through(self) -> None:
        self.assertNotEqual(detect.classify('{"a": }').kind, "json")

    def test_code_language(self) -> None:
        detection = detect.classify("use std::io;\nfn main() {\n    println!(\"hi\");\n}\n")
        self.assertEqual(detection.kind, "code")
        self.assertEqual(detection.language, "rust")

    def test_sql_language(self) -> None:
        detection = detect.classify("SELECT id, name\nFROM users\nWHERE id = 1;")
        self.assertEqual(detection.language, "sql")

    def test_diff_stats(self) -> None:
        detection = detect.classify(
            "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1,2 @@\n-old\n+new\n+more\n"
        )
        self.assertEqual(detection.meta["files"], 1)
        self.assertGreaterEqual(detection.meta["added"], 2)

    def test_empty(self) -> None:
        self.assertEqual(detect.classify("   \n\t").kind, "empty")

    def test_large_text(self) -> None:
        text = "lorem ipsum " * 100_000
        detection = detect.classify(text)
        self.assertEqual(detection.kind, "plain-text")
        self.assertLess(detection.confidence, 0.6)


class SecretTests(unittest.TestCase):
    def test_high_confidence_patterns(self) -> None:
        cases = [
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
            "xoxb-1234567890-abcdef",
            "AIza" + "a" * 35,
        ]
        for text in cases:
            with self.subTest(text=text[:20]):
                self.assertIsNotNone(secrets.find_secret(text))

    def test_does_not_drop_normal_code(self) -> None:
        cases = [
            "const token = process.env.TOKEN;",
            "# use a password manager, not this string",
            "SELECT password FROM users;",
            "sk-learn is a Python library",
            "sk-123",
            "AKIA",
        ]
        for text in cases:
            with self.subTest(text=text[:20]):
                self.assertIsNone(secrets.find_secret(text))


if __name__ == "__main__":
    unittest.main()
