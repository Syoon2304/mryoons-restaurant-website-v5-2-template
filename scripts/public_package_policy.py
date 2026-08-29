#!/usr/bin/env python3
"""Shared public-tree and ZIP path policy for the V5.2 publishing system.

The validator, deterministic packager, and phone importer all call this module.
Keeping one implementation prevents a locally "valid" tree from producing a ZIP
that the GitHub importer later rejects.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


ASCII_PATH = re.compile(r"[A-Za-z0-9._/-]+")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class PolicyViolation:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class SourceEntry:
    path: Path
    relative: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int


def _fail(code: str, message: str, path: str | None = None) -> ValueError:
    error = ValueError(message)
    error.code = code  # type: ignore[attr-defined]
    error.path = path  # type: ignore[attr-defined]
    return error


def load_policy(path: Path) -> dict[str, Any]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("policy.invalid", f"Could not load importer policy: {exc}", str(path)) from exc
    if not isinstance(policy, dict):
        raise _fail("policy.type", "Importer policy must be a JSON object.", str(path))
    required = {
        "policy_version", "package_filename", "max_compressed_bytes", "max_expanded_bytes",
        "max_single_file_bytes", "max_file_count", "max_compression_ratio",
        "allowed_extensions", "allowed_extensionless", "disallowed_directories",
        "required_root_files", "forbidden_exact_names", "forbidden_name_parts",
        "forbidden_suffixes", "forbidden_capture_endings", "max_bytes_by_extension",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise _fail("policy.missing", f"Importer policy is missing: {', '.join(missing)}", str(path))
    unexpected = sorted(set(policy) - required)
    if unexpected:
        raise _fail("policy.unexpected", f"Importer policy has unexpected fields: {', '.join(unexpected)}", str(path))
    if policy.get("policy_version") != "2.0.0" or policy.get("package_filename") != "website.zip":
        raise _fail("policy.version", "Policy version and package filename must match the V5.2 contract.", str(path))
    integer_keys = (
        "max_compressed_bytes", "max_expanded_bytes", "max_single_file_bytes",
        "max_file_count", "max_compression_ratio",
    )
    for key in integer_keys:
        if isinstance(policy.get(key), bool) or not isinstance(policy.get(key), int) or int(policy[key]) <= 0:
            raise _fail("policy.value", f"{key} must be a positive integer.", str(path))
    list_keys = required - {"policy_version", "package_filename", "max_bytes_by_extension", *integer_keys}
    for key in list_keys:
        if not isinstance(policy.get(key), list) or not all(isinstance(v, str) for v in policy[key]):
            raise _fail("policy.value", f"{key} must be a list of strings.", str(path))
        values = policy[key]
        if len(values) != len({value.casefold() for value in values}) or any(not value for value in values):
            raise _fail("policy.value", f"{key} may not contain empty or duplicate values.", str(path))
    extension_pattern = re.compile(r"\.[a-z0-9]+")
    for key in ("allowed_extensions", "forbidden_suffixes", "forbidden_capture_endings"):
        if any(not value.startswith(".") or value != value.casefold() for value in policy[key]):
            raise _fail("policy.value", f"{key} must contain lowercase dotted suffixes.", str(path))
    if any(not extension_pattern.fullmatch(value) for value in policy["allowed_extensions"]):
        raise _fail("policy.value", "allowed_extensions contains an invalid extension.", str(path))
    for name in policy["required_root_files"]:
        try:
            normalized = normalize_relative_name(name)
        except ValueError as exc:
            raise _fail("policy.value", f"Invalid required root filename: {exc}", str(path)) from exc
        if "/" in normalized:
            raise _fail("policy.value", "required_root_files entries must be at ZIP root.", str(path))
    extension_limits = policy.get("max_bytes_by_extension")
    if not isinstance(extension_limits, dict):
        raise _fail("policy.value", "max_bytes_by_extension must be an object.", str(path))
    allowed_extensions = set(policy["allowed_extensions"])
    for extension, limit in extension_limits.items():
        if extension not in allowed_extensions or isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise _fail("policy.value", "max_bytes_by_extension must map allowed extensions to positive integers.", str(path))
        if limit > policy["max_single_file_bytes"]:
            raise _fail("policy.value", "An extension size limit may not exceed max_single_file_bytes.", str(path))
    return policy


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def normalize_relative_name(name: str) -> str:
    """Return a canonical relative POSIX path or raise a coded ValueError."""
    if not isinstance(name, str) or not name:
        raise _fail("path.empty", "A public path may not be empty.", str(name))
    if "\\" in name:
        raise _fail("path.backslash", "Public paths must use forward slashes.", name)
    if "\x00" in name:
        raise _fail("path.nul", "A public path contains a NUL byte.", name)
    if name.startswith(("/", "~", "//")) or re.match(r"^[A-Za-z]:", name):
        raise _fail("path.absolute", "Absolute and network paths are not allowed.", name)
    pure = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise _fail("path.traversal", "Public paths may not contain empty, dot, or parent segments.", name)
    normalized = pure.as_posix()
    if normalized != name:
        raise _fail("path.noncanonical", "Public paths must already be canonical POSIX paths.", name)
    if not ASCII_PATH.fullmatch(normalized):
        raise _fail(
            "path.characters",
            "Use only ASCII letters, numbers, dots, underscores, hyphens, and slashes in public file names.",
            name,
        )
    for part in pure.parts:
        stem = part.split(".", 1)[0].casefold()
        if stem in WINDOWS_RESERVED:
            raise _fail("path.reserved", "A file name is reserved on Windows.", name)
    return normalized


def validate_public_name(name: str, policy: dict[str, Any]) -> None:
    normalized = normalize_relative_name(name)
    pure = PurePosixPath(normalized)
    lower_parts = [part.casefold() for part in pure.parts]
    disallowed = {str(v).casefold() for v in policy["disallowed_directories"]}
    if any(part in disallowed for part in lower_parts[:-1]):
        raise _fail("path.protected", "The public tree contains a protected or non-public directory.", name)
    if any(part.startswith(".") and part != ".well-known" for part in lower_parts):
        raise _fail("path.hidden", "Hidden files and folders are not allowed in the public tree.", name)

    filename = pure.name
    filename_lower = filename.casefold()
    full_lower = normalized.casefold()
    semantic_lower = re.sub(r"[-_]+", " ", full_lower)
    if filename_lower in {str(v).casefold() for v in policy["forbidden_exact_names"]}:
        raise _fail("privacy.filename", "A private, course, or starter-control filename is not public output.", name)
    if any(
        re.sub(r"[-_]+", " ", str(value).casefold()) in semantic_lower
        for value in policy["forbidden_name_parts"]
    ):
        raise _fail("privacy.filename", "A filename looks like private or course-controlled material.", name)
    if any(filename_lower.endswith(str(v).casefold()) for v in policy["forbidden_capture_endings"]):
        raise _fail("privacy.capture", "Raw browser or website captures are not public output.", name)
    suffix = pure.suffix.casefold()
    if suffix in {str(v).casefold() for v in policy["forbidden_suffixes"]}:
        raise _fail("privacy.file_type", "This private or source-document file type is not allowed.", name)

    if filename_lower in {str(v).casefold() for v in policy["allowed_extensionless"]}:
        return
    allowed = {str(v).casefold() for v in policy["allowed_extensions"]}
    if suffix not in allowed:
        raise _fail("path.extension", f"File type '{suffix or '(none)'}' is not allowed in the public tree.", name)


def _walk_without_following(root: Path) -> tuple[list[Path], list[PolicyViolation]]:
    paths: list[Path] = []
    violations: list[PolicyViolation] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            rel = "." if directory == root else directory.relative_to(root).as_posix()
            violations.append(PolicyViolation("filesystem.read", f"Could not inspect directory: {exc}", rel))
            continue
        for path in children:
            rel = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                violations.append(PolicyViolation("filesystem.stat", f"Could not inspect entry: {exc}", rel))
                continue
            if stat.S_ISLNK(mode):
                violations.append(PolicyViolation("filesystem.symlink", "Symbolic links are not allowed.", rel))
            elif stat.S_ISDIR(mode):
                stack.append(path)
            elif stat.S_ISREG(mode):
                paths.append(path)
            else:
                violations.append(PolicyViolation("filesystem.special", "Only regular files and directories are allowed.", rel))
    return paths, violations


def validate_source_tree(root: Path, policy: dict[str, Any]) -> tuple[list[SourceEntry], list[PolicyViolation]]:
    violations: list[PolicyViolation] = []
    if root.is_symlink():
        return [], [PolicyViolation("filesystem.symlink", "The public-tree root may not be a symbolic link.", str(root))]
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        return [], [PolicyViolation("filesystem.root", f"Public-tree root is unavailable: {exc}", str(root))]
    if not root_resolved.is_dir():
        return [], [PolicyViolation("filesystem.root", "Public-tree root must be a directory.", str(root))]

    paths, walk_violations = _walk_without_following(root)
    violations.extend(walk_violations)
    entries: list[SourceEntry] = []
    seen_casefold: dict[str, str] = {}
    total_size = 0
    for path in sorted(paths):
        rel = path.relative_to(root).as_posix()
        try:
            validate_public_name(rel, policy)
        except ValueError as exc:
            violations.append(PolicyViolation(
                getattr(exc, "code", "path.invalid"), str(exc), getattr(exc, "path", rel)
            ))
        case_key = rel.casefold()
        if case_key in seen_casefold and seen_casefold[case_key] != rel:
            violations.append(PolicyViolation(
                "path.case_collision", f"Case-insensitive path collision with {seen_casefold[case_key]}.", rel
            ))
        else:
            seen_casefold[case_key] = rel
        try:
            resolved = path.resolve(strict=True)
            info = path.lstat()
        except OSError as exc:
            violations.append(PolicyViolation("filesystem.stat", f"Could not inspect file: {exc}", rel))
            continue
        if not is_within(root_resolved, resolved):
            violations.append(PolicyViolation("filesystem.escape", "A file resolves outside the public-tree root.", rel))
            continue
        if info.st_nlink > 1:
            violations.append(PolicyViolation("filesystem.hardlink", "Hard-linked files are not allowed.", rel))
        size = info.st_size
        total_size += size
        if size > int(policy["max_single_file_bytes"]):
            violations.append(PolicyViolation("size.single_file", "An individual file exceeds the public asset limit.", rel))
        limit = policy.get("max_bytes_by_extension", {}).get(path.suffix.casefold())
        if isinstance(limit, int) and size > limit:
            violations.append(PolicyViolation("size.extension", "This file type exceeds its public size limit.", rel))
        entries.append(SourceEntry(
            path=path,
            relative=rel,
            size=size,
            device=info.st_dev,
            inode=info.st_ino,
            mtime_ns=info.st_mtime_ns,
            ctime_ns=info.st_ctime_ns,
        ))

    if len(entries) > int(policy["max_file_count"]):
        violations.append(PolicyViolation(
            "size.file_count", f"The public tree exceeds the {policy['max_file_count']} file limit."
        ))
    if total_size > int(policy["max_expanded_bytes"]):
        violations.append(PolicyViolation("size.expanded", "The public tree exceeds the expanded-size limit."))
    names = {entry.relative for entry in entries}
    for required in policy["required_root_files"]:
        if required not in names:
            violations.append(PolicyViolation("package.required", "A required root file is missing.", required))
    return entries, violations


def ensure_safe_destination(repo_root: Path, destination: Path) -> tuple[Path, Path]:
    """Require the destructive importer target to be exactly repo_root/public."""
    if repo_root.is_symlink():
        raise _fail("destination.repo_symlink", "Repository root may not be a symbolic link.", str(repo_root))
    if destination.is_symlink():
        raise _fail("destination.symlink", "Importer destination may not be a symbolic link.", str(destination))
    root = repo_root.resolve(strict=True)
    expected = root / "public"
    candidate = destination.resolve(strict=False)
    if candidate != expected:
        raise _fail("destination.invalid", "Importer destination must be exactly the repository public/ directory.", str(destination))
    return root, expected
