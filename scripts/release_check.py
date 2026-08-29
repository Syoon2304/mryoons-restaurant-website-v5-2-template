#!/usr/bin/env python3
"""Run every repository-local release check in one command."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(command, cwd=ROOT, check=True, env=environment)


def main() -> int:
    python = sys.executable
    for path in sorted((ROOT / "scripts").glob("*.py")):
        compile(path.read_bytes(), str(path), "exec")
    print("Compiled Python sources successfully.")
    for path in sorted((ROOT / "infrastructure").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
    print("Parsed infrastructure JSON files successfully.")
    generated = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}
    )
    if generated:
        raise RuntimeError("Generated Python cache files must not ship: " + ", ".join(generated))
    run("Python unit tests", [python, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run("Validate starter public site", [python, "scripts/validate_site.py", "public", "--mode", "starter", "--repo-root", "."])
    run("Verify exact reviewed starter tree", [python, "scripts/verify_starter_tree.py", "public", "--repo-root", "."])
    run("Validate production test fixture", [python, "scripts/validate_site.py", "tests/fixtures/valid-site", "--mode", "production", "--repo-root", "."])
    if shutil.which("node"):
        run("Pages middleware syntax", ["node", "--check", "functions/api/_middleware.js"])
        run("Pages contact syntax", ["node", "--check", "functions/api/contact.js"])
        run("Pages health syntax", ["node", "--check", "functions/api/health.js"])
        run("Pages Functions behavior", ["node", "--test", "tests/contact-function.test.mjs"])
    else:
        print("\nWARNING: Node was not available; Pages Functions checks were skipped.")
    print("\nALL REPOSITORY-LOCAL RELEASE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
