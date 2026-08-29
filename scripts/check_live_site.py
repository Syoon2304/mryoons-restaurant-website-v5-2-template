#!/usr/bin/env python3
"""Run a bounded, SSRF-resistant live-site health check from GitHub Actions."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


MAX_RESPONSE_BYTES = 512 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ALLOWED_MEDIA_KINDS = {
    "direct-video",
    "cloudflare-stream",
    "youtube",
    "vimeo",
    "large-download",
}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "www.youtube-nocookie.com"}
VIMEO_HOSTS = {"player.vimeo.com"}
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


class LiveCheckError(Exception):
    """A safe-to-report live-check failure."""


class UnsafeUrlError(LiveCheckError):
    """A URL or resolved address failed the outbound-request security policy."""


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass
class Check:
    target: str
    passed: bool
    status: int | None
    message: str
    elapsed_ms: int
    required: bool = True


@dataclass
class LiveReport:
    base_url: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed or not c.required for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "base_url": self.base_url,
            "checks": [c.__dict__ for c in self.checks],
        }

    def markdown(self) -> str:
        lines = [f"# Live website health check: {'PASS' if self.passed else 'FAIL'}", ""]
        for c in self.checks:
            mark = "PASS" if c.passed else ("WARN" if not c.required else "FAIL")
            status = f" HTTP {c.status}" if c.status is not None else ""
            lines.append(f"- **{mark}** `{c.target}`{status} - {c.message} ({c.elapsed_ms} ms)")
        return "\n".join(lines) + "\n"


def normalize_hostname(value: str) -> str:
    try:
        hostname = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UnsafeUrlError("URL hostname is invalid") from exc
    if not hostname or len(hostname) > 253:
        raise UnsafeUrlError("URL hostname is invalid")
    return hostname


def parse_https_url(url: str) -> tuple[Any, str, int]:
    if not isinstance(url, str) or not url or "\\" in url or any(ord(char) < 32 for char in url):
        raise UnsafeUrlError("URL is malformed")
    try:
        url.encode("ascii")
    except UnicodeEncodeError as exc:
        raise UnsafeUrlError("URL must use ASCII or percent-encoded characters") from exc
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("URL is malformed") from exc
    if parsed.scheme.lower() != "https" or not parsed.netloc or not parsed.hostname:
        raise UnsafeUrlError("URL must be a complete HTTPS URL")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise UnsafeUrlError("URL credentials are not allowed")
    if parsed.fragment:
        raise UnsafeUrlError("URL fragments are not allowed")
    if port not in (None, 443):
        raise UnsafeUrlError("only the standard HTTPS port is allowed")
    return parsed, normalize_hostname(parsed.hostname), 443


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return is_public_address(address.ipv4_mapped)
    blocked = (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )
    return address.is_global and not blocked


def resolve_public_ips(hostname: str, port: int = 443) -> tuple[str, ...]:
    if "%" in hostname:
        raise UnsafeUrlError("scoped IP addresses are not allowed")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_address(literal):
            raise UnsafeUrlError("URL resolves to a non-public IP address")
        return (literal.compressed,)

    try:
        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise LiveCheckError(f"hostname could not be resolved: {exc}") from exc
    if not answers:
        raise LiveCheckError("hostname did not resolve to an address")

    addresses: list[str] = []
    for answer in answers:
        raw = answer[4][0]
        if "%" in raw:
            raise UnsafeUrlError("scoped IP addresses are not allowed")
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise UnsafeUrlError("DNS returned an invalid IP address") from exc
        if not is_public_address(address):
            raise UnsafeUrlError("URL resolves to a non-public IP address")
        normalized = address.compressed
        if normalized not in addresses:
            addresses.append(normalized)
    return tuple(addresses)


def validate_public_url(url: str) -> ResolvedTarget:
    _parsed, hostname, port = parse_https_url(url)
    return ResolvedTarget(url, hostname, port, resolve_public_ips(hostname, port))


def url_origin(url: str) -> tuple[str, str, int]:
    parsed, hostname, port = parse_https_url(url)
    return parsed.scheme.lower(), hostname, port


def same_origin_policy(base_url: str) -> Callable[[str], ResolvedTarget]:
    expected_origin = url_origin(base_url)

    def validate(url: str) -> ResolvedTarget:
        parsed, hostname, port = parse_https_url(url)
        if (parsed.scheme.lower(), hostname, port) != expected_origin:
            raise UnsafeUrlError("redirect left the configured website origin")
        return ResolvedTarget(url, hostname, port, resolve_public_ips(hostname, port))

    return validate


def normalize_approved_hosts(values: Iterable[str]) -> tuple[str, ...]:
    approved: list[str] = []
    for value in values:
        for raw in value.split(","):
            candidate = raw.strip().lower().rstrip(".")
            if not candidate:
                continue
            wildcard = candidate.startswith("*.")
            hostname = candidate[2:] if wildcard else candidate
            try:
                hostname = hostname.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError(f"invalid approved media host: {raw}") from exc
            if not HOSTNAME_RE.fullmatch(hostname):
                raise ValueError(f"invalid approved media host: {raw}")
            normalized = f"*.{hostname}" if wildcard else hostname
            if normalized not in approved:
                approved.append(normalized)
    return tuple(approved)


def host_matches(hostname: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
        elif hostname == pattern:
            return True
    return False


def media_host_allowed(kind: str, hostname: str, approved_hosts: Iterable[str]) -> bool:
    if kind == "cloudflare-stream":
        return hostname.endswith(".cloudflarestream.com") or hostname.endswith(".videodelivery.net")
    if kind == "youtube":
        return hostname in YOUTUBE_HOSTS
    if kind == "vimeo":
        return hostname in VIMEO_HOSTS
    if kind in {"direct-video", "large-download"}:
        return host_matches(hostname, approved_hosts)
    return False


def external_media_policy(kind: str, approved_hosts: Iterable[str]) -> Callable[[str], ResolvedTarget]:
    if kind not in ALLOWED_MEDIA_KINDS:
        raise UnsafeUrlError(f"external media kind is not approved: {kind or '<missing>'}")
    allowed = tuple(approved_hosts)

    def validate(url: str) -> ResolvedTarget:
        _parsed, hostname, port = parse_https_url(url)
        if not media_host_allowed(kind, hostname, allowed):
            raise UnsafeUrlError(f"host is not approved for {kind}: {hostname}")
        return ResolvedTarget(url, hostname, port, resolve_public_ips(hostname, port))

    return validate


def canonical_page_url(base_url: str, path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or "%" in path:
        raise UnsafeUrlError("manifest page path is not canonical")
    try:
        path.encode("ascii")
    except UnicodeEncodeError as exc:
        raise UnsafeUrlError("manifest page path must use ASCII URL-safe characters") from exc
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or path.startswith("/"):
        raise UnsafeUrlError("manifest page path must be a relative file path")
    if unquote(path) != path:
        raise UnsafeUrlError("encoded manifest page paths are not allowed")
    pure = PurePosixPath(path)
    if pure.as_posix() != path or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafeUrlError("manifest page path is not canonical")
    if any(not re.fullmatch(r"[A-Za-z0-9._~-]+", part) for part in pure.parts):
        raise UnsafeUrlError("manifest page path must use URL-safe characters")
    url_path = "" if path == "index.html" else path
    target = urljoin(base_url, url_path)
    if url_origin(target) != url_origin(base_url):
        raise UnsafeUrlError("manifest page path left the configured website origin")
    return target


def canonical_route_url(base_url: str, path: str) -> str:
    """Build a same-origin URL from a V5.2 canonical_url_path."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or "%" in path:
        raise UnsafeUrlError("manifest canonical URL path is not a canonical root-relative path")
    try:
        path.encode("ascii")
    except UnicodeEncodeError as exc:
        raise UnsafeUrlError("manifest canonical URL path must use ASCII URL-safe characters") from exc
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise UnsafeUrlError("manifest canonical URL path may not contain a scheme, host, query, or fragment")
    if path != "/":
        trailing_slash = path.endswith("/")
        pure = PurePosixPath(path.lstrip("/"))
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise UnsafeUrlError("manifest canonical URL path is not canonical")
        if any(not re.fullmatch(r"[A-Za-z0-9._~-]+", part) for part in pure.parts):
            raise UnsafeUrlError("manifest canonical URL path must use URL-safe characters")
        expected = "/" + pure.as_posix() + ("/" if trailing_slash else "")
        if expected != path:
            raise UnsafeUrlError("manifest canonical URL path is not canonical")
    target = urljoin(base_url, path)
    if url_origin(target) != url_origin(base_url):
        raise UnsafeUrlError("manifest canonical URL path left the configured website origin")
    return target


def safe_report_url(url: str) -> str:
    try:
        parsed, hostname, port = parse_https_url(url)
    except LiveCheckError:
        return "invalid URL"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host if port == 443 else f"{display_host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection pinned to an address that passed the public-IP policy."""

    def __init__(self, hostname: str, port: int, address: str, timeout: float, context: ssl.SSLContext):
        super().__init__(hostname, port=port, timeout=timeout, context=context)
        self.resolved_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.resolved_address, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def read_capped_body(response: Any, max_body_bytes: int) -> bytes:
    raw_length = response.getheader("Content-Length")
    if raw_length is not None:
        value = raw_length.strip()
        if not value.isascii() or not value.isdecimal():
            raise LiveCheckError("response had an invalid Content-Length")
        if int(value) > max_body_bytes:
            raise LiveCheckError(f"response exceeded the {max_body_bytes}-byte body limit")
    body = response.read(max_body_bytes + 1)
    if len(body) > max_body_bytes:
        raise LiveCheckError(f"response exceeded the {max_body_bytes}-byte body limit")
    return body


def request_once(
    target: ResolvedTarget,
    timeout: float,
    method: str,
    headers: dict[str, str],
    max_body_bytes: int,
) -> tuple[int, bytes, dict[str, str]]:
    parsed = urlsplit(target.url)
    request_path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    context = ssl.create_default_context()
    last_error: Exception | None = None
    for address in target.addresses:
        connection = PinnedHTTPSConnection(target.hostname, target.port, address, timeout, context)
        try:
            connection.request(method, request_path, headers=headers)
            response = connection.getresponse()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in REDIRECT_STATUSES or method == "HEAD":
                body = b""
            else:
                body = read_capped_body(response, max_body_bytes)
            return response.status, body, response_headers
        except LiveCheckError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            connection.close()
    raise LiveCheckError(str(last_error or "connection failed"))


def fetch(
    url: str,
    timeout: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    *,
    url_policy: Callable[[str], ResolvedTarget] = validate_public_url,
    max_redirects: int = MAX_REDIRECTS,
    max_body_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, bytes, dict[str, str], int]:
    if method not in {"GET", "HEAD"}:
        raise ValueError("live checker supports GET and HEAD only")
    request_headers = {
        "User-Agent": "AI-Website-Course-Health-Check/2.0",
        "Accept": "text/html,application/json,*/*",
        "Accept-Encoding": "identity",
    }
    if headers:
        request_headers.update(headers)
    current = url
    start = time.monotonic()
    for redirect_count in range(max_redirects + 1):
        target = url_policy(current)
        status, body, response_headers = request_once(
            target,
            timeout,
            method,
            request_headers,
            max_body_bytes,
        )
        if status not in REDIRECT_STATUSES:
            elapsed = int((time.monotonic() - start) * 1000)
            return status, body, response_headers, elapsed
        location = response_headers.get("location")
        if not location:
            raise LiveCheckError("redirect response did not include a Location header")
        if redirect_count >= max_redirects:
            raise LiveCheckError(f"response exceeded the {max_redirects}-redirect limit")
        current = urljoin(current, location)
    raise LiveCheckError(f"response exceeded the {max_redirects}-redirect limit")


def run_url(
    report: LiveReport,
    url: str,
    timeout: float,
    required: bool = True,
    expect_json: bool = False,
    method: str = "GET",
    url_policy: Callable[[str], ResolvedTarget] = validate_public_url,
) -> bytes | None:
    start = time.monotonic()
    display_url = safe_report_url(url)
    try:
        status, body, _headers, elapsed = fetch(
            url,
            timeout,
            method=method,
            url_policy=url_policy,
        )
        ok = 200 <= status < 300
        message = "reachable" if ok else "endpoint returned a non-success status"
        if expect_json and ok:
            try:
                json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                ok = False
                message = "response was not valid JSON"
        report.checks.append(Check(display_url, ok, status, message, elapsed, required))
        return body if ok else None
    except UnsafeUrlError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        report.checks.append(Check(display_url, False, None, str(exc), elapsed, True))
    except LiveCheckError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        report.checks.append(Check(display_url, False, None, str(exc), elapsed, required))
    return None


def normalize_base(url: str) -> str:
    try:
        parsed, hostname, port = parse_https_url(url)
    except UnsafeUrlError as exc:
        raise ValueError(str(exc)) from exc
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError("SITE_URL must be an HTTPS origin without a path or query")
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host if port == 443 else f"{display_host}:{port}"
    return urlunsplit(("https", netloc, "/", "", ""))


def add_manifest_failure(report: LiveReport, target: str, message: str) -> None:
    report.checks.append(Check(target, False, None, message, 0, True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, default=Path("public/site-manifest.json"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--approved-media-host", action="append", default=[])
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    args = parser.parse_args(argv)

    try:
        base = normalize_base(args.base_url)
        if args.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        env_hosts = os.environ.get("LIVE_CHECK_APPROVED_MEDIA_HOSTS", "")
        approved_media_hosts = normalize_approved_hosts([*args.approved_media_host, env_hosts])
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("site manifest must be a JSON object")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = LiveReport(base)
    manifest_domain = manifest.get("public_domain")
    try:
        manifest_base = normalize_base(manifest_domain) if isinstance(manifest_domain, str) else ""
    except ValueError as exc:
        add_manifest_failure(report, "site-manifest.json public_domain", str(exc))
        manifest_base = ""
    if manifest_base and manifest_base != base:
        add_manifest_failure(
            report,
            "site-manifest.json public_domain",
            "configured SITE_URL does not match the manifest public_domain origin",
        )
    origin_policy = same_origin_policy(base)
    pages = manifest.get("pages", [])
    if not isinstance(pages, list):
        add_manifest_failure(report, "site-manifest.json pages", "pages must be a list")
        pages = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("canonical_url_path"), str):
            add_manifest_failure(report, f"site-manifest.json pages[{index}]", "page entry is invalid")
            continue
        try:
            page_url = canonical_route_url(base, page["canonical_url_path"])
        except UnsafeUrlError as exc:
            add_manifest_failure(report, f"site-manifest.json pages[{index}]", str(exc))
            continue
        body = run_url(report, page_url, args.timeout, required=True, url_policy=origin_policy)
        if body is not None and b"<html" not in body.lower():
            report.checks.append(Check(safe_report_url(page_url), False, 200, "response did not look like HTML", 0, True))
    run_url(report, urljoin(base, "robots.txt"), args.timeout, required=True, url_policy=origin_policy)
    run_url(report, urljoin(base, "sitemap.xml"), args.timeout, required=True, url_policy=origin_policy)

    forms_value = manifest.get("forms", [])
    forms = [form for form in forms_value if isinstance(form, dict) and form.get("enabled", True)] if isinstance(forms_value, list) else []
    if forms:
        health_url = urljoin(base, "api/health")
        body = run_url(report, health_url, args.timeout, required=True, expect_json=True, url_policy=origin_policy)
        if body:
            try:
                health = json.loads(body.decode("utf-8"))
                configured = bool(health.get("formDeliveryConfigured"))
                turnstile_required = bool(health.get("turnstileRequired"))
                turnstile_configured = bool(health.get("turnstileConfigured"))
                if not configured:
                    report.checks.append(Check(safe_report_url(health_url), False, 200, "form delivery is not configured", 0, True))
                if turnstile_required and not turnstile_configured:
                    report.checks.append(Check(safe_report_url(health_url), False, 200, "Turnstile is required but its secret, hostname, or action contract is missing", 0, True))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

    media_value = manifest.get("external_media", [])
    if not isinstance(media_value, list):
        add_manifest_failure(report, "site-manifest.json external_media", "external_media must be a list")
        media_value = []
    for index, media in enumerate(media_value):
        target = f"site-manifest.json external_media[{index}]"
        if not isinstance(media, dict):
            add_manifest_failure(report, target, "external media entry is invalid")
            continue
        kind = media.get("kind")
        media_url = media.get("url")
        if not isinstance(kind, str) or not isinstance(media_url, str):
            add_manifest_failure(report, target, "external media kind and URL are required")
            continue
        if media.get("owner_approved") is not True:
            add_manifest_failure(report, target, "external media is missing explicit owner approval")
            continue
        try:
            policy = external_media_policy(kind, approved_media_hosts)
        except UnsafeUrlError as exc:
            add_manifest_failure(report, target, str(exc))
            continue
        # YouTube and Vimeo commonly reject automated HEAD probes. Their host and redirect
        # security policy remains mandatory, while reachability is advisory.
        required = kind not in {"youtube", "vimeo"}
        run_url(
            report,
            media_url,
            args.timeout,
            required=required,
            method="HEAD",
            url_policy=policy,
        )

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    if args.report_md:
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(report.markdown(), encoding="utf-8")
    print(report.markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
