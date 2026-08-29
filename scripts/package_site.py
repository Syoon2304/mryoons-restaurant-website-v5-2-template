#!/usr/bin/env python3
"""Validate public/ and create a deterministic phone-ready website.zip."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_site import validate_site  # noqa: E402
from public_package_policy import SourceEntry, load_policy, validate_source_tree  # noqa: E402

def _entry_matches(info: os.stat_result, entry: SourceEntry) -> bool:
    return bool(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_size == entry.size
        and info.st_dev == entry.device
        and info.st_ino == entry.inode
        and info.st_mtime_ns == entry.mtime_ns
        and info.st_ctime_ns == entry.ctime_ns
    )


def read_regular_file(entry: SourceEntry) -> bytes:
    path = entry.path
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not _entry_matches(info, entry):
            raise OSError(f"File changed or is not a single regular file: {path}")
        chunks: list[bytes] = []
        remaining = entry.size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError(f"File ended early while packaging: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError(f"File grew while packaging: {path}")
        if not _entry_matches(os.fstat(descriptor), entry):
            raise OSError(f"File changed while packaging: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def create_zip(source: Path, output: Path, entries) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.relative)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = read_regular_file(entry)
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("public"))
    parser.add_argument("--output", type=Path, default=Path("website.zip"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve(strict=True)
    source = args.source.resolve(strict=True)
    output = args.output.resolve(strict=False)
    policy_path = repo_root / "infrastructure" / "importer-policy.json"
    if source != repo_root / "public":
        print("Package source must be exactly the repository public/ directory.", file=sys.stderr)
        return 2
    if output != repo_root / "website.zip":
        print("Package output must be exactly repository-root website.zip.", file=sys.stderr)
        return 2
    policy = load_policy(policy_path)
    entries, policy_violations = validate_source_tree(source, policy)
    if policy_violations:
        for violation in policy_violations:
            location = f" ({violation.path})" if violation.path else ""
            print(f"{violation.code}{location}: {violation.message}", file=sys.stderr)
        print("Package was not created because the public-tree policy failed.", file=sys.stderr)
        return 1

    report = validate_site(
        source,
        mode="production",
        repo_root=repo_root,
        policy_path=policy_path,
    )
    if not report.passed:
        print(report.to_markdown())
        print("Package was not created because required checks failed.", file=sys.stderr)
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix=".website-package-", dir=repo_root) as candidate_dir:
            candidate = Path(candidate_dir) / "website.zip"
            create_zip(source, candidate, entries)
            size = candidate.stat().st_size
            if size > int(policy["max_compressed_bytes"]):
                raise RuntimeError(
                    f"Package would be {size / 1024 / 1024:.2f} MiB, over the "
                    f"{int(policy['max_compressed_bytes']) / 1024 / 1024:.0f} MiB course limit."
                )
            from import_website_zip import (  # Imported late to avoid coupling normal validation to ZIP code.
                ImportReport,
                directories_equal,
                extract_safely,
                inspect_zip,
            )
            inspection = ImportReport(package=candidate.name)
            zip_entries = inspect_zip(candidate, policy, inspection)
            with tempfile.TemporaryDirectory(prefix="website-package-roundtrip-") as temp:
                extracted = Path(temp) / "public"
                extract_safely(candidate, zip_entries, extracted)
                round_trip = validate_site(
                    extracted,
                    mode="production",
                    repo_root=repo_root,
                    policy_path=policy_path,
                )
                if not round_trip.passed:
                    raise RuntimeError("Round-trip production validation failed:\n" + round_trip.to_markdown())
                if not directories_equal(source, extracted):
                    raise RuntimeError("Round-trip extracted files do not exactly match public/.")
            os.replace(candidate, output)
    except Exception as exc:
        print(f"Package was not created: {exc}", file=sys.stderr)
        return 1

    manifest = json.loads((source / "site-manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "created": str(output),
        "bytes": size,
        "mib": round(size / 1024 / 1024, 2),
        "sha256": sha256(output),
        "package_version": manifest.get("package_version"),
        "round_trip_verified": True,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
