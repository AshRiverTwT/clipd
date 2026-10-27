from __future__ import annotations

import unittest

from clipd.classify import validators


class UrlTests(unittest.TestCase):
    def test_http_urls(self) -> None:
        for raw in (
            "https://example.com",
            "http://example.com/path?q=1#frag",
            "https://localhost:8080/x",
            "http://192.168.1.10:3000",
        ):
            with self.subTest(raw=raw):
                info = validators.parse_url(raw)
                self.assertIsNotNone(info)
                self.assertEqual(info.scheme, raw.split(":", 1)[0])

    def test_scheme_less_links(self) -> None:
        for raw, expected in (
            ("www.google.com", "https://www.google.com"),
            ("WWW.GOOGLE.COM", "https://WWW.GOOGLE.COM"),
            ("google.com", "https://google.com"),
            ("example.com/path?q=1#frag", "https://example.com/path?q=1#frag"),
            ("example.co.uk", "https://example.co.uk"),
            ("www.google.com.", "https://www.google.com"),
        ):
            with self.subTest(raw=raw):
                info = validators.parse_url(raw)
                self.assertIsNotNone(info)
                self.assertEqual(info.url, expected)
                self.assertEqual(info.scheme, "https")

    def test_filename_is_not_a_link(self) -> None:
        for raw in ("README.md", "script.py", "config.toml", "photo.png"):
            with self.subTest(raw=raw):
                self.assertIsNone(validators.parse_url(raw))

    def test_rejects_dangerous_schemes(self) -> None:
        for raw in (
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/plain,hello",
            "vbscript:msgbox(1)",
            "https://user:pass@example.com",
            "http://foo",
            "https://example.com/a b",
            "https://example.com\\@evil.com",
            "https://example.com:99999",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(validators.parse_url(raw))

    def test_domain(self) -> None:
        info = validators.parse_url("https://Example.COM:443/a")
        self.assertEqual(info.host, "example.com")
        self.assertEqual(info.domain, "example.com:443")


class GitTests(unittest.TestCase):
    def test_scp_remote(self) -> None:
        remote = validators.parse_git("git@github.com:hyprwm/Hyprland.git")
        self.assertIsNotNone(remote)
        self.assertEqual(remote.host, "github.com")
        self.assertEqual(remote.repo, "Hyprland")
        self.assertEqual(remote.clone_url, "https://github.com/hyprwm/Hyprland.git")

    def test_https_remote(self) -> None:
        remote = validators.parse_git("https://github.com/owner/repo")
        self.assertIsNotNone(remote)
        self.assertEqual(remote.page_url, "https://github.com/owner/repo")
        self.assertEqual(remote.clone_url, "https://github.com/owner/repo.git")

    def test_github_extra_path_is_url_not_git(self) -> None:
        self.assertIsNone(validators.parse_git("https://github.com/owner/repo/issues"))
        self.assertIsNotNone(validators.parse_url("https://github.com/owner/repo/issues"))

    def test_unknown_host_requires_git_marker(self) -> None:
        self.assertIsNone(validators.parse_git("https://wiki.hypr.land/Configuring/Basics"))
        self.assertIsNotNone(validators.parse_git("https://forge.example.com/a/b.git"))
        self.assertIsNotNone(validators.parse_git("https://git.example.com/a/b"))

    def test_rejects_injection(self) -> None:
        for raw in (
            "https://github.com/a/b;rm -rf /",
            "https://github.com/a/--upload-pack=x",
            "https://user:pass@github.com/a/b",
            "git@github.com:a/b ext::sh",
            "https://github.com/a/b?x=1",
            "https://github.com/a",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(validators.parse_git(raw))

    def test_ssh_url(self) -> None:
        remote = validators.parse_git("ssh://git@gitlab.com/group/sub/repo.git")
        self.assertIsNotNone(remote)
        self.assertEqual(remote.path, "group/sub/repo")
        self.assertTrue(remote.ssh_url.startswith("git@gitlab.com:"))


class PathTests(unittest.TestCase):
    def test_existing_path(self) -> None:
        path = validators.parse_path("/etc/hosts")
        self.assertIsNotNone(path)
        self.assertTrue(path.is_absolute())

    def test_missing_path(self) -> None:
        self.assertIsNone(validators.parse_path("/definitely/not/here-123"))
        self.assertIsNotNone(validators.parse_path("/definitely/not/here-123", must_exist=False))

    def test_quoted_and_file_uri(self) -> None:
        self.assertIsNotNone(validators.parse_path('"/etc/hosts"'))
        self.assertIsNotNone(validators.parse_path("file:///etc/hosts"))
        self.assertIsNone(validators.parse_path("file://host/etc/hosts"))

    def test_multiple_paths(self) -> None:
        paths = validators.parse_paths("/etc/hosts\n/tmp")
        self.assertIsNotNone(paths)
        self.assertEqual(len(paths), 2)
        self.assertIsNone(validators.parse_paths("/etc/hosts\n/nope/nope"))

    def test_rejects_newline_in_path(self) -> None:
        self.assertIsNone(validators.parse_path("/etc/hosts\n/etc/passwd"))

    def test_clone_destination(self) -> None:
        self.assertIsNotNone(validators.validate_clone_dest("~/src/repo"))
        self.assertIsNotNone(validators.validate_clone_dest("/tmp/repo"))
        self.assertIsNone(validators.validate_clone_dest("relative/repo"))
        self.assertIsNone(validators.validate_clone_dest("/"))
        self.assertIsNone(validators.validate_clone_dest("line\nbreak"))


class OtherTests(unittest.TestCase):
    def test_ip_and_cidr(self) -> None:
        self.assertEqual(validators.parse_ip("10.0.0.1")["version"], 4)
        self.assertEqual(validators.parse_ip("2001:db8::1")["version"], 6)
        self.assertEqual(validators.parse_ip("10.0.0.0/8")["cidr"], "10.0.0.0/8")
        self.assertIsNone(validators.parse_ip("999.1.1.1"))

    def test_email(self) -> None:
        self.assertEqual(validators.parse_email("dev@example.com"), "dev@example.com")
        self.assertIsNone(validators.parse_email("not an email"))
        self.assertIsNone(validators.parse_email("a@b"))

    def test_colors(self) -> None:
        self.assertEqual(validators.parse_color("#fff")["hex"], "#ffffff")
        self.assertEqual(validators.parse_color("rgb(255, 0, 10)")["hex"], "#ff000a")
        self.assertIsNone(validators.parse_color("rgb(300,0,0)"))

    def test_jwt(self) -> None:
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "signature"
        )
        data = validators.parse_jwt(token)
        self.assertIsNotNone(data)
        self.assertEqual(data["payload"]["sub"], "1234567890")
        self.assertIsNone(validators.parse_jwt("not.a.jwt"))

    def test_commands(self) -> None:
        self.assertIsNotNone(validators.parse_command("git status --short"))
        self.assertIsNotNone(validators.parse_command("$ sudo pacman -Syu"))
        self.assertIsNotNone(validators.parse_command("rg TODO src"))
        self.assertIsNone(validators.parse_command("This is a normal sentence."))
        self.assertIsNone(validators.parse_command("git status\nrm -rf /"))


if __name__ == "__main__":
    unittest.main()
