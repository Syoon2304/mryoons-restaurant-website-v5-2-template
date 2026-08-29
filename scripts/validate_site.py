#!/usr/bin/env python3
"""Validate a production static website before it is committed or deployed.

The validator intentionally uses only the Python standard library so the same
checks run locally, in GitHub Actions, and inside a Codex environment without
installing dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_package_policy import (  # noqa: E402
    SourceEntry,
    load_policy,
    normalize_relative_name,
    validate_source_tree,
)

TEXT_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".xml", ".txt",
    ".svg", ".webmanifest", ".ics",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".iso", ".dmg"}

PLACEHOLDER_PATTERNS = [
    ("placeholder.lorem", re.compile(r"\blorem\s+ipsum\b", re.I)),
    ("placeholder.todo", re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.I)),
    ("placeholder.example", re.compile(r"\bexample\.(?:com|org|net)\b", re.I)),
    ("placeholder.phone", re.compile(r"(?:\+?1[\s.-]?)?\(?555\)?[\s.-]?\d{3}[\s.-]?\d{4}")),
    ("placeholder.replace", re.compile(r"\b(?:replace\s+me|insert\s+here|your\s+restaurant|restaurant\s+name\s+here)\b", re.I)),
    ("placeholder.brackets", re.compile(r"\[(?:restaurant|business|domain|phone|address|city|owner|insert)[^\]]*\]", re.I)),
    ("placeholder.business_name", re.compile(r"\[your business\]", re.I)),
    ("placeholder.truth_status", re.compile(r"\b(?:MISSING|CONFLICT|STALE|UNTESTED|DO[_ -]?NOT[_ -]?PUBLISH)\b", re.I)),
    ("placeholder.draft_status", re.compile(r"\b(?:status|truth[_ -]?status)\s*[:=]\s*['\"]?(?:draft|missing|conflict|stale|untested|do[_ -]?not[_ -]?publish)\b", re.I)),
]

PRIVATE_CONTENT_PATTERNS = [
    ("privacy.course_controller", re.compile(r"CHATGPT PROJECT INSTRUCTIONS|AI WEBSITE SYSTEM", re.I)),
    ("privacy.business_record", re.compile(r"STRUCTURED BUSINESS DATA|WEBSITE SOURCE OF TRUTH", re.I)),
    ("privacy.checkpoint", re.compile(r"EXISTING WEBSITE IMPORT APPROVAL|FINAL PREVIEW APPROVAL|DESIGN LOCK", re.I)),
]

SECRET_PATTERNS = [
    ("secret.private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("secret.openai", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("secret.github", re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b")),
    ("secret.aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret.stripe", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("secret.slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("secret.cloudflare", re.compile(r"(?i)\b(?:cloudflare[_-]?(?:api[_-]?)?token|cf[_-]?api[_-]?token)\s*[:=]\s*['\"][^'\"]{20,}['\"]")),
    ("secret.generic_assignment", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
]

ALLOWED_EXTERNAL_EMBED_HOSTS = {
    "www.youtube-nocookie.com", "www.youtube.com", "youtube.com",
    "player.vimeo.com", "iframe.videodelivery.net",
}

DISALLOWED_PUBLIC_MEDIA_HOSTS = {
    "drive.google.com", "docs.google.com", "photos.google.com",
    "dropbox.com", "www.dropbox.com",
}

def is_allowed_embed_host(host: str) -> bool:
    value = host.lower().rstrip(".")
    if value in ALLOWED_EXTERNAL_EMBED_HOSTS:
        return True
    return value.endswith(".cloudflarestream.com") or value.endswith(".videodelivery.net")

REQUIRED_MANIFEST_KEYS = {
    "schema_version", "site_name", "business_type", "public_domain", "package_version",
    "build_date", "stage", "workflow_version", "system_version", "business_assets_version",
    "repository_package_spec", "design_lock_reference", "final_approval_reference",
    "primary_action", "pages", "legacy_routes", "icons", "external_media", "public_documents", "forms",
}

VERSION_MATCH_KEYS = {
    "package_version", "build_date", "workflow_version", "system_version",
    "business_assets_version", "repository_package_spec",
}

PLACEHOLDER_VALUES = {"", "todo", "tbd", "missing", "draft", "not-created", "not-populated", "replace-me"}


@dataclass
class Finding:
    level: str
    code: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {"level": self.level, "code": self.code, "message": self.message}
        if self.path:
            data["path"] = self.path
        return data


@dataclass
class ValidationReport:
    root: str
    mode: str
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, level: str, code: str, message: str, path: str | Path | None = None) -> None:
        self.findings.append(Finding(level, code, message, str(path) if path else None))

    def error(self, code: str, message: str, path: str | Path | None = None) -> None:
        self.add("error", code, message, path)

    def warning(self, code: str, message: str, path: str | Path | None = None) -> None:
        self.add("warning", code, message, path)

    def info(self, code: str, message: str, path: str | Path | None = None) -> None:
        self.add("info", code, message, path)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "root": self.root,
            "mode": self.mode,
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "findings": len(self.findings),
            },
            "stats": self.stats,
            "findings": [f.as_dict() for f in self.findings],
        }

    def to_markdown(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"# Website validation: {status}",
            "",
            f"- Mode: `{self.mode}`",
            f"- Errors: **{len(self.errors)}**",
            f"- Warnings: **{len(self.warnings)}**",
        ]
        for key, value in sorted(self.stats.items()):
            lines.append(f"- {key.replace('_', ' ').title()}: `{value}`")
        lines.append("")
        if self.findings:
            lines.append("## Findings")
            lines.append("")
            for f in self.findings:
                icon = {"error": "ERROR", "warning": "WARNING", "info": "INFO"}[f.level]
                location = f" (`{f.path}`)" if f.path else ""
                lines.append(f"- **{icon} - {f.code}**{location}: {f.message}")
        else:
            lines.append("No findings.")
        return "\n".join(lines) + "\n"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None
        self.title_chunks: list[str] = []
        self.in_title = False
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.forms: list[dict[str, str]] = []
        self.inputs: list[dict[str, str]] = []
        self.labels_for: set[str] = set()
        self.ids: list[str] = []
        self.h1_count = 0
        self.jsonld_chunks: list[str] = []
        self.in_jsonld = False
        self._jsonld_buffer: list[str] = []
        self.canonical: str | None = None
        self.has_charset = False
        self.turnstile_widgets: list[dict[str, str]] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "html":
            self.html_lang = a.get("lang") or None
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            self.meta.append(a)
            if "charset" in a:
                self.has_charset = True
        elif tag == "link":
            self.links.append({"tag": tag, **a})
            rel = {part.strip().lower() for part in a.get("rel", "").split()}
            if "canonical" in rel:
                self.canonical = a.get("href") or None
        elif tag in {"a", "area"}:
            self.links.append({"tag": tag, **a})
        elif tag in {"img", "source", "video", "audio", "iframe", "script"}:
            self.images.append({"tag": tag, **a})
            if tag == "script" and a.get("type", "").lower() == "application/ld+json":
                self.in_jsonld = True
                self._jsonld_buffer = []
        elif tag == "form":
            self.forms.append(a)
        elif tag in {"input", "select", "textarea"}:
            self.inputs.append({"tag": tag, **a})
        elif tag == "label" and a.get("for"):
            self.labels_for.add(a["for"])
        if tag == "h1":
            self.h1_count += 1
        if "cf-turnstile" in a.get("class", "").split():
            self.turnstile_widgets.append(a)
        if a.get("id"):
            self.ids.append(a["id"])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "script" and self.in_jsonld:
            self.jsonld_chunks.append("".join(self._jsonld_buffer).strip())
            self._jsonld_buffer = []
            self.in_jsonld = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_chunks.append(data)
        if self.in_jsonld:
            self._jsonld_buffer.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_chunks).split())

    def meta_value(self, *, name: str | None = None, prop: str | None = None) -> str | None:
        for item in self.meta:
            if name and item.get("name", "").lower() == name.lower():
                return item.get("content") or None
            if prop and item.get("property", "").lower() == prop.lower():
                return item.get("content") or None
        return None


@dataclass
class ParsedPage:
    relpath: str
    parser: PageParser
    text: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_page_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("Site path must be a non-empty string.")
    try:
        parsed = urlparse(path)
    except ValueError as exc:
        raise ValueError("Site path is malformed.") from exc
    if parsed.scheme or parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Site path must be a relative path without a scheme, host, query, or fragment.")
    if unquote(path) != path:
        raise ValueError("Site paths may not contain percent-encoded characters.")
    try:
        return normalize_relative_name(path)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def normalize_url_path(path: str) -> str:
    """Return a canonical root-relative public URL path without decoding it."""
    if not isinstance(path, str) or not path.startswith("/") or not path.isascii():
        raise ValueError("URL path must be an ASCII root-relative path beginning with '/'.")
    if path.startswith("//") or "\\" in path or any(ord(char) < 33 for char in path):
        raise ValueError("URL path contains an unsafe separator or character.")
    try:
        parsed = urlparse(path)
    except ValueError as exc:
        raise ValueError("URL path is malformed.") from exc
    if parsed.scheme or parsed.netloc or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("URL path may not contain a scheme, host, parameters, query, or fragment.")
    if unquote(path) != path:
        raise ValueError("URL path may not contain percent-encoded characters.")
    if path == "/":
        return path
    trailing_slash = path.endswith("/")
    pure = PurePosixPath(path.lstrip("/"))
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("URL path may not contain empty, dot, or parent segments.")
    for part in pure.parts:
        if re.fullmatch(r"[A-Za-z0-9._~-]+", part) is None:
            raise ValueError("URL path segments must use URL-safe ASCII characters.")
    normalized = "/" + pure.as_posix()
    if trailing_slash:
        normalized += "/"
    if normalized != path:
        raise ValueError("URL path is not canonical.")
    return normalized


def _json_type_matches(value: Any, expected: str) -> bool:
    mapping = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
    }
    return expected in mapping and mapping[expected](value)


def _canonical_https_parts(value: str) -> tuple[Any, str] | None:
    if not value.isascii() or "\\" in value or any(ord(char) < 33 for char in value):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname or ""
    hostname_pattern = re.compile(
        r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    )
    if (
        parsed.scheme != "https" or not hostname_pattern.fullmatch(hostname)
        or hostname != hostname.casefold() or parsed.username or parsed.password
        or port is not None or parsed.netloc != hostname
    ):
        return None
    return parsed, hostname


def _format_valid(value: str, format_name: str) -> bool:
    if format_name == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None
        except ValueError:
            return False
    if format_name == "https-origin":
        result = _canonical_https_parts(value)
        if result is None:
            return False
        parsed, _hostname = result
        return not parsed.path and not parsed.params and not parsed.query and not parsed.fragment
    if format_name == "relative-site-path":
        try:
            normalize_page_path(value)
            return True
        except ValueError:
            return False
    if format_name == "site-url-path":
        try:
            normalize_url_path(value)
            return True
        except ValueError:
            return False
    if format_name == "https-url":
        result = _canonical_https_parts(value)
        if result is None:
            return False
        parsed, _hostname = result
        return not parsed.fragment
    if format_name == "action-url":
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        if parsed.scheme == "https":
            return _canonical_https_parts(value) is not None
        if parsed.scheme in {"tel", "mailto"}:
            return bool(parsed.path and not parsed.netloc and not parsed.fragment)
        try:
            normalize_page_path(value.lstrip("/"))
            return value.startswith("/") and "//" not in value
        except ValueError:
            return False
    return True


def validate_json_schema(value: Any, schema: dict[str, Any], location: str = "$") -> list[str]:
    """Validate the deliberately small schema vocabulary used by this repository."""
    errors: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str) and not _json_type_matches(value, expected):
        return [f"{location}: expected {expected}"]
    if isinstance(expected, list) and not any(
        isinstance(item, str) and _json_type_matches(value, item) for item in expected
    ):
        return [f"{location}: expected one of {expected!r}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: must equal {schema['const']!r}")
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        errors.append(f"{location}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{location}: string is too short")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{location}: string is too long")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            errors.append(f"{location}: does not match the required pattern")
        format_name = schema.get("format")
        if isinstance(format_name, str) and not _format_valid(value, format_name):
            errors.append(f"{location}: is not a valid {format_name}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{location}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            if schema.get("additionalProperties") is False:
                for key in sorted(set(value) - set(properties)):
                    errors.append(f"{location}: unexpected property {key!r}")
            for key, child in properties.items():
                if key in value and isinstance(child, dict):
                    errors.extend(validate_json_schema(value[key], child, f"{location}.{key}"))
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{location}: needs at least {schema['minItems']} item(s)")
        if schema.get("uniqueItems") is True:
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{location}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, item_schema, f"{location}[{index}]"))
    return errors


def _candidate_paths(root: Path, current_rel: str, raw_url: str) -> list[Path]:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return []
    path = unquote(parsed.path)
    if path.startswith("/"):
        rel = PurePosixPath(path.lstrip("/"))
    else:
        rel = PurePosixPath(current_rel).parent / path
    clean = PurePosixPath(os.path.normpath(str(rel)).replace("\\", "/"))
    if str(clean).startswith("../") or str(clean) == "..":
        return []
    base = root / str(clean)
    candidates = [base]
    if path.endswith("/") or not path:
        candidates.insert(0, base / "index.html")
    elif not PurePosixPath(path).suffix:
        candidates.extend([Path(str(base) + ".html"), base / "index.html"])
    return candidates


def is_external_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return bool(parsed.scheme or parsed.netloc)


def iter_urls(page: ParsedPage) -> Iterable[tuple[str, str]]:
    p = page.parser
    for item in p.links:
        href = item.get("href")
        if href:
            yield "href", href
    for item in p.images:
        for attr in ("src", "poster"):
            value = item.get(attr)
            if value:
                yield attr, value
        srcset = item.get("srcset")
        if srcset:
            for part in srcset.split(","):
                candidate = part.strip().split()[0] if part.strip() else ""
                if candidate:
                    yield "srcset", candidate


def scan_text_for_risks(text: str, report: ValidationReport, relpath: str, production: bool) -> None:
    if production:
        for code, pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(text):
                report.error(code, "Placeholder or unfinished content was found.", relpath)
        for code, pattern in PRIVATE_CONTENT_PATTERNS:
            if pattern.search(text):
                report.error(code, "Private workflow or business-record content was found in public output.", relpath)
    for code, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            report.error(code, "A secret-like value was found. Remove it and rotate the credential if it is real.", relpath)


def extract_jsonld_types(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        t = value.get("@type")
        if isinstance(t, str):
            found.add(t)
        elif isinstance(t, list):
            found.update(str(v) for v in t)
        for child in value.values():
            found.update(extract_jsonld_types(child))
    elif isinstance(value, list):
        for child in value:
            found.update(extract_jsonld_types(child))
    return found


def validate_manifest(
    root: Path,
    repo_root: Path | None,
    report: ValidationReport,
    production: bool,
) -> dict[str, Any] | None:
    path = root / "site-manifest.json"
    if not path.is_file():
        report.error("manifest.missing", "site-manifest.json is required at the website root.", "site-manifest.json")
        return None
    try:
        manifest = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.error("manifest.invalid_json", f"site-manifest.json is not valid JSON: {exc}", "site-manifest.json")
        return None
    if not isinstance(manifest, dict):
        report.error("manifest.type", "site-manifest.json must contain a JSON object.", "site-manifest.json")
        return None

    schema_path = (repo_root / "infrastructure" / "site-manifest.schema.json") if repo_root else None
    if schema_path is None or not schema_path.is_file():
        report.error("manifest.schema_missing", "The protected V5.2 manifest schema is required.", schema_path or "infrastructure/site-manifest.schema.json")
    else:
        try:
            schema = json.loads(read_text(schema_path))
        except json.JSONDecodeError as exc:
            report.error("manifest.schema_invalid", f"The protected manifest schema is invalid JSON: {exc}", schema_path)
        else:
            if not isinstance(schema, dict):
                report.error("manifest.schema_invalid", "The protected manifest schema must be a JSON object.", schema_path)
            else:
                for message in validate_json_schema(manifest, schema):
                    report.error("manifest.schema", message, "site-manifest.json")
    missing = sorted(REQUIRED_MANIFEST_KEYS - set(manifest))
    if missing:
        report.error("manifest.required", f"Missing required manifest keys: {', '.join(missing)}", "site-manifest.json")
    stage = manifest.get("stage")
    if production and stage != "production":
        report.error("manifest.stage", "Imported website packages must use stage 'production'.", "site-manifest.json")
    if manifest.get("business_type") not in {"restaurant", "food-establishment"}:
        report.error("manifest.business_type", "This starter repository expects the restaurant business module.", "site-manifest.json")
    domain = manifest.get("public_domain")
    if not isinstance(domain, str) or not _format_valid(domain, "https-origin"):
        report.error("manifest.domain", "public_domain must be a complete HTTPS URL.", "site-manifest.json")
    elif any(token in domain.lower() for token in ("example.com", "localhost", "your-domain", "replace")):
        report.error("manifest.domain_placeholder", "public_domain still contains a placeholder.", "site-manifest.json")
    build_date = manifest.get("build_date")
    if not isinstance(build_date, str):
        report.error("manifest.build_date", "build_date must be an ISO-8601 string.", "site-manifest.json")
    elif not _format_valid(build_date, "date-time"):
        report.error("manifest.build_date", "build_date must be timezone-aware ISO-8601.", "site-manifest.json")
    if production:
        for key in (
            "package_version", "workflow_version", "system_version", "business_assets_version",
            "repository_package_spec", "design_lock_reference", "final_approval_reference",
        ):
            value = manifest.get(key)
            if not isinstance(value, str) or value.strip().casefold() in PLACEHOLDER_VALUES:
                report.error("manifest.production_reference", f"{key} must contain an approved production value.", "site-manifest.json")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        report.error("manifest.pages", "pages must be a non-empty list.", "site-manifest.json")
    else:
        normalized: set[str] = set()
        url_paths: set[str] = set()
        canonical_paths: set[str] = set()
        titles: set[str] = set()
        for idx, page in enumerate(pages):
            if not isinstance(page, dict) or not isinstance(page.get("file_path"), str):
                report.error("manifest.page_entry", f"pages[{idx}] must contain a string file_path.", "site-manifest.json")
                continue
            try:
                rel = normalize_page_path(page["file_path"])
            except ValueError as exc:
                report.error("manifest.page_path", f"pages[{idx}] has an invalid file_path: {exc}", "site-manifest.json")
                continue
            if not rel.endswith((".html", ".htm")):
                report.error("manifest.page_extension", f"Manifest page must be an HTML file: {rel}", "site-manifest.json")
            if rel in normalized:
                report.error("manifest.page_duplicate", f"Manifest page file_path is duplicated: {rel}", "site-manifest.json")
            normalized.add(rel)
            for key, seen in (("url_path", url_paths), ("canonical_url_path", canonical_paths)):
                value = page.get(key)
                try:
                    route = normalize_url_path(value) if isinstance(value, str) else ""
                except ValueError as exc:
                    report.error("manifest.page_url_path", f"pages[{idx}] has an invalid {key}: {exc}", "site-manifest.json")
                    route = ""
                if route:
                    if route in seen:
                        report.error("manifest.page_url_duplicate", f"Manifest page {key} is duplicated: {route}", "site-manifest.json")
                    seen.add(route)
            if page.get("indexable") is True and page.get("canonical_url_path") != page.get("url_path"):
                report.error(
                    "manifest.page_canonical",
                    f"pages[{idx}] is indexable, so canonical_url_path must equal url_path.",
                    "site-manifest.json",
                )
            title = page.get("title")
            if isinstance(title, str):
                if title in titles:
                    report.error("manifest.title_duplicate", f"Manifest page title is duplicated: {title}", "site-manifest.json")
                titles.add(title)
            if not (root / rel).is_file():
                report.error("manifest.page_missing", f"Manifest page does not exist: {rel}", "site-manifest.json")
        if "index.html" not in normalized:
            report.error("manifest.home", "The page list must include index.html.", "site-manifest.json")
        actual_html = {path.relative_to(root).as_posix() for path in root.rglob("*.html")}
        for rel in sorted(actual_html - normalized):
            report.error("manifest.page_unlisted", f"Public HTML page is missing from the manifest: {rel}", "site-manifest.json")
    for key in ("legacy_routes", "icons", "external_media", "public_documents", "forms"):
        if not isinstance(manifest.get(key), list):
            report.error(f"manifest.{key}", f"{key} must be a list.", "site-manifest.json")
    primary = manifest.get("primary_action")
    if not isinstance(primary, dict) or not primary.get("label") or not primary.get("url"):
        report.error("manifest.primary_action", "primary_action must include label and url.", "site-manifest.json")
    return manifest


def validate_version(root: Path, manifest: dict[str, Any] | None, report: ValidationReport) -> None:
    path = root / "version.json"
    if not path.is_file():
        report.error("version.missing", "version.json is required.", "version.json")
        return
    try:
        version = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        report.error("version.invalid_json", f"version.json is not valid JSON: {exc}", "version.json")
        return
    if not isinstance(version, dict):
        report.error("version.type", "version.json must contain a JSON object.", "version.json")
        return
    allowed = VERSION_MATCH_KEYS | {"source"}
    unexpected = sorted(set(version) - allowed)
    missing = sorted(allowed - set(version))
    if unexpected:
        report.error("version.unexpected", f"Unexpected version.json keys: {', '.join(unexpected)}", "version.json")
    if missing:
        report.error("version.required", f"Missing version.json keys: {', '.join(missing)}", "version.json")
    if manifest:
        for key in sorted(VERSION_MATCH_KEYS):
            if version.get(key) != manifest.get(key):
                report.error("version.mismatch", f"version.json {key} must match site-manifest.json.", "version.json")


def parse_csp(value: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for segment in value.split(";"):
        tokens = segment.strip().split()
        if not tokens:
            continue
        directives[tokens[0].lower()] = [token.lower() for token in tokens[1:]]
    return directives


def _expected_csp_hosts(manifest: dict[str, Any] | None) -> dict[str, set[str]]:
    expected = {name: set() for name in ("script-src", "frame-src", "connect-src", "media-src", "img-src", "font-src", "style-src")}
    if not manifest:
        return expected
    forms = manifest.get("forms")
    if isinstance(forms, list) and any(isinstance(item, dict) and item.get("enabled") is True for item in forms):
        for directive in ("script-src", "frame-src", "connect-src"):
            expected[directive].add("challenges.cloudflare.com")
    media = manifest.get("external_media")
    if isinstance(media, list):
        for item in media:
            if not isinstance(item, dict):
                continue
            try:
                parsed = urlparse(str(item.get("url", "")))
            except ValueError:
                continue
            host = (parsed.hostname or "").casefold().rstrip(".")
            if not host:
                continue
            kind = item.get("kind")
            if kind == "direct-video":
                expected["media-src"].add(host)
            elif kind in {"cloudflare-stream", "youtube", "vimeo"}:
                expected["frame-src"].add(host)
    return expected


def _csp_source_host(token: str) -> str | None:
    result = _canonical_https_parts(token.rstrip("/"))
    if result is None:
        return None
    parsed, hostname = result
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return hostname


def validate_headers(
    root: Path,
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> None:
    path = root / "_headers"
    if not path.is_file():
        if production:
            report.error("headers.missing", "A _headers file with baseline security headers is required.", "_headers")
        return
    text = read_text(path)
    required = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "",
        "permissions-policy": "",
        "content-security-policy": "",
    }
    if production:
        required["strict-transport-security"] = "max-age="
    for name, expected in required.items():
        values = re.findall(rf"(?mi)^\s*{re.escape(name)}:\s*(.+)$", text)
        if not values:
            report.error("headers.required", f"Missing required security header: {name}", "_headers")
        elif len(values) != 1:
            report.error("headers.count", f"Security header must appear exactly once: {name}", "_headers")
        elif expected and expected not in values[0].casefold():
            report.error("headers.value", f"Header {name} must include {expected}.", "_headers")

    matches = re.findall(r"(?mi)^\s*content-security-policy:\s*(.+)$", text)
    if not matches:
        return
    if len(matches) != 1:
        report.error("headers.csp_count", "_headers must contain exactly one auditable Content-Security-Policy line.", "_headers")
    directives = parse_csp(matches[0])
    required_directives = {
        "default-src": "'self'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "frame-ancestors": "'none'",
        "object-src": "'none'",
        "img-src": "'self'",
        "media-src": "'self'",
        "style-src": "'self'",
        "script-src": "'self'",
        "frame-src": None,
        "connect-src": "'self'",
        "font-src": "'self'",
        "upgrade-insecure-requests": None,
    }
    for directive, expected_token in required_directives.items():
        if directive not in directives:
            report.error("headers.csp_directive", f"Content-Security-Policy is missing {directive}.", "_headers")
        elif expected_token and expected_token not in directives[directive]:
            report.error("headers.csp_value", f"Content-Security-Policy {directive} must include {expected_token}.", "_headers")
    script_tokens = directives.get("script-src", [])
    if "'unsafe-eval'" in script_tokens:
        report.error("headers.csp_unsafe_eval", "Do not allow unsafe-eval in script-src.", "_headers")
    if "data:" in script_tokens:
        report.error("headers.csp_script_data", "Do not allow data: scripts.", "_headers")

    expected_hosts = _expected_csp_hosts(manifest)
    network_directives = {"script-src", "frame-src", "connect-src", "media-src", "img-src", "font-src", "style-src"}
    for directive in sorted(network_directives):
        for token in directives.get(directive, []):
            if token in {"https:", "http:", "*"} or "*" in token:
                report.error(
                    "headers.csp_broad_source",
                    f"Content-Security-Policy {directive} must name each approved external host; broad source {token} is not allowed.",
                    "_headers",
                )
                continue
            if token.startswith(("https://", "http://")):
                host = _csp_source_host(token)
                if host is None:
                    report.error("headers.csp_external_source", f"Content-Security-Policy has a non-canonical external source: {token}", "_headers")
                elif token.startswith("http://"):
                    report.error("headers.csp_insecure_source", f"Content-Security-Policy may not use HTTP: {token}", "_headers")
                elif host not in expected_hosts.get(directive, set()):
                    report.error(
                        "headers.csp_undeclared_host",
                        f"Content-Security-Policy {directive} allows {host}, but the V5.2 manifest does not declare a feature that needs it.",
                        "_headers",
                    )
    for directive, hosts in expected_hosts.items():
        declared = {
            host for token in directives.get(directive, [])
            if (host := _csp_source_host(token)) is not None
        }
        for host in sorted(hosts - declared):
            report.error(
                "headers.csp_missing_host",
                f"Content-Security-Policy {directive} must allow the manifest-approved host {host}.",
                "_headers",
            )


def parse_pages(root: Path, report: ValidationReport) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    for path in sorted(root.rglob("*.html")):
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        parser = PageParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:  # HTMLParser is forgiving, but keep the report deterministic.
            report.error("html.parse", f"Could not parse HTML: {exc}", rel)
        pages.append(ParsedPage(rel, parser, text))
    if not pages:
        report.error("html.none", "The site contains no HTML pages.")
    return pages


def validate_manifest_page_titles(
    manifest: dict[str, Any] | None,
    pages: list[ParsedPage],
    report: ValidationReport,
) -> None:
    if not manifest or not isinstance(manifest.get("pages"), list):
        return
    actual = {page.relpath: page.parser.title for page in pages}
    for index, item in enumerate(manifest["pages"]):
        if not isinstance(item, dict) or not isinstance(item.get("file_path"), str):
            continue
        try:
            rel = normalize_page_path(item["file_path"])
        except ValueError:
            continue
        title = item.get("title")
        if rel in actual and isinstance(title, str) and title != actual[rel]:
            report.error(
                "manifest.title_mismatch",
                f"pages[{index}] title must exactly match the page title: {actual[rel]}",
                "site-manifest.json",
            )


def validate_page(
    page: ParsedPage,
    root: Path,
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> set[str]:
    rel = page.relpath
    p = page.parser
    if not p.has_charset:
        report.error("html.charset", "Add a UTF-8 charset meta tag.", rel)
    if not p.html_lang:
        report.error("html.lang", "The html element needs a lang attribute.", rel)
    if not p.title:
        report.error("seo.title", "The page needs a non-empty title.", rel)
    elif not 15 <= len(p.title) <= 70:
        report.warning("seo.title_length", "Title length is outside the usual 15-70 character range.", rel)
    description = p.meta_value(name="description")
    if not description:
        report.error("seo.description", "The page needs a meta description.", rel)
    elif not 50 <= len(description) <= 180:
        report.warning("seo.description_length", "Meta description length is outside the usual 50-180 character range.", rel)
    if not p.meta_value(name="viewport"):
        report.error("responsive.viewport", "The page needs a viewport meta tag.", rel)
    if p.h1_count != 1:
        report.error("structure.h1", f"Expected exactly one h1; found {p.h1_count}.", rel)
    duplicate_ids = sorted({value for value in p.ids if p.ids.count(value) > 1})
    if duplicate_ids:
        report.error("html.duplicate_ids", f"Duplicate IDs: {', '.join(duplicate_ids)}", rel)
    if production:
        if not p.canonical:
            report.error("seo.canonical", "The page needs one canonical URL.", rel)
        elif manifest and isinstance(manifest.get("public_domain"), str):
            domain = manifest["public_domain"].rstrip("/")
            manifest_page = next(
                (
                    item for item in manifest.get("pages", [])
                    if isinstance(item, dict) and item.get("file_path") == rel
                ),
                None,
            )
            canonical_path = manifest_page.get("canonical_url_path") if isinstance(manifest_page, dict) else None
            if not isinstance(canonical_path, str):
                report.error("seo.canonical_contract", "The page has no canonical_url_path in the manifest.", rel)
            else:
                expected = domain + canonical_path
                if p.canonical != expected:
                    report.error("seo.canonical_mismatch", f"Canonical URL must be exactly {expected}.", rel)
        elif not p.canonical.startswith("https://"):
            report.error("seo.canonical_scheme", "Canonical URL must use HTTPS.", rel)
    if not p.meta_value(prop="og:title"):
        report.warning("social.og_title", "Open Graph title is missing.", rel)
    if not p.meta_value(prop="og:description"):
        report.warning("social.og_description", "Open Graph description is missing.", rel)
    for item in p.images:
        tag = item.get("tag")
        if tag == "img" and "alt" not in item:
            report.error("a11y.image_alt", "Every img element needs an alt attribute; use empty alt only for decorative images.", rel)
        if tag == "iframe":
            src = item.get("src", "")
            try:
                host = urlparse(src).hostname or ""
            except ValueError:
                host = ""
            if src and not is_allowed_embed_host(host):
                report.error("embed.host", f"Iframe host is not approved for public embedding: {host}", rel)
            if not item.get("title"):
                report.error("a11y.iframe_title", "Every iframe needs a descriptive title.", rel)
        if tag in {"video", "audio"} and "autoplay" in item and "muted" not in item:
            report.error("media.autoplay_audio", "Autoplay media must not start with sound.", rel)
    for control in p.inputs:
        input_type = control.get("type", "text").lower()
        if input_type in {"hidden", "submit", "button", "reset", "image"}:
            continue
        control_id = control.get("id")
        labelled = bool(control.get("aria-label") or control.get("aria-labelledby") or (control_id and control_id in p.labels_for))
        if not labelled:
            report.error("a11y.form_label", f"Form control '{control.get('name', control.get('tag', 'control'))}' needs an associated label.", rel)
    for form in p.forms:
        method = form.get("method", "get").lower()
        action = form.get("action", "")
        if method == "post" and action == "/api/contact":
            privacy_present = any("privacy" in (item.get("href", "").lower()) for item in p.links)
            if not privacy_present:
                report.error("form.privacy_link", "A form page must link to the privacy notice.", rel)
        if method == "post" and not action:
            report.error("form.action", "POST forms require an action.", rel)
    types: set[str] = set()
    for idx, chunk in enumerate(p.jsonld_chunks):
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError as exc:
            report.error("schema.invalid_json", f"JSON-LD block {idx + 1} is invalid: {exc}", rel)
            continue
        types.update(extract_jsonld_types(data))
    scan_text_for_risks(page.text, report, rel, production)
    return types


def validate_links(
    root: Path,
    pages: list[ParsedPage],
    manifest: dict[str, Any] | None,
    report: ValidationReport,
) -> None:
    route_to_file: dict[str, str] = {}
    file_to_route: dict[str, str] = {}
    if manifest and isinstance(manifest.get("pages"), list):
        for item in manifest["pages"]:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("url_path"), str) and isinstance(item.get("file_path"), str):
                route_to_file[item["url_path"]] = item["file_path"]
                file_to_route[item["file_path"]] = item["url_path"]
    seen: set[tuple[str, str]] = set()
    for page in pages:
        for attr, raw in iter_urls(page):
            url = raw.strip()
            if not url or url.startswith("#") or url.startswith(("mailto:", "tel:", "sms:", "geo:", "data:", "blob:")):
                continue
            if url.lower().startswith("javascript:"):
                report.error("link.javascript", "javascript: URLs are not allowed.", page.relpath)
                continue
            try:
                parsed = urlparse(url)
            except ValueError:
                report.error("link.url", f"URL is malformed: {url}", page.relpath)
                continue
            if parsed.scheme or parsed.netloc:
                if parsed.scheme == "http":
                    report.error("link.insecure", f"External URL must use HTTPS: {url}", page.relpath)
                elif parsed.scheme != "https":
                    report.error("link.scheme", f"External or network-path URL must use an explicit HTTPS scheme: {url}", page.relpath)
                else:
                    try:
                        port = parsed.port
                    except ValueError:
                        port = -1
                    if not parsed.hostname or "\\" in url or any(ord(character) < 32 for character in url):
                        report.error("link.url", "External HTTPS URL is malformed.", page.relpath)
                    if parsed.username or parsed.password:
                        report.error("link.credentials", "External URLs may not contain credentials.", page.relpath)
                    if port is not None:
                        report.error("link.port", "External URLs must use canonical HTTPS without an explicit port.", page.relpath)
                continue
            key = (page.relpath, url)
            if key in seen:
                continue
            seen.add(key)
            candidates = _candidate_paths(root, page.relpath, url)
            current_route = file_to_route.get(page.relpath, "/" + page.relpath)
            resolved_route = urlparse(urljoin(current_route, parsed.path)).path
            mapped_file = route_to_file.get(resolved_route)
            if mapped_file:
                candidates.append(root / mapped_file)
            if not candidates:
                report.error("link.escape", f"Link escapes the site root: {url}", page.relpath)
            elif not any(candidate.is_file() for candidate in candidates):
                report.error("link.broken", f"Missing local target for {attr}: {url}", page.relpath)


def validate_css_urls(root: Path, report: ValidationReport, production: bool) -> None:
    pattern = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
    for path in root.rglob("*.css"):
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        scan_text_for_risks(text, report, rel, production)
        for _, raw in pattern.findall(text):
            url = raw.strip()
            if not url or url.startswith(("data:", "http://", "https://", "#")):
                if url.startswith("http://"):
                    report.error("css.insecure", f"CSS asset must use HTTPS: {url}", rel)
                elif url.startswith("https://"):
                    report.error("css.external_asset", "CSS-referenced public assets must be packaged locally under the V5.2 CSP.", rel)
                continue
            candidates = _candidate_paths(root, rel, url)
            if not any(candidate.is_file() for candidate in candidates):
                report.error("css.broken_asset", f"Missing CSS asset: {url}", rel)


def validate_file_inventory(
    root: Path,
    entries: list[SourceEntry],
    report: ValidationReport,
    production: bool,
) -> None:
    file_count = 0
    total_bytes = 0
    large_files: list[str] = []
    javascript: list[Path] = []
    for entry in entries:
        path = entry.path
        file_count += 1
        size = entry.size
        total_bytes += size
        rel = entry.relative
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            report.error("media.video_bundled", "Large video must be hosted externally, not bundled in the website.", rel)
        if suffix in ARCHIVE_EXTENSIONS:
            report.error("archive.nested", "Nested archives are not allowed in the deployed website.", rel)
        if size > 20 * 1024 * 1024:
            report.error("asset.too_large", "Individual website assets must remain below 20 MiB course limit.", rel)
        elif size > 2 * 1024 * 1024:
            large_files.append(f"{rel} ({size / 1024 / 1024:.1f} MiB)")
        if suffix in TEXT_EXTENSIONS:
            text = read_text(path)
            scan_text_for_risks(text, report, rel, production)
        if suffix in {".js", ".mjs"}:
            javascript.append(path)
    node = shutil.which("node")
    if node:
        for path in javascript:
            result = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True, timeout=20, check=False
            )
            if result.returncode:
                detail = (result.stderr or result.stdout).strip().splitlines()
                message = detail[-1] if detail else "JavaScript syntax check failed."
                report.error("javascript.syntax", message, path.relative_to(root).as_posix())
    elif javascript:
        report.warning("javascript.node_unavailable", "Node.js is unavailable, so JavaScript syntax was not checked.")
    if large_files:
        report.warning("asset.large", "Review large assets: " + "; ".join(large_files[:10]))
    report.stats.update({
        "files": file_count,
        "site_bytes": total_bytes,
        "site_mib": round(total_bytes / 1024 / 1024, 2),
    })


def validate_sitemap_and_robots(root: Path, manifest: dict[str, Any] | None, report: ValidationReport, production: bool) -> None:
    robots = root / "robots.txt"
    sitemap = root / "sitemap.xml"
    if not robots.is_file():
        report.error("seo.robots", "robots.txt is required.", "robots.txt")
    if not sitemap.is_file():
        report.error("seo.sitemap", "sitemap.xml is required.", "sitemap.xml")
        return
    robots_text = read_text(robots) if robots.is_file() else ""
    if robots.is_file() and "sitemap:" not in robots_text.lower():
        report.error("seo.robots_sitemap", "robots.txt must reference the sitemap.", "robots.txt")
    if production and any(
        line.strip().casefold() == "disallow: /" for line in robots_text.splitlines()
    ):
        report.error("seo.robots_blocked", "Production robots.txt may not block the entire website.", "robots.txt")
    try:
        tree = ET.parse(sitemap)
    except ET.ParseError as exc:
        report.error("seo.sitemap_xml", f"sitemap.xml is invalid XML: {exc}", "sitemap.xml")
        return
    locations = {
        (node.text or "").strip()
        for node in tree.getroot().iter()
        if node.tag.endswith("loc") and (node.text or "").strip()
    }
    if production and not locations:
        report.error("seo.sitemap_empty", "sitemap.xml must include at least one URL.", "sitemap.xml")
    if manifest and isinstance(manifest.get("pages"), list) and isinstance(manifest.get("public_domain"), str):
        domain = manifest["public_domain"].rstrip("/")
        expected_locations: set[str] = set()
        for page in manifest["pages"]:
            if not isinstance(page, dict) or not isinstance(page.get("canonical_url_path"), str):
                continue
            if page.get("indexable") is not True:
                continue
            try:
                url_path = normalize_url_path(page["canonical_url_path"])
            except ValueError:
                continue
            expected = domain + url_path
            expected_locations.add(expected)
            if expected not in locations:
                report.error("seo.sitemap_missing_page", f"Sitemap does not contain manifest page: {expected}", "sitemap.xml")
        for location in sorted(locations - expected_locations):
            report.error("seo.sitemap_unlisted_url", f"Sitemap URL is not a manifest page on the configured origin: {location}", "sitemap.xml")
        expected_sitemap = f"Sitemap: {domain}/sitemap.xml"
        sitemap_lines = {line.strip() for line in robots_text.splitlines() if line.strip().lower().startswith("sitemap:")}
        if sitemap_lines != {expected_sitemap}:
            report.error("seo.robots_sitemap_origin", f"robots.txt must contain exactly: {expected_sitemap}", "robots.txt")


def csp_allows_https_host(tokens: list[str], host: str) -> bool:
    value = host.lower().rstrip(".")
    if "https:" in tokens or "*" in tokens:
        return True
    for token in tokens:
        source = token.lower().rstrip("/")
        if not source.startswith("https://"):
            continue
        source_host = source[8:].split("/", 1)[0].split(":", 1)[0]
        if source_host.startswith("*."):
            suffix = source_host[1:]
            if value.endswith(suffix) and value != suffix.lstrip("."):
                return True
        elif value == source_host:
            return True
    return False


def validate_external_media(
    root: Path,
    pages: list[ParsedPage],
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> None:
    if not manifest or not isinstance(manifest.get("external_media"), list):
        return
    page_urls = {
        page.relpath: {url.strip() for _attribute, url in iter_urls(page) if url.strip()}
        for page in pages
    }
    all_page_urls = set().union(*page_urls.values()) if page_urls else set()
    csp: dict[str, list[str]] = {}
    headers_path = root / "_headers"
    if headers_path.is_file():
        match = re.search(r"(?mi)^\s*content-security-policy:\s*(.+)$", read_text(headers_path))
        if match:
            csp = parse_csp(match.group(1))

    allowed_kinds = {"direct-video", "cloudflare-stream", "youtube", "vimeo", "large-download"}
    declared_urls: set[str] = set()
    seen_ids: set[str] = set()
    for idx, item in enumerate(manifest["external_media"]):
        location = f"external_media[{idx}]"
        if not isinstance(item, dict):
            report.error("media.entry", f"{location} must be an object.", "site-manifest.json")
            continue
        for key in ("id", "kind", "url", "page", "purpose"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                report.error("media.field", f"{location} needs a non-empty {key}.", "site-manifest.json")
        kind = str(item.get("kind", ""))
        if kind and kind not in allowed_kinds:
            report.error("media.kind", f"{location} kind must be one of: {', '.join(sorted(allowed_kinds))}.", "site-manifest.json")
        url = item.get("url")
        try:
            parsed = urlparse(url) if isinstance(url, str) else None
        except ValueError:
            parsed = None
        if not isinstance(url, str) or not url.startswith("https://") or not parsed or not parsed.hostname:
            report.error("media.url", f"{location} needs an HTTPS URL with a hostname.", "site-manifest.json")
            continue
        declared_urls.add(url)
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in seen_ids:
                report.error("media.id_duplicate", f"External media id is duplicated: {item_id}", "site-manifest.json")
            seen_ids.add(item_id)
        if production and item.get("owner_approved") is not True:
            report.error("media.approval", f"{location} must record owner_approved as true.", "site-manifest.json")
        if production and (not isinstance(item.get("accessibility"), str) or not item["accessibility"].strip()):
            report.error("media.accessibility", f"{location} needs an accessibility treatment note.", "site-manifest.json")
        host = parsed.hostname.lower()
        if host in DISALLOWED_PUBLIC_MEDIA_HOSTS or host.endswith(".drive.google.com") or host.endswith(".dropbox.com"):
            report.error("media.private_host", f"{location} uses a private/file-sharing host. Publish a delivery copy through R2, Stream, YouTube, Vimeo, or another approved public media host.", "site-manifest.json")
        page_value = item.get("page")
        if isinstance(page_value, str) and page_value.strip():
            try:
                page_rel = normalize_page_path(page_value)
            except ValueError as exc:
                report.error("media.page", f"{location} has an invalid page path: {exc}", "site-manifest.json")
                page_rel = ""
            page_path = root / page_rel
            if page_rel and not page_path.is_file():
                report.error("media.page", f"{location} references a missing page: {page_rel}", "site-manifest.json")
            elif page_rel and url not in page_urls.get(page_rel, set()):
                if production:
                    report.error("media.unused", f"{location} URL is not present on its declared page: {page_rel}", "site-manifest.json")
                else:
                    report.warning("media.unused", f"{location} URL is not present on its declared page: {page_rel}", "site-manifest.json")
        elif url not in all_page_urls:
            if production:
                report.error("media.unused", f"{location} URL is not present in any HTML page.", "site-manifest.json")
            else:
                report.warning("media.unused", f"{location} URL is not present in any HTML page.", "site-manifest.json")

        poster = item.get("poster")
        if poster:
            try:
                poster_rel = normalize_page_path(str(poster))
            except ValueError as exc:
                report.error("media.poster", f"External media poster path is invalid: {exc}", "site-manifest.json")
            else:
                if not (root / poster_rel).is_file():
                    report.error("media.poster", f"External media poster is missing: {poster}", "site-manifest.json")
        elif kind == "direct-video":
            report.error("media.poster_required", f"{location} direct video needs an optimized local poster image.", "site-manifest.json")

        if kind == "direct-video":
            if PurePosixPath(parsed.path).suffix.lower() not in {".mp4", ".webm", ".m4v"}:
                report.warning("media.direct_extension", f"{location} direct video URL does not end in a recognized web video extension.", "site-manifest.json")
            if not csp_allows_https_host(csp.get("media-src", []), host):
                report.error("media.csp", f"Content-Security-Policy media-src does not allow {host}.", "_headers")
        elif kind in {"cloudflare-stream", "youtube", "vimeo"}:
            if kind == "cloudflare-stream" and not (host.endswith(".cloudflarestream.com") or host.endswith(".videodelivery.net")):
                report.error("media.host", f"{location} is marked cloudflare-stream but uses {host}.", "site-manifest.json")
            if kind == "youtube" and host not in {"www.youtube-nocookie.com", "www.youtube.com", "youtube.com"}:
                report.error("media.host", f"{location} is marked youtube but uses {host}.", "site-manifest.json")
            if kind == "vimeo" and host != "player.vimeo.com":
                report.error("media.host", f"{location} is marked vimeo but uses {host}.", "site-manifest.json")
            if not csp_allows_https_host(csp.get("frame-src", []), host):
                report.error("media.csp", f"Content-Security-Policy frame-src does not allow {host}.", "_headers")

    for page in pages:
        for item in page.parser.images:
            tag = item.get("tag")
            if tag not in {"iframe", "video", "audio", "source"}:
                continue
            src = item.get("src", "")
            if src.startswith("https://") and src not in declared_urls:
                report.error("media.undeclared", f"External {tag} URL is not declared in site-manifest.json: {src}", page.relpath)


def validate_external_asset_contract(
    pages: list[ParsedPage],
    manifest: dict[str, Any] | None,
    report: ValidationReport,
) -> None:
    forms_enabled = bool(
        manifest and isinstance(manifest.get("forms"), list)
        and any(isinstance(item, dict) and item.get("enabled") is True for item in manifest["forms"])
    )
    turnstile_script = "https://challenges.cloudflare.com/turnstile/v0/api.js"
    for page in pages:
        for item in page.parser.images:
            tag = item.get("tag")
            src = item.get("src", "")
            poster = item.get("poster", "")
            if poster.startswith(("http://", "https://")):
                report.error("asset.external_poster", "Media poster images must be optimized local files.", page.relpath)
            if tag == "script" and src.startswith(("http://", "https://")):
                if src != turnstile_script or not forms_enabled:
                    report.error("asset.external_script", f"External script is not part of the approved form contract: {src}", page.relpath)
            if tag == "img":
                candidates = [src]
                srcset = item.get("srcset", "")
                candidates.extend(
                    part.strip().split()[0] for part in srcset.split(",") if part.strip()
                )
                for candidate in candidates:
                    if candidate.startswith(("http://", "https://")):
                        report.error("asset.external_image", "Public images must be optimized local files under the V5.2 CSP.", page.relpath)
        for item in page.parser.links:
            if item.get("tag") != "link":
                continue
            href = item.get("href", "")
            rel = {token.casefold() for token in item.get("rel", "").split()}
            loads_resource = bool(rel & {"stylesheet", "icon", "preload", "modulepreload", "manifest"})
            if loads_resource and href.startswith(("http://", "https://")):
                report.error("asset.external_link_resource", "Stylesheets, icons, and other link resources must be packaged locally.", page.relpath)


def validate_forms_infrastructure(
    root: Path,
    repo_root: Path | None,
    pages: list[ParsedPage],
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> None:
    if not manifest or not isinstance(manifest.get("forms"), list):
        return
    enabled_forms = [f for f in manifest["forms"] if isinstance(f, dict) and f.get("enabled", True)]
    if not enabled_forms:
        return
    pages_by_path = {page.relpath: page for page in pages}
    for index, form in enumerate(enabled_forms):
        if form.get("action") != "/api/contact":
            report.error("form.manifest_action", f"forms[{index}] action must be exactly /api/contact.", "site-manifest.json")
        try:
            form_page_rel = normalize_page_path(form.get("page")) if isinstance(form.get("page"), str) else ""
        except ValueError as exc:
            report.error("form.page", f"forms[{index}] page is invalid: {exc}", "site-manifest.json")
            form_page_rel = ""
        form_page = pages_by_path.get(form_page_rel)
        if form_page is None:
            report.error("form.page", f"forms[{index}] must reference an existing HTML page.", "site-manifest.json")
            widgets: list[dict[str, str]] = []
            script_present = False
        else:
            widgets = form_page.parser.turnstile_widgets
            script_present = any(
                item.get("tag") == "script" and item.get("src") == "https://challenges.cloudflare.com/turnstile/v0/api.js"
                for item in form_page.parser.images
            )
            if not any(
                item.get("method", "get").casefold() == "post" and item.get("action") == "/api/contact"
                for item in form_page.parser.forms
            ):
                report.error("form.page_action", f"forms[{index}] page needs a POST form with action /api/contact.", form_page_rel)
            form_id = form.get("id")
            if not any(
                item.get("tag") == "input" and item.get("type", "text").casefold() == "hidden"
                and item.get("name") == "form_type" and item.get("value") == form_id
                for item in form_page.parser.inputs
            ):
                report.error("form.type_field", f"forms[{index}] page needs a matching hidden form_type field.", form_page_rel)
        expected_action = form.get("turnstile_action")
        matching_widgets = [widget for widget in widgets if widget.get("data-action") == expected_action]
        if not matching_widgets:
            report.error("form.turnstile_widget", f"forms[{index}] needs a cf-turnstile widget with the declared data-action.", "site-manifest.json")
        elif any(not widget.get("data-sitekey") for widget in matching_widgets):
            report.error("form.turnstile_sitekey", "The Turnstile widget needs its public data-sitekey.", "site-manifest.json")
        elif production:
            known_test_keys = {
                "1x00000000000000000000AA",
                "2x00000000000000000000AB",
                "3x00000000000000000000FF",
            }
            for widget in matching_widgets:
                sitekey = widget.get("data-sitekey", "")
                if sitekey in known_test_keys or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", sitekey):
                    report.error("form.turnstile_sitekey", "Production forms require a real, syntactically valid public Turnstile site key.", "site-manifest.json")
        if not script_present:
            report.error("form.turnstile_script", "Enabled forms require the canonical Cloudflare Turnstile script.", "site-manifest.json")
        privacy = form.get("privacy_page")
        try:
            privacy_path = normalize_page_path(privacy) if isinstance(privacy, str) else ""
        except ValueError as exc:
            report.error("form.privacy_page", f"forms[{index}] privacy_page is invalid: {exc}", "site-manifest.json")
        else:
            if not privacy_path or not (root / privacy_path).is_file():
                report.error("form.privacy_page", f"forms[{index}] must reference an existing privacy page.", "site-manifest.json")
    if repo_root is None:
        report.warning("form.repo_root", "Form infrastructure could not be checked because --repo-root was not supplied.")
        return
    contact = repo_root / "functions" / "api" / "contact.js"
    health = repo_root / "functions" / "api" / "health.js"
    if not contact.is_file():
        report.error("form.handler_missing", "Enabled forms require functions/api/contact.js.", contact)
    if not health.is_file():
        report.error("form.health_missing", "Enabled forms require functions/api/health.js.", health)


def validate_public_documents(
    root: Path,
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> None:
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".pdf", ".ics"}
    }
    if not manifest or not isinstance(manifest.get("public_documents"), list):
        for rel in sorted(actual):
            report.error("document.undeclared", f"Public document is not declared in the manifest: {rel}", rel)
        return
    declared: set[str] = set()
    ids: set[str] = set()
    for index, item in enumerate(manifest["public_documents"]):
        location = f"public_documents[{index}]"
        if not isinstance(item, dict):
            report.error("document.entry", f"{location} must be an object.", "site-manifest.json")
            continue
        try:
            rel = normalize_page_path(item.get("path")) if isinstance(item.get("path"), str) else ""
        except ValueError as exc:
            report.error("document.path", f"{location} has an invalid path: {exc}", "site-manifest.json")
            continue
        if Path(rel).suffix.casefold() not in {".pdf", ".ics"}:
            report.error("document.type", f"{location} must reference a PDF or ICS public document.", "site-manifest.json")
        if rel in declared:
            report.error("document.path_duplicate", f"Public document path is duplicated: {rel}", "site-manifest.json")
        declared.add(rel)
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in ids:
                report.error("document.id_duplicate", f"Public document id is duplicated: {item_id}", "site-manifest.json")
            ids.add(item_id)
        if production and item.get("owner_approved") is not True:
            report.error("document.approval", f"{location} must record owner_approved as true.", "site-manifest.json")
        if rel and not (root / rel).is_file():
            report.error("document.missing", f"Declared public document does not exist: {rel}", "site-manifest.json")
        page_value = item.get("page")
        try:
            page_rel = normalize_page_path(page_value) if isinstance(page_value, str) else ""
        except ValueError as exc:
            report.error("document.page", f"{location} has an invalid page: {exc}", "site-manifest.json")
            continue
        page_path = root / page_rel
        if not page_rel or not page_path.is_file():
            report.error("document.page", f"{location} must reference an existing HTML page.", "site-manifest.json")
        elif rel and rel not in read_text(page_path) and f"/{rel}" not in read_text(page_path):
            report.error("document.unused", f"{location} is not linked from its declared page.", "site-manifest.json")
    for rel in sorted(actual - declared):
        report.error("document.undeclared", f"Public document is not declared and owner-approved in the manifest: {rel}", rel)


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None
    if len(data) != 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _ico_dimensions(path: Path) -> set[tuple[int, int]] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 6:
        return None
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1 or count < 1 or len(data) < 6 + count * 16:
        return None
    sizes: set[tuple[int, int]] = set()
    for index in range(count):
        width, height = data[6 + index * 16], data[7 + index * 16]
        sizes.add((width or 256, height or 256))
    return sizes


def validate_icons(
    root: Path,
    pages: list[ParsedPage],
    manifest: dict[str, Any] | None,
    report: ValidationReport,
) -> None:
    required = {
        "assets/icons/favicon.ico": ("icon", "image/x-icon", {"16x16", "32x32", "48x48"}),
        "assets/icons/favicon.svg": ("icon", "image/svg+xml", {"any"}),
        "assets/icons/apple-touch-icon.png": ("apple-touch-icon", "image/png", {"180x180"}),
        "site.webmanifest": ("manifest", "application/manifest+json", set()),
        "assets/icons/icon-192.png": ("app-icon", "image/png", {"192x192"}),
        "assets/icons/icon-512.png": ("app-icon", "image/png", {"512x512"}),
    }
    entries = manifest.get("icons") if manifest else None
    if not isinstance(entries, list):
        report.error("icon.manifest", "site-manifest.json icons must be a list.", "site-manifest.json")
        return
    by_path: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or not isinstance(item.get("file_path"), str):
            report.error("icon.entry", f"icons[{index}] is invalid.", "site-manifest.json")
            continue
        try:
            rel = normalize_page_path(item["file_path"])
        except ValueError as exc:
            report.error("icon.path", f"icons[{index}] has an invalid file_path: {exc}", "site-manifest.json")
            continue
        if rel in by_path:
            report.error("icon.duplicate", f"Icon file is declared more than once: {rel}", "site-manifest.json")
        by_path[rel] = item
    for rel, (expected_rel, mime_type, expected_sizes) in required.items():
        item = by_path.get(rel)
        if item is None:
            report.error("icon.required", f"Required icon-family file is not declared: {rel}", "site-manifest.json")
            continue
        actual_sizes = item.get("sizes")
        if item.get("rel") != expected_rel or item.get("mime_type") != mime_type or not isinstance(actual_sizes, list) or set(actual_sizes) != expected_sizes:
            report.error("icon.contract", f"Icon declaration does not match the V5.2 contract: {rel}", "site-manifest.json")
        if not (root / rel).is_file():
            report.error("icon.missing", f"Declared icon-family file is missing: {rel}", rel)
    ico_sizes = _ico_dimensions(root / "assets/icons/favicon.ico")
    if ico_sizes is not None and not {(16, 16), (32, 32), (48, 48)}.issubset(ico_sizes):
        report.error("icon.ico_sizes", "favicon.ico must contain 16, 32, and 48 pixel images.", "assets/icons/favicon.ico")
    elif ico_sizes is None:
        report.error("icon.ico_invalid", "favicon.ico is not a valid Windows icon container.", "assets/icons/favicon.ico")
    for rel, expected in (
        ("assets/icons/apple-touch-icon.png", (180, 180)),
        ("assets/icons/icon-192.png", (192, 192)),
        ("assets/icons/icon-512.png", (512, 512)),
    ):
        dimensions = _png_dimensions(root / rel)
        if dimensions != expected:
            report.error("icon.png_dimensions", f"{rel} must be a valid {expected[0]}x{expected[1]} PNG.", rel)
    svg = root / "assets/icons/favicon.svg"
    if not svg.is_file() or "<svg" not in read_text(svg)[:500].casefold():
        report.error("icon.svg_invalid", "favicon.svg must be a local SVG image.", "assets/icons/favicon.svg")
    webmanifest_path = root / "site.webmanifest"
    try:
        webmanifest = json.loads(read_text(webmanifest_path))
    except (OSError, json.JSONDecodeError) as exc:
        report.error("icon.webmanifest", f"site.webmanifest is invalid: {exc}", "site.webmanifest")
        webmanifest = {}
    expected_app_icons = {
        ("/assets/icons/icon-192.png", "192x192", "image/png"),
        ("/assets/icons/icon-512.png", "512x512", "image/png"),
    }
    actual_app_icons = {
        (item.get("src"), item.get("sizes"), item.get("type"))
        for item in webmanifest.get("icons", [])
        if isinstance(item, dict)
    } if isinstance(webmanifest, dict) and isinstance(webmanifest.get("icons"), list) else set()
    if not expected_app_icons.issubset(actual_app_icons):
        report.error("icon.webmanifest_icons", "site.webmanifest must reference the local 192x192 and 512x512 PNG app icons.", "site.webmanifest")
    required_head_links = {
        ("icon", "/assets/icons/favicon.ico"),
        ("icon", "/assets/icons/favicon.svg"),
        ("apple-touch-icon", "/assets/icons/apple-touch-icon.png"),
        ("manifest", "/site.webmanifest"),
    }
    for page in pages:
        actual_links: set[tuple[str, str]] = set()
        for item in page.parser.links:
            if item.get("tag") != "link":
                continue
            href = item.get("href", "")
            for rel in item.get("rel", "").casefold().split():
                actual_links.add((rel, href))
        missing = sorted(required_head_links - actual_links)
        if missing:
            report.error("icon.head_links", f"Page is missing required icon or webmanifest links: {missing}", page.relpath)


def _parse_redirects(root: Path, report: ValidationReport) -> dict[str, tuple[str, str]]:
    path = root / "_redirects"
    redirects: dict[str, tuple[str, str]] = {}
    if not path.is_file():
        return redirects
    for line_number, raw in enumerate(read_text(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in {2, 3}:
            report.error("legacy.redirect_syntax", f"_redirects line {line_number} must contain source, destination, and optional status.", "_redirects")
            continue
        source, destination = parts[:2]
        status = parts[2] if len(parts) == 3 else "302"
        if not source.startswith("/"):
            continue
        if not destination.startswith("/"):
            report.error("legacy.redirect_external", "Internal legacy routes may redirect only to a declared path on this website.", "_redirects")
            continue
        if any(token in source for token in ("*", ":")):
            code = "legacy.redirect_blanket_home" if destination == "/" else "legacy.redirect_pattern"
            report.error(code, "Legacy redirects must be exact paths; wildcard or parameter routes are not approved.", "_redirects")
            continue
        try:
            source = normalize_url_path(source)
            destination = normalize_url_path(destination)
        except ValueError as exc:
            report.error("legacy.redirect_path", f"_redirects line {line_number} has an invalid path: {exc}", "_redirects")
            continue
        if source in redirects:
            report.error("legacy.redirect_duplicate", f"Redirect source appears more than once: {source}", "_redirects")
        redirects[source] = (destination, status)
    return redirects


def validate_legacy_routes(
    root: Path,
    manifest: dict[str, Any] | None,
    report: ValidationReport,
    production: bool,
) -> None:
    redirects = _parse_redirects(root, report)
    items = manifest.get("legacy_routes") if manifest else None
    if not isinstance(items, list):
        report.error("legacy.manifest", "site-manifest.json legacy_routes must be a list.", "site-manifest.json")
        return
    page_routes = {
        item.get("url_path")
        for item in manifest.get("pages", [])
        if isinstance(item, dict) and isinstance(item.get("url_path"), str)
    } if manifest else set()
    page_rewrites: dict[str, str] = {}
    if manifest and isinstance(manifest.get("pages"), list):
        for index, page in enumerate(manifest["pages"]):
            if not isinstance(page, dict) or not isinstance(page.get("file_path"), str) or not isinstance(page.get("url_path"), str):
                continue
            file_path = page["file_path"]
            url_path = page["url_path"]
            if file_path == "index.html":
                natural_path = "/"
            elif file_path.endswith("/index.html"):
                natural_path = "/" + file_path[:-len("index.html")]
            else:
                natural_path = "/" + file_path
            if url_path != natural_path:
                destination = "/" + file_path
                page_rewrites[url_path] = destination
                if redirects.get(url_path) != (destination, "200"):
                    report.error(
                        "manifest.page_route",
                        f"pages[{index}] decouples url_path from file_path, so _redirects must contain exactly: {url_path} {destination} 200",
                        "_redirects",
                    )
    declared_redirects: dict[str, str] = {}
    seen_sources: set[str] = set()
    for index, item in enumerate(items):
        location = f"legacy_routes[{index}]"
        if not isinstance(item, dict):
            report.error("legacy.entry", f"{location} must be an object.", "site-manifest.json")
            continue
        try:
            source = normalize_url_path(item.get("source_url_path")) if isinstance(item.get("source_url_path"), str) else ""
            destination = normalize_url_path(item.get("destination_url_path")) if isinstance(item.get("destination_url_path"), str) else ""
        except ValueError as exc:
            report.error("legacy.path", f"{location} has an invalid path: {exc}", "site-manifest.json")
            continue
        if source in seen_sources:
            report.error("legacy.source_duplicate", f"Legacy source path is duplicated: {source}", "site-manifest.json")
        seen_sources.add(source)
        if production and item.get("owner_approved") is not True:
            report.error("legacy.unapproved", f"{location} must record owner_approved as true.", "site-manifest.json")
        handling = item.get("handling")
        if handling == "rebuild-same-url":
            if source != destination:
                report.error("legacy.rebuild_destination", f"{location} must keep the exact same URL path.", "site-manifest.json")
            if source not in page_routes:
                report.error("legacy.coverage", f"No rebuilt page preserves legacy URL {source}.", "site-manifest.json")
            if source in redirects:
                report.error("legacy.rebuild_redirected", f"Rebuilt legacy URL must not also redirect: {source}", "_redirects")
        elif handling == "permanent-redirect":
            declared_redirects[source] = destination
            if source == destination:
                report.error("legacy.redirect_loop", f"Redirect source and destination are identical: {source}", "site-manifest.json")
            if destination == "/":
                report.error("legacy.redirect_blanket_home", f"Legacy URL {source} may not be discarded into the homepage.", "site-manifest.json")
            if source in page_routes:
                report.error("legacy.redirect_page_collision", f"Redirect source is also declared as a live page: {source}", "site-manifest.json")
            if destination not in page_routes:
                report.error("legacy.redirect_target", f"Redirect target is not a declared live page: {destination}", "site-manifest.json")
            actual = redirects.get(source)
            if actual != (destination, "301"):
                report.error("legacy.redirect_exact", f"_redirects must contain exactly: {source} {destination} 301", "_redirects")
        else:
            report.error("legacy.handling", f"{location} has an unsupported handling value.", "site-manifest.json")
    for source, (destination, status) in redirects.items():
        if page_rewrites.get(source) == destination and status == "200":
            continue
        if declared_redirects.get(source) != destination:
            report.error("legacy.redirect_unapproved", f"Redirect is not declared and owner-approved in legacy_routes: {source}", "_redirects")
    for source, destination in declared_redirects.items():
        if destination in declared_redirects:
            report.error("legacy.redirect_chain", f"Redirect chain is not allowed: {source} -> {destination}", "site-manifest.json")
    for start in declared_redirects:
        visited: set[str] = set()
        current = start
        while current in declared_redirects:
            if current in visited:
                report.error("legacy.redirect_loop", f"Redirect loop includes {current}.", "site-manifest.json")
                break
            visited.add(current)
            current = declared_redirects[current]


def validate_site(
    root: Path,
    mode: str = "auto",
    repo_root: Path | None = None,
    policy_path: Path | None = None,
) -> ValidationReport:
    original_root = root.absolute()
    report = ValidationReport(str(original_root), mode)
    if original_root.is_symlink():
        report.error("filesystem.symlink", "The public-tree root may not be a symbolic link.", original_root)
        return report
    root = original_root.resolve()
    if repo_root:
        if repo_root.absolute().is_symlink():
            report.error("filesystem.repo_symlink", "Repository root may not be a symbolic link.", repo_root)
            return report
        repo_root = repo_root.resolve()
    if not root.is_dir():
        report.error("site.missing", "Website directory does not exist.", root)
        return report
    if policy_path is None and repo_root is not None:
        policy_path = repo_root / "infrastructure" / "importer-policy.json"
    if policy_path is None:
        report.error("policy.location", "A protected importer-policy.json path is required.")
        return report
    try:
        policy = load_policy(policy_path)
    except ValueError as exc:
        report.error(getattr(exc, "code", "policy.invalid"), str(exc), getattr(exc, "path", policy_path))
        return report
    entries, policy_violations = validate_source_tree(root, policy)
    for violation in policy_violations:
        report.error(violation.code, violation.message, violation.path)
    if any(violation.code.startswith("filesystem.") for violation in policy_violations):
        return report
    if not (root / "index.html").is_file():
        report.error("site.index", "index.html is required at the website root.", "index.html")
    manifest_probe: dict[str, Any] | None = None
    manifest_path = root / "site-manifest.json"
    if manifest_path.is_file():
        try:
            loaded = json.loads(read_text(manifest_path))
            if isinstance(loaded, dict):
                manifest_probe = loaded
        except json.JSONDecodeError:
            pass
    if mode == "auto":
        production = bool(manifest_probe and manifest_probe.get("stage") == "production")
        report.mode = "production" if production else "starter"
    else:
        production = mode == "production"
    validate_file_inventory(root, entries, report, production)
    manifest = validate_manifest(root, repo_root, report, production)
    validate_version(root, manifest, report)
    validate_headers(root, manifest, report, production)
    pages = parse_pages(root, report)
    validate_manifest_page_titles(manifest, pages, report)
    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    all_types: set[str] = set()
    for page in pages:
        types = validate_page(page, root, manifest, report, production)
        all_types.update(types)
        title = page.parser.title
        if title:
            if title in titles:
                report.error("seo.duplicate_title", f"Title duplicates {titles[title]}: {title}", page.relpath)
            else:
                titles[title] = page.relpath
        description = page.parser.meta_value(name="description")
        if description:
            if description in descriptions:
                report.warning("seo.duplicate_description", f"Description duplicates {descriptions[description]}.", page.relpath)
            else:
                descriptions[description] = page.relpath
    if production and not ({"Restaurant", "FoodEstablishment"} & all_types):
        report.error("schema.restaurant", "Production restaurant websites need Restaurant or FoodEstablishment JSON-LD.")
    validate_links(root, pages, manifest, report)
    validate_css_urls(root, report, production)
    validate_sitemap_and_robots(root, manifest, report, production)
    validate_legacy_routes(root, manifest, report, production)
    validate_icons(root, pages, manifest, report)
    validate_external_media(root, pages, manifest, report, production)
    validate_external_asset_contract(pages, manifest, report)
    validate_public_documents(root, manifest, report, production)
    validate_forms_infrastructure(root, repo_root, pages, manifest, report, production)
    report.stats["html_pages"] = len(pages)
    report.stats["sha256_tree"] = tree_digest(entries)
    return report


def tree_digest(entries: list[SourceEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.relative):
        rel = entry.relative.encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        with entry.path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def write_report(report: ValidationReport, json_path: Path | None, md_path: Path | None) -> None:
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(report.to_markdown(), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Website output directory, normally public/")
    parser.add_argument("--mode", choices=("auto", "starter", "production"), default="auto")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    parser.add_argument("--policy", type=Path, default=None)
    args = parser.parse_args(argv)
    report = validate_site(args.root, args.mode, args.repo_root, args.policy)
    write_report(report, args.report_json, args.report_md)
    print(report.to_markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
