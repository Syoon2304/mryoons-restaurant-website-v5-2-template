from __future__ import annotations

import json
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from import_website_zip import (  # noqa: E402
    ImportRejected,
    ImportReport,
    inspect_zip,
    load_policy,
    run_import,
)


def write_zip_from_directory(source: Path, output: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            rel = path.relative_to(source).as_posix()
            zf.write(path, prefix + rel)


class ImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.public = self.repo / "public"
        self.public.mkdir()
        (self.public / "sentinel.txt").write_text("previous live site", encoding="utf-8")
        (self.repo / "infrastructure").mkdir()
        shutil.copy2(REPO_ROOT / "infrastructure" / "importer-policy.json", self.repo / "infrastructure" / "importer-policy.json")
        shutil.copy2(REPO_ROOT / "infrastructure" / "site-manifest.schema.json", self.repo / "infrastructure" / "site-manifest.schema.json")
        shutil.copytree(REPO_ROOT / "functions", self.repo / "functions")
        self.policy_path = self.repo / "infrastructure" / "importer-policy.json"
        self.policy = load_policy(self.policy_path)
        self.site = self.root / "valid-site"
        shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "valid-site", self.site)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def package(self, prefix: str = "") -> Path:
        output = self.repo / "website.zip"
        write_zip_from_directory(self.site, output, prefix=prefix)
        return output

    def test_valid_package_atomically_replaces_public(self) -> None:
        package = self.package()
        report = ImportReport()
        run_import(package, self.public, self.policy_path, report)
        self.assertTrue(report.passed)
        self.assertTrue((self.public / "index.html").is_file())
        self.assertFalse((self.public / "sentinel.txt").exists())

    def test_invalid_package_preserves_previous_public(self) -> None:
        package = self.package(prefix="restaurant-site/")
        report = ImportReport()
        with self.assertRaises(ImportRejected):
            run_import(package, self.public, self.policy_path, report)
        self.assertEqual((self.public / "sentinel.txt").read_text(encoding="utf-8"), "previous live site")
        self.assertFalse((self.public / "index.html").exists())

    def test_parent_wrapper_folder_is_rejected(self) -> None:
        package = self.package(prefix="restaurant-site/")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "package.index")

    def test_path_traversal_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("../escape.txt", "unsafe")
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.path_traversal")

    def test_symlink_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            link = zipfile.ZipInfo("assets/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(link, "../../outside")
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.symlink")

    def test_special_unix_file_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            special = zipfile.ZipInfo("assets/pipe.txt")
            special.create_system = 3
            special.external_attr = (stat.S_IFIFO | 0o644) << 16
            zf.writestr(special, "not a regular file")
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.special_file")

    def test_case_collision_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
            zf.writestr("assets/Logo.svg", "a")
            zf.writestr("assets/logo.svg", "b")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.case_collision")

    def test_video_file_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
            zf.writestr("assets/hero.mp4", b"video")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.extension")

    def test_suspicious_compression_ratio_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr("index.html", b"0" * 1024 * 1024)
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.compression_ratio")

    def test_secret_in_extracted_site_is_rejected_and_old_site_remains(self) -> None:
        secret = self.site / "assets" / "js" / "secret.js"
        secret.write_text('const access_token = "sensitive-value-with-enough-characters";\n', encoding="utf-8")
        package = self.package()
        report = ImportReport()
        with self.assertRaises(ImportRejected) as caught:
            run_import(package, self.public, self.policy_path, report)
        self.assertEqual(caught.exception.code, "secret.generic_assignment")
        self.assertTrue((self.public / "sentinel.txt").is_file())


    def test_compressed_package_size_limit_is_enforced(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("index.html", "x" * 256)
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        policy = dict(self.policy)
        policy["max_compressed_bytes"] = 100
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, policy, ImportReport())
        self.assertEqual(caught.exception.code, "package.too_large")

    def test_expanded_size_limit_is_enforced(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("index.html", "x" * 256)
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        policy = dict(self.policy)
        policy["max_compressed_bytes"] = 10_000
        policy["max_expanded_bytes"] = 100
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.expanded_size")

    def test_individual_file_size_limit_is_enforced(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("index.html", "x" * 256)
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        policy = dict(self.policy)
        policy["max_compressed_bytes"] = 10_000
        policy["max_expanded_bytes"] = 10_000
        policy["max_single_file_bytes"] = 100
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.single_file_size")

    def test_file_count_limit_is_enforced(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
            zf.writestr("extra.txt", "x")
        policy = dict(self.policy)
        policy["max_compressed_bytes"] = 10_000
        policy["max_file_count"] = 3
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, policy, ImportReport())
        self.assertEqual(caught.exception.code, "zip.file_count")

    def test_nested_archive_is_rejected(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
            zf.writestr("assets/other.zip", b"zip")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertIn(caught.exception.code, {"zip.extension", "zip.nested_archive"})

    def test_noncanonical_and_portability_hostile_paths_are_rejected(self) -> None:
        names = [
            "/absolute.html",
            "//attacker.test/page.html",
            "assets\\windows.html",
            "assets/page name.html",
            "assets/%2e%2e.html",
            "assets/CON.txt",
        ]
        for name in names:
            with self.subTest(name=name):
                package = self.repo / "website.zip"
                with zipfile.ZipFile(package, "w") as zf:
                    zf.writestr("index.html", "safe")
                    zf.writestr("site-manifest.json", "{}")
                    zf.writestr("version.json", "{}")
                    zf.writestr(name, "unsafe")
                with self.assertRaises(ImportRejected):
                    inspect_zip(package, self.policy, ImportReport())

    def test_all_required_root_contract_files_are_enforced(self) -> None:
        package = self.repo / "website.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("index.html", "safe")
            zf.writestr("site-manifest.json", "{}")
            zf.writestr("version.json", "{}")
        with self.assertRaises(ImportRejected) as caught:
            inspect_zip(package, self.policy, ImportReport())
        self.assertEqual(caught.exception.code, "package.required")

    def test_package_and_policy_must_be_protected_repository_files(self) -> None:
        package = self.package()
        outside_package = self.root / "website.zip"
        shutil.copy2(package, outside_package)
        with self.assertRaises(ImportRejected) as caught:
            run_import(outside_package, self.public, self.policy_path, ImportReport())
        self.assertEqual(caught.exception.code, "package.location")

        alternate_policy = self.repo / "alternate-policy.json"
        shutil.copy2(self.policy_path, alternate_policy)
        with self.assertRaises(ImportRejected) as caught:
            run_import(package, self.public, alternate_policy, ImportReport())
        self.assertEqual(caught.exception.code, "policy.location")

    def test_destination_symlink_is_rejected_and_target_is_untouched(self) -> None:
        package = self.package()
        outside = self.root / "outside-public"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("outside", encoding="utf-8")
        shutil.rmtree(self.public)
        self.public.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ImportRejected) as caught:
            run_import(package, self.public, self.policy_path, ImportReport())
        self.assertEqual(caught.exception.code, "destination.symlink")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")

    def test_existing_internal_symlink_is_not_followed_during_comparison(self) -> None:
        package = self.package()
        outside = self.root / "outside-secret.txt"
        outside.write_text("outside stays unchanged", encoding="utf-8")
        (self.public / "escape.txt").symlink_to(outside)
        report = ImportReport()
        run_import(package, self.public, self.policy_path, report)
        self.assertTrue(report.passed)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside stays unchanged")
        self.assertFalse((self.public / "escape.txt").exists())

    def test_package_and_policy_symlinks_are_rejected(self) -> None:
        real_package = self.package()
        moved_package = self.repo / "real-package.zip"
        real_package.rename(moved_package)
        real_package.symlink_to(moved_package)
        with self.assertRaises(ImportRejected) as caught:
            run_import(real_package, self.public, self.policy_path, ImportReport())
        self.assertEqual(caught.exception.code, "package.symlink")

    def test_policy_contract_rejects_unknown_fields_and_boolean_limits(self) -> None:
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["unexpected_override"] = True
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaises(ImportRejected) as caught:
            load_policy(self.policy_path)
        self.assertEqual(caught.exception.code, "policy.unexpected")

        policy.pop("unexpected_override")
        policy["max_file_count"] = True
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaises(ImportRejected) as caught:
            load_policy(self.policy_path)
        self.assertEqual(caught.exception.code, "policy.value")


if __name__ == "__main__":
    unittest.main()
