#!/usr/bin/env python3
"""Safely import website.zip into public/ after validation.

The ZIP is treated as untrusted input. It is inspected before extraction,
extracted into a clean staging directory, validated as a complete production
site, and only then swapped into public/. A failed import never changes the
committed website.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_site import ValidationReport, validate_site  # noqa: E402
from public_package_policy import (  # noqa: E402
    ensure_safe_destination,
    is_within,
    load_policy as load_shared_policy,
    normalize_relative_name,
    validate_public_name,
)


class ImportRejected(RuntimeError):
    """Raised when a package violates the transport contract."""

    def __init__(self, code: str, message: str, path: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


@dataclass
class ImportReport:
    passed: bool = False
    package: str = ""
    package_sha256: str = ""
    compressed_bytes: int = 0
    expanded_bytes: int = 0
    file_count: int = 0
    package_version: str | None = None
    site_name: str | None = None
    changed: bool | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    validation: dict[str, Any] | None = None

    def reject(self, exc: ImportRejected) -> None:
        entry = {"code": exc.code, "message": exc.message}
        if exc.path:
            entry["path"] = exc.path
        self.errors.append(entry)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "package": self.package,
            "package_sha256": self.package_sha256,
            "compressed_bytes": self.compressed_bytes,
            "expanded_bytes": self.expanded_bytes,
            "file_count": self.file_count,
            "package_version": self.package_version,
            "site_name": self.site_name,
            "changed": self.changed,
            "errors": self.errors,
            "warnings": self.warnings,
            "validation": self.validation,
        }

    def to_markdown(self) -> str:
        if self.passed:
            lines = [
                "# Website package accepted",
                "",
                "The previous live site remained untouched until every required check passed.",
                "",
                f"- Site: **{self.site_name or 'Website'}**",
                f"- Package version: `{self.package_version or 'unknown'}`",
                f"- Files imported: `{self.file_count}`",
                f"- Compressed size: `{self.compressed_bytes / 1024 / 1024:.2f} MiB`",
                f"- Expanded size: `{self.expanded_bytes / 1024 / 1024:.2f} MiB`",
                f"- Package SHA-256: `{self.package_sha256}`",
                "",
                "## Student next action",
                "Wait for the Cloudflare deployment to finish, then open the live website on cellular data and complete the owner phone test.",
            ]
            if self.changed is False:
                lines.extend(["", "No website file changed, so no new site commit was needed."])
            return "\n".join(lines) + "\n"
        lines = [
            "# Website package rejected",
            "",
            "The previous website was preserved. Fix the first issue below and upload a corrected `website.zip`.",
            "",
        ]
        for item in self.errors:
            location = f" (`{item['path']}`)" if item.get("path") else ""
            lines.append(f"- **{item['code']}**{location}: {item['message']}")
        if not self.errors:
            lines.append("- The package failed for an unknown reason. Ask course support to review the workflow log.")
        return "\n".join(lines) + "\n"


def load_policy(path: Path) -> dict[str, Any]:
    try:
        return load_shared_policy(path)
    except ValueError as exc:
        raise ImportRejected(
            getattr(exc, "code", "policy.invalid"),
            str(exc),
            getattr(exc, "path", str(path)),
        ) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def zip_entry_is_special(info: zipfile.ZipInfo) -> bool:
    if info.create_system != 3:
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    return file_type not in {0, stat.S_IFREG, stat.S_IFDIR}


def normalize_entry_name(name: str) -> str:
    try:
        return normalize_relative_name(name)
    except ValueError as exc:
        mapping = {
            "path.backslash": "zip.backslash",
            "path.nul": "zip.nul",
            "path.absolute": "zip.absolute_path",
            "path.traversal": "zip.path_traversal",
            "path.noncanonical": "zip.path_traversal",
            "path.characters": "zip.filename_chars",
            "path.reserved": "zip.filename_chars",
            "path.empty": "zip.path_traversal",
        }
        raise ImportRejected(
            mapping.get(getattr(exc, "code", ""), "zip.path"), str(exc), getattr(exc, "path", name)
        ) from exc


def validate_entry_name(name: str, policy: dict[str, Any]) -> None:
    try:
        validate_public_name(name, policy)
    except ValueError as exc:
        mapping = {
            "path.protected": "zip.protected_path",
            "path.hidden": "zip.hidden_path",
            "path.extension": "zip.extension",
            "privacy.filename": "zip.private_source",
            "privacy.capture": "zip.private_source",
            "privacy.file_type": "zip.private_source",
        }
        raise ImportRejected(
            mapping.get(getattr(exc, "code", ""), "zip.path"), str(exc), getattr(exc, "path", name)
        ) from exc


def inspect_zip(package: Path, policy: dict[str, Any], report: ImportReport) -> list[zipfile.ZipInfo]:
    if package.name != policy["package_filename"]:
        raise ImportRejected("package.filename", f"The file must be named exactly {policy['package_filename']}.", package.name)
    if not package.is_file():
        raise ImportRejected("package.missing", "website.zip was not found.", str(package))
    report.compressed_bytes = package.stat().st_size
    if report.compressed_bytes > int(policy["max_compressed_bytes"]):
        raise ImportRejected(
            "package.too_large",
            f"The ZIP is {report.compressed_bytes / 1024 / 1024:.2f} MiB; the course limit is {int(policy['max_compressed_bytes']) / 1024 / 1024:.0f} MiB. Move videos or large downloads to R2/Stream and rebuild.",
            package.name,
        )
    if not zipfile.is_zipfile(package):
        raise ImportRejected("package.invalid_zip", "The file is not a valid ZIP archive.", package.name)
    entries: list[zipfile.ZipInfo] = []
    seen_exact: set[str] = set()
    seen_casefold: dict[str, str] = {}
    total_size = 0
    file_count = 0
    with zipfile.ZipFile(package) as zf:
        for info in zf.infolist():
            normalized = normalize_entry_name(info.filename.rstrip("/")) if info.filename.rstrip("/") else ""
            if not normalized:
                continue
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise ImportRejected("zip.encrypted", "Encrypted ZIP entries are not supported.", normalized)
            if zip_entry_is_symlink(info):
                raise ImportRejected("zip.symlink", "Symbolic links are not allowed.", normalized)
            if zip_entry_is_special(info):
                raise ImportRejected("zip.special_file", "Only regular files and directories are allowed.", normalized)
            validate_entry_name(normalized, policy)
            if normalized in seen_exact:
                raise ImportRejected("zip.duplicate", "Duplicate ZIP path.", normalized)
            case_key = normalized.casefold()
            if case_key in seen_casefold:
                raise ImportRejected("zip.case_collision", f"Case-insensitive path collision with {seen_casefold[case_key]}.", normalized)
            seen_exact.add(normalized)
            seen_casefold[case_key] = normalized
            file_count += 1
            total_size += info.file_size
            if file_count > int(policy["max_file_count"]):
                raise ImportRejected("zip.file_count", f"The package exceeds the {policy['max_file_count']} file limit.")
            if info.file_size > int(policy["max_single_file_bytes"]):
                raise ImportRejected("zip.single_file_size", "An individual file exceeds the course asset limit.", normalized)
            extension_limits = policy.get("max_bytes_by_extension", {})
            ext_limit = extension_limits.get(PurePosixPath(normalized).suffix.lower())
            if ext_limit is not None and info.file_size > int(ext_limit):
                raise ImportRejected("zip.extension_size", "This file type exceeds its course size limit.", normalized)
            if total_size > int(policy["max_expanded_bytes"]):
                raise ImportRejected("zip.expanded_size", "The expanded website exceeds the safe extraction limit.")
            if info.file_size > 0:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > float(policy["max_compression_ratio"]):
                    raise ImportRejected("zip.compression_ratio", "An entry has a suspicious compression ratio and may be a ZIP bomb.", normalized)
            entries.append(info)
    names = {normalize_entry_name(info.filename) for info in entries}
    for required in policy["required_root_files"]:
        if required not in names:
            code = {
                "index.html": "package.index",
                "site-manifest.json": "package.manifest",
                "version.json": "package.version",
            }.get(required, "package.required")
            raise ImportRejected(code, f"{required} is required at the ZIP root.", required)
    report.expanded_bytes = total_size
    report.file_count = file_count
    return entries


def extract_safely(package: Path, entries: list[zipfile.ZipInfo], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(package) as zf:
        for info in entries:
            name = normalize_entry_name(info.filename)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = destination.parent.resolve()
            if target.resolve() not in (resolved_parent, *resolved_parent.parents):
                raise ImportRejected("extract.escape", "Extraction path escaped the staging directory.", name)
            with zf.open(info, "r") as source, destination.open("wb") as output:
                copied = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    output.write(chunk)
                if copied != info.file_size:
                    raise ImportRejected("extract.size_mismatch", "Extracted file size did not match ZIP metadata.", name)
            os.chmod(destination, 0o644)


def read_manifest(root: Path, report: ImportReport) -> None:
    try:
        manifest = json.loads((root / "site-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(manifest, dict):
        value = manifest.get("package_version")
        if isinstance(value, str):
            report.package_version = value
        value = manifest.get("site_name")
        if isinstance(value, str):
            report.site_name = value


def directories_equal(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists():
        return False
    def inventory(root: Path) -> dict[str, str] | None:
        result: dict[str, str] = {}
        if root.is_symlink() or not root.is_dir():
            return None
        resolved_root = root.resolve(strict=True)
        stack = [root]
        while stack:
            directory = stack.pop()
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    return None
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                    continue
                if info.st_nlink > 1 or not is_within(resolved_root, path.resolve(strict=True)):
                    return None
                rel = path.relative_to(root).as_posix()
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                try:
                    digest = hashlib.sha256()
                    while chunk := os.read(descriptor, 1024 * 1024):
                        digest.update(chunk)
                    result[rel] = digest.hexdigest()
                finally:
                    os.close(descriptor)
        return result
    first = inventory(a)
    second = inventory(b)
    return first is not None and second is not None and first == second


def replace_directory(staged: Path, destination: Path) -> bool:
    if directories_equal(staged, destination):
        return False
    backup = destination.parent / f".{destination.name}.previous-import"
    if destination.is_symlink() or backup.is_symlink():
        raise ImportRejected("destination.symlink", "Importer destination and backup may not be symbolic links.")
    if backup.exists():
        shutil.rmtree(backup)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        if backup.exists():
            backup.rename(destination)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)
    return True


def write_report(report: ImportReport, json_path: Path | None, md_path: Path | None) -> None:
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(report.to_markdown(), encoding="utf-8")


def run_import(
    package: Path,
    destination: Path,
    policy_path: Path,
    report: ImportReport,
    repo_root: Path | None = None,
) -> None:
    try:
        repo_root, destination = ensure_safe_destination(repo_root or destination.parent, destination)
    except (ValueError, OSError) as exc:
        raise ImportRejected(
            getattr(exc, "code", "destination.invalid"), str(exc), getattr(exc, "path", str(destination))
        ) from exc
    if package.is_symlink():
        raise ImportRejected("package.symlink", "website.zip may not be a symbolic link.", str(package))
    if policy_path.is_symlink():
        raise ImportRejected("policy.symlink", "The protected importer policy may not be a symbolic link.", str(policy_path))
    package = package.resolve(strict=False)
    policy_path = policy_path.resolve(strict=False)
    if package.parent != repo_root or package.name != "website.zip":
        raise ImportRejected("package.location", "website.zip must be the repository-root transport file.", str(package))
    expected_policy = repo_root / "infrastructure" / "importer-policy.json"
    if policy_path != expected_policy:
        raise ImportRejected("policy.location", "Importer policy must be the protected repository policy file.", str(policy_path))
    policy = load_policy(policy_path)
    report.package = package.name
    entries = inspect_zip(package, policy, report)
    report.package_sha256 = sha256_file(package)
    staging_parent = repo_root / ".website-import-staging"
    if staging_parent.is_symlink():
        raise ImportRejected("staging.symlink", "Importer staging path may not be a symbolic link.", str(staging_parent))
    if staging_parent.exists():
        shutil.rmtree(staging_parent)
    staging_parent.mkdir(parents=True)
    staged_site = staging_parent / "new-public"
    try:
        extract_safely(package, entries, staged_site)
        read_manifest(staged_site, report)
        validation: ValidationReport = validate_site(
            staged_site,
            mode="production",
            repo_root=repo_root,
            policy_path=policy_path,
        )
        report.validation = validation.as_dict()
        report.warnings.extend(f.as_dict() for f in validation.warnings)
        if not validation.passed:
            first = validation.errors[0]
            raise ImportRejected(first.code, first.message, first.path)
        report.changed = replace_directory(staged_site, destination)
        report.passed = True
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="package", type=Path, default=Path("website.zip"))
    parser.add_argument("--destination", type=Path, default=Path("public"))
    parser.add_argument("--policy", type=Path, default=Path("infrastructure/importer-policy.json"))
    parser.add_argument("--report-json", type=Path, default=Path(".website-import-report.json"))
    parser.add_argument("--report-md", type=Path, default=Path(".website-import-report.md"))
    args = parser.parse_args(argv)
    report = ImportReport(package=args.package.name)
    try:
        package = args.package.absolute()
        destination = args.destination.absolute()
        policy = args.policy.absolute()
        repo_root = SCRIPT_DIR.parent.resolve(strict=True)
        for report_path in (args.report_json, args.report_md):
            resolved = report_path.resolve(strict=False)
            if not is_within(repo_root, resolved):
                raise ImportRejected("report.location", "Importer reports must stay inside the repository.", str(report_path))
        run_import(package, destination, policy, report, repo_root=repo_root)
    except ImportRejected as exc:
        report.reject(exc)
    except Exception as exc:  # Fail closed and preserve a useful owner-facing message.
        report.errors.append({"code": "import.internal", "message": f"Unexpected importer error: {exc}"})
    write_report(report, args.report_json, args.report_md)
    print(report.to_markdown())
    if not report.passed:
        first = report.errors[0] if report.errors else {"code": "import.failed", "message": "Import failed."}
        print(f"::error title=Website package rejected::{first['code']}: {first['message']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
