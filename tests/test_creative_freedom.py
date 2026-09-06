from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
TEST_DIR = REPO_ROOT / "tests"
for path in (SCRIPT_DIR, TEST_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from creative_fixture import build_creative_site  # noqa: E402
from import_website_zip import ImportReport, run_import  # noqa: E402
from validate_site import validate_site  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_zip(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(source).as_posix())


class CreativeFreedomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.site = build_creative_site(self.root / "creative-site")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_radically_different_blank_canvas_site_passes(self) -> None:
        report = validate_site(self.site, mode="production", repo_root=REPO_ROOT)
        self.assertTrue(report.passed, report.to_markdown())
        self.assertEqual(report.warnings, [], report.to_markdown())

        creative_css = self.site / "assets" / "visual" / "aurora.css"
        creative_js = self.site / "assets" / "visual" / "kinetic.mjs"
        self.assertNotEqual(digest(creative_css), digest(REPO_ROOT / "public" / "assets" / "css" / "site.css"))
        self.assertNotEqual(digest(creative_js), digest(REPO_ROOT / "public" / "assets" / "js" / "site.js"))
        self.assertTrue((self.site / "assets" / "fonts" / "neon-current.svg").is_file())
        self.assertTrue((self.site / "assets" / "visual" / "brand-sigil.svg").is_file())
        self.assertTrue((self.site / "experiences" / "after-dark.html").is_file())
        css = creative_css.read_text(encoding="utf-8")
        self.assertIn("@font-face", css)
        self.assertIn("@keyframes", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertFalse((self.site / "setup-status.html").exists())
        self.assertFalse((self.site / "assets" / "css" / "site.css").exists())

    def test_phone_import_replaces_shell_and_preserves_infrastructure(self) -> None:
        repo = self.root / "repo"
        public = repo / "public"
        public.mkdir(parents=True)
        (public / "setup-status.html").write_text("starter-only", encoding="utf-8")
        (public / "assets" / "css").mkdir(parents=True)
        (public / "assets" / "css" / "site.css").write_text("starter-only", encoding="utf-8")

        (repo / "infrastructure").mkdir()
        for name in ("importer-policy.json", "site-manifest.schema.json"):
            shutil.copy2(REPO_ROOT / "infrastructure" / name, repo / "infrastructure" / name)
        shutil.copytree(REPO_ROOT / "functions", repo / "functions")
        protected = repo / ".github" / "workflows" / "protected-marker.yml"
        protected.parent.mkdir(parents=True)
        protected.write_text("name: protected\n", encoding="utf-8")

        package = repo / "website.zip"
        write_zip(self.site, package)
        report = ImportReport()
        run_import(package, public, repo / "infrastructure" / "importer-policy.json", report)

        self.assertTrue(report.passed, report.to_markdown())
        self.assertFalse((public / "setup-status.html").exists())
        self.assertFalse((public / "assets" / "css" / "site.css").exists())
        self.assertTrue((public / "experiences" / "after-dark.html").is_file())
        self.assertTrue((public / "assets" / "fonts" / "neon-current.svg").is_file())
        self.assertEqual(protected.read_text(encoding="utf-8"), "name: protected\n")
        self.assertTrue((repo / "functions" / "api" / "health.js").is_file())


if __name__ == "__main__":
    unittest.main()
