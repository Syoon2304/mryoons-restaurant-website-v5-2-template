from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_live_site as live  # noqa: E402


PUBLIC_IP = "93.184.216.34"


def resolved(url: str) -> live.ResolvedTarget:
    _parsed, hostname, port = live.parse_https_url(url)
    return live.ResolvedTarget(url, hostname, port, (PUBLIC_IP,))


class CanonicalPageTests(unittest.TestCase):
    def test_canonical_relative_pages_stay_on_origin(self) -> None:
        base = "https://restaurant.test/"
        self.assertEqual(live.canonical_page_url(base, "index.html"), base)
        self.assertEqual(
            live.canonical_page_url(base, "locations/downtown.html"),
            "https://restaurant.test/locations/downtown.html",
        )

    def test_unsafe_or_noncanonical_page_paths_are_rejected(self) -> None:
        paths = [
            "//attacker.test/page.html",
            "https://attacker.test/page.html",
            "/absolute.html",
            "../outside.html",
            "nested/../outside.html",
            "nested//page.html",
            "page.html?next=https://attacker.test",
            "page.html#fragment",
            "nested\\page.html",
            "%2e%2e/private.html",
            "page name.html",
        ]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(live.UnsafeUrlError):
                live.canonical_page_url("https://restaurant.test/", path)

    def test_canonical_public_route_can_differ_from_file_path(self) -> None:
        base = "https://restaurant.test/"
        self.assertEqual(live.canonical_route_url(base, "/"), base)
        self.assertEqual(
            live.canonical_route_url(base, "/private-events/"),
            "https://restaurant.test/private-events/",
        )

    def test_unsafe_public_routes_are_rejected(self) -> None:
        for path in ("menu.html", "//attacker.test/x", "/../private", "/page?x=1", "/page#x", "/page name"):
            with self.subTest(path=path), self.assertRaises(live.UnsafeUrlError):
                live.canonical_route_url("https://restaurant.test/", path)


class AddressPolicyTests(unittest.TestCase):
    def test_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(live.UnsafeUrlError, "credentials"):
            live.parse_https_url("https://user:secret@restaurant.test/menu.html")

    def test_non_ascii_url_must_be_percent_encoded(self) -> None:
        with self.assertRaisesRegex(live.UnsafeUrlError, "ASCII"):
            live.parse_https_url("https://restaurant.test/ménu.html")

    def test_non_public_literal_addresses_are_rejected(self) -> None:
        addresses = [
            "10.0.0.1",
            "127.0.0.1",
            "169.254.1.1",
            "240.0.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "::",
            "ff02::1",
        ]
        for address in addresses:
            with self.subTest(address=address), self.assertRaises(live.UnsafeUrlError):
                live.resolve_public_ips(address)

    @patch.object(live.socket, "getaddrinfo")
    def test_any_private_dns_answer_rejects_the_host(self, getaddrinfo: Mock) -> None:
        getaddrinfo.return_value = [
            (live.socket.AF_INET, live.socket.SOCK_STREAM, live.socket.IPPROTO_TCP, "", (PUBLIC_IP, 443)),
            (live.socket.AF_INET, live.socket.SOCK_STREAM, live.socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(live.UnsafeUrlError, "non-public"):
            live.resolve_public_ips("restaurant.test")


class MediaPolicyTests(unittest.TestCase):
    def test_provider_kinds_enforce_their_known_hosts(self) -> None:
        with patch.object(live, "resolve_public_ips", return_value=(PUBLIC_IP,)):
            youtube = live.external_media_policy("youtube", ())
            self.assertEqual(youtube("https://www.youtube-nocookie.com/embed/123").hostname, "www.youtube-nocookie.com")
            with self.assertRaisesRegex(live.UnsafeUrlError, "not approved"):
                youtube("https://media.attacker.test/embed/123")

    def test_direct_media_requires_an_explicit_host_allowlist(self) -> None:
        with patch.object(live, "resolve_public_ips", return_value=(PUBLIC_IP,)):
            blocked = live.external_media_policy("direct-video", ())
            with self.assertRaisesRegex(live.UnsafeUrlError, "not approved"):
                blocked("https://media.restaurant.test/hero.mp4")
            allowed = live.external_media_policy(
                "direct-video",
                live.normalize_approved_hosts(["media.restaurant.test"]),
            )
            self.assertEqual(
                allowed("https://media.restaurant.test/hero.mp4").hostname,
                "media.restaurant.test",
            )


class RedirectAndBodyTests(unittest.TestCase):
    def test_every_redirect_target_is_revalidated(self) -> None:
        checked: list[str] = []

        def policy(url: str) -> live.ResolvedTarget:
            checked.append(url)
            return resolved(url)

        responses = [
            (302, b"", {"location": "/menu.html"}),
            (200, b"<html></html>", {}),
        ]
        with patch.object(live, "request_once", side_effect=responses) as request_once:
            status, body, _headers, _elapsed = live.fetch(
                "https://restaurant.test/",
                1,
                url_policy=policy,
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<html></html>")
        self.assertEqual(
            checked,
            ["https://restaurant.test/", "https://restaurant.test/menu.html"],
        )
        self.assertEqual(request_once.call_count, 2)

    def test_same_origin_policy_blocks_cross_origin_redirect_before_connect(self) -> None:
        policy = live.same_origin_policy("https://restaurant.test/")
        with patch.object(live, "resolve_public_ips", return_value=(PUBLIC_IP,)), patch.object(
            live,
            "request_once",
            return_value=(302, b"", {"location": "https://attacker.test/internal"}),
        ) as request_once:
            with self.assertRaisesRegex(live.UnsafeUrlError, "left"):
                live.fetch("https://restaurant.test/", 1, url_policy=policy)
        self.assertEqual(request_once.call_count, 1)

    def test_media_redirect_must_remain_on_an_approved_host(self) -> None:
        policy = live.external_media_policy("direct-video", ("media.restaurant.test",))
        with patch.object(live, "resolve_public_ips", return_value=(PUBLIC_IP,)), patch.object(
            live,
            "request_once",
            return_value=(302, b"", {"location": "https://attacker.test/hero.mp4"}),
        ) as request_once:
            with self.assertRaisesRegex(live.UnsafeUrlError, "not approved"):
                live.fetch("https://media.restaurant.test/hero.mp4", 1, url_policy=policy)
        self.assertEqual(request_once.call_count, 1)

    def test_redirect_limit_is_enforced(self) -> None:
        with patch.object(
            live,
            "request_once",
            return_value=(302, b"", {"location": "/loop"}),
        ) as request_once:
            with self.assertRaisesRegex(live.LiveCheckError, "2-redirect"):
                live.fetch(
                    "https://restaurant.test/",
                    1,
                    url_policy=resolved,
                    max_redirects=2,
                )
        self.assertEqual(request_once.call_count, 3)

    def test_response_body_is_capped_even_without_content_length(self) -> None:
        response = Mock()
        response.getheader.return_value = None
        response.read.side_effect = lambda count: b"x" * count
        with self.assertRaisesRegex(live.LiveCheckError, "body limit"):
            live.read_capped_body(response, 32)
        response.read.assert_called_once_with(33)

    def test_declared_oversized_response_is_rejected_before_read(self) -> None:
        response = Mock()
        response.getheader.return_value = "33"
        with self.assertRaisesRegex(live.LiveCheckError, "body limit"):
            live.read_capped_body(response, 32)
        response.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
