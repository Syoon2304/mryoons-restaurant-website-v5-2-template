#!/usr/bin/env python3
"""Permit starter-mode CI only when public/ exactly matches the reviewed V5.2 shell."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from public_package_policy import load_policy, validate_source_tree  # noqa: E402
from validate_site import tree_digest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("public"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve(strict=True)
    root = args.root.resolve(strict=True)
    policy = load_policy(repo_root / "infrastructure" / "importer-policy.json")
    entries, violations = validate_source_tree(root, policy)
    if violations:
        for violation in violations:
            location = f" ({violation.path})" if violation.path else ""
            print(f"{violation.code}{location}: {violation.message}", file=sys.stderr)
        return 1
    try:
        manifest = json.loads((root / "site-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read starter manifest: {exc}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict) or manifest.get("stage") != "starter":
        print("Starter-tree verification applies only to stage 'starter'.", file=sys.stderr)
        return 1

    digest_path = repo_root / "infrastructure" / "starter-tree.sha256"
    expected = digest_path.read_text(encoding="ascii").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        print("infrastructure/starter-tree.sha256 is invalid.", file=sys.stderr)
        return 1
    actual = tree_digest(entries)
    if actual != expected:
        print("Starter public/ differs from the reviewed V5.2 shell.", file=sys.stderr)
        print(f"expected={expected}", file=sys.stderr)
        print(f"actual={actual}", file=sys.stderr)
        print("Set stage to production and complete all production gates, or have the course creator review and deliberately update the starter digest.", file=sys.stderr)
        return 1
    print(f"Reviewed V5.2 starter tree verified: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
