from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import package_site  # noqa: E402
from import_website_zip import ImportReport, inspect_zip, load_policy  # noqa: E402


class PackagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "valid-site", self.repo / "public")
        shutil.copytree(REPO_ROOT / "functions", self.repo / "functions")
        (self.repo / "infrastructure").mkdir()
        for name in ("importer-policy.json", "site-manifest.schema.json"):
            shutil.copy2(REPO_ROOT / "infrastructure" / name, self.repo / "infrastructure" / name)
        self.output = self.repo / "website.zip"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def package(self) -> int:
        return package_site.main([
            "--source", str(self.repo / "public"),
            "--output", str(self.output),
            "--repo-root", str(self.repo),
        ])

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_deterministic_package_passes_import_policy_and_round_trip(self) -> None:
        self.assertEqual(self.package(), 0)
        first = self.digest(self.output)
        policy = load_policy(self.repo / "infrastructure" / "importer-policy.json")
        entries = inspect_zip(self.output, policy, ImportReport())
        names = [entry.filename for entry in entries]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in entries))
        self.assertFalse(any(name.endswith("/") for name in names))

        self.assertEqual(self.package(), 0)
        self.assertEqual(self.digest(self.output), first)

    def test_symlink_source_is_rejected_and_previous_package_is_preserved(self) -> None:
        self.output.write_bytes(b"previous-package")
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("must not be packaged", encoding="utf-8")
        (self.repo / "public" / "assets" / "escape.txt").symlink_to(outside)
        self.assertEqual(self.package(), 1)
        self.assertEqual(self.output.read_bytes(), b"previous-package")

    def test_source_and_output_boundaries_are_exact(self) -> None:
        other_source = self.repo / "other-public"
        shutil.copytree(self.repo / "public", other_source)
        result = package_site.main([
            "--source", str(other_source),
            "--output", str(self.output),
            "--repo-root", str(self.repo),
        ])
        self.assertEqual(result, 2)

        result = package_site.main([
            "--source", str(self.repo / "public"),
            "--output", str(self.repo / "other.zip"),
            "--repo-root", str(self.repo),
        ])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
