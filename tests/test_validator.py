from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_site import validate_site  # noqa: E402


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "site"
        shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "valid-site", self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self):
        return validate_site(self.root, mode="production", repo_root=REPO_ROOT)

    def test_known_good_restaurant_site_passes(self) -> None:
        report = self.validate()
        self.assertTrue(report.passed, report.to_markdown())

    def test_missing_index_fails(self) -> None:
        (self.root / "index.html").unlink()
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("site.index", {f.code for f in report.errors})

    def test_secret_like_value_fails(self) -> None:
        (self.root / "assets" / "js" / "leak.js").write_text(
            'const api_key = "this-is-a-realistic-secret-value-123456";\n', encoding="utf-8"
        )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("secret.generic_assignment", {f.code for f in report.errors})

    def test_placeholder_content_fails(self) -> None:
        path = self.root / "menu.html"
        path.write_text(path.read_text(encoding="utf-8").replace("Acceptance test menu", "TODO menu"), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("placeholder.todo", {f.code for f in report.errors})

    def test_broken_internal_link_fails(self) -> None:
        path = self.root / "index.html"
        path.write_text(path.read_text(encoding="utf-8").replace('href="/menu.html"', 'href="/missing-menu.html"', 1), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("link.broken", {f.code for f in report.errors})

    def test_bundled_video_fails(self) -> None:
        (self.root / "assets" / "hero.mp4").write_bytes(b"video")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("media.video_bundled", {f.code for f in report.errors})


    def test_csp_requires_media_src(self) -> None:
        path = self.root / "_headers"
        path.write_text(path.read_text(encoding="utf-8").replace("media-src 'self' blob:; ", ""), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("headers.csp_directive", {f.code for f in report.errors})

    def test_external_direct_video_rejects_private_host_and_missing_poster(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        url = "https://drive.google.com/file/d/private-video/view"
        manifest["external_media"] = [{
            "id": "private-video",
            "kind": "direct-video",
            "url": url,
            "page": "index.html",
            "purpose": "Test invalid private media host",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        index = self.root / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("</body>", f'<video muted><source src="{url}" type="video/mp4"></video></body>'), encoding="utf-8")
        report = self.validate()
        codes = {f.code for f in report.errors}
        self.assertIn("media.private_host", codes)
        self.assertIn("media.poster_required", codes)

    def test_valid_external_direct_video_contract_passes(self) -> None:
        poster = self.root / "assets" / "images" / "hero-video-poster.webp"
        poster.write_bytes(b"test-poster")
        url = "https://media.harborlanterntest.test/hero-loop-v1.mp4"
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["external_media"] = [{
            "id": "hero-loop",
            "kind": "direct-video",
            "url": url,
            "page": "index.html",
            "purpose": "Muted decorative test loop",
            "poster": "assets/images/hero-video-poster.webp",
            "accessibility": "Decorative and muted; visible text conveys the message.",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        headers = self.root / "_headers"
        headers.write_text(
            headers.read_text(encoding="utf-8").replace(
                "media-src 'self' blob:;",
                "media-src 'self' blob: https://media.harborlanterntest.test;",
            ),
            encoding="utf-8",
        )
        index = self.root / "index.html"
        markup = f'<video autoplay muted loop playsinline poster="/assets/images/hero-video-poster.webp"><source src="{url}" type="video/mp4"></video>'
        index.write_text(index.read_text(encoding="utf-8").replace("</main>", markup + "</main>"), encoding="utf-8")
        report = self.validate()
        self.assertTrue(report.passed, report.to_markdown())

    def test_stream_kind_rejects_unapproved_host_and_csp_gap(self) -> None:
        url = "https://unapproved-video-host.test/embed/123"
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["external_media"] = [{
            "id": "story",
            "kind": "cloudflare-stream",
            "url": url,
            "page": "index.html",
            "purpose": "Test invalid Stream host",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        index = self.root / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("</main>", f'<iframe src="{url}" title="Test story"></iframe></main>'), encoding="utf-8")
        report = self.validate()
        codes = {f.code for f in report.errors}
        self.assertIn("media.host", codes)
        self.assertIn("media.csp", codes)

    def test_manifest_package_version_must_match(self) -> None:
        version_path = self.root / "version.json"
        version = json.loads(version_path.read_text(encoding="utf-8"))
        version["package_version"] = "mismatch"
        version_path.write_text(json.dumps(version), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertIn("version.mismatch", {f.code for f in report.errors})

    def test_internal_file_symlink_is_rejected_without_following_it(self) -> None:
        outside = Path(self.temp.name) / "outside-secret.txt"
        outside.write_text('api_key = "outside-secret-that-must-not-be-read"', encoding="utf-8")
        (self.root / "assets" / "images" / "escape.txt").symlink_to(outside)
        report = self.validate()
        self.assertIn("filesystem.symlink", {finding.code for finding in report.errors})
        self.assertNotIn("secret.generic_assignment", {finding.code for finding in report.errors})

    def test_directory_symlink_and_root_symlink_are_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "leak.txt").write_text("private bytes", encoding="utf-8")
        (self.root / "assets" / "escape-dir").symlink_to(outside, target_is_directory=True)
        report = self.validate()
        self.assertIn("filesystem.symlink", {finding.code for finding in report.errors})

        alias = Path(self.temp.name) / "site-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        alias_report = validate_site(alias, mode="production", repo_root=REPO_ROOT)
        self.assertIn("filesystem.symlink", {finding.code for finding in alias_report.errors})

    def test_hard_link_is_rejected(self) -> None:
        source = self.root / "assets" / "js" / "site.js"
        os.link(source, self.root / "assets" / "js" / "duplicate.js")
        report = self.validate()
        self.assertIn("filesystem.hardlink", {finding.code for finding in report.errors})

    def test_manifest_network_path_is_rejected(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pages"][0]["file_path"] = "//attacker.test/index.html"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        report = self.validate()
        self.assertIn("manifest.page_path", {finding.code for finding in report.errors})

    def test_manifest_and_version_reject_unknown_or_drifting_contract_fields(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["private_owner_notes"] = "must not publish"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        version_path = self.root / "version.json"
        version = json.loads(version_path.read_text(encoding="utf-8"))
        version["workflow_version"] = "5.0"
        version["unexpected"] = True
        version_path.write_text(json.dumps(version, indent=2) + "\n", encoding="utf-8")
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("manifest.schema", codes)
        self.assertIn("version.mismatch", codes)
        self.assertIn("version.unexpected", codes)

    def test_unlisted_html_and_wrong_origin_canonical_are_rejected(self) -> None:
        extra = self.root / "secret-preview.html"
        shutil.copy2(self.root / "privacy.html", extra)
        index = self.root / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                "https://restaurant-v5-2-test.pages.dev/",
                "https://attacker.test/",
                1,
            ),
            encoding="utf-8",
        )
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("manifest.page_unlisted", codes)
        self.assertIn("seo.canonical_mismatch", codes)

    def test_broad_or_undeclared_csp_host_is_rejected(self) -> None:
        headers = self.root / "_headers"
        headers.write_text(
            headers.read_text(encoding="utf-8").replace(
                "img-src 'self' data:;",
                "img-src 'self' data: https: https://images.attacker.test;",
            ),
            encoding="utf-8",
        )
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("headers.csp_broad_source", codes)
        self.assertIn("headers.csp_undeclared_host", codes)

    def test_private_business_record_filename_and_content_are_rejected(self) -> None:
        (self.root / "assets" / "business-record.json").write_text("{}", encoding="utf-8")
        script = self.root / "assets" / "js" / "site.js"
        script.write_text(script.read_text(encoding="utf-8") + "\n// STRUCTURED BUSINESS DATA\n", encoding="utf-8")
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("privacy.filename", codes)
        self.assertIn("privacy.business_record", codes)

    def test_invalid_javascript_is_rejected(self) -> None:
        script = self.root / "assets" / "js" / "site.js"
        script.write_text("function broken( {\n", encoding="utf-8")
        report = self.validate()
        self.assertIn("javascript.syntax", {finding.code for finding in report.errors})

    def test_turnstile_widget_action_must_match_manifest(self) -> None:
        page = self.root / "contact.html"
        page.write_text(page.read_text(encoding="utf-8").replace('data-action="contact_submit"', 'data-action="wrong_action"'), encoding="utf-8")
        report = self.validate()
        self.assertIn("form.turnstile_widget", {finding.code for finding in report.errors})

    def test_public_pdf_requires_manifest_declaration_and_owner_approval(self) -> None:
        document = self.root / "assets" / "menu-download.pdf"
        document.write_bytes(b"%PDF-1.4\nfixture\n")
        report = self.validate()
        self.assertIn("document.undeclared", {finding.code for finding in report.errors})

        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["public_documents"] = [{
            "id": "menu-download",
            "path": "assets/menu-download.pdf",
            "page": "menu.html",
            "purpose": "Accessible secondary menu download",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        menu = self.root / "menu.html"
        menu.write_text(menu.read_text(encoding="utf-8").replace("</main>", '<a href="/assets/menu-download.pdf">Download menu PDF</a></main>'), encoding="utf-8")
        report = self.validate()
        self.assertTrue(report.passed, report.to_markdown())

    def test_page_file_path_and_public_url_path_are_independent(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        menu_page = next(page for page in manifest["pages"] if page["file_path"] == "menu.html")
        menu_page["url_path"] = "/food/"
        menu_page["canonical_url_path"] = "/food/"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        for page_path in self.root.glob("*.html"):
            text = page_path.read_text(encoding="utf-8").replace('href="/menu.html"', 'href="/food/"')
            if page_path.name == "menu.html":
                text = text.replace(
                    "https://restaurant-v5-2-test.pages.dev/menu.html",
                    "https://restaurant-v5-2-test.pages.dev/food/",
                )
            page_path.write_text(text, encoding="utf-8")
        sitemap = self.root / "sitemap.xml"
        sitemap.write_text(
            sitemap.read_text(encoding="utf-8").replace(
                "https://restaurant-v5-2-test.pages.dev/menu.html",
                "https://restaurant-v5-2-test.pages.dev/food/",
            ),
            encoding="utf-8",
        )
        (self.root / "_redirects").write_text("/food/ /menu.html 200\n", encoding="utf-8")
        report = self.validate()
        self.assertTrue(report.passed, report.to_markdown())

    def test_decoupled_public_url_requires_an_exact_internal_rewrite(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        menu_page = next(page for page in manifest["pages"] if page["file_path"] == "menu.html")
        menu_page["url_path"] = "/food/"
        menu_page["canonical_url_path"] = "/food/"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        report = self.validate()
        self.assertIn("manifest.page_route", {finding.code for finding in report.errors})

    def test_rebuilt_legacy_url_must_have_a_matching_live_page(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["legacy_routes"] = [{
            "source_url_path": "/seasonal-specials/",
            "handling": "rebuild-same-url",
            "destination_url_path": "/seasonal-specials/",
            "purpose": "Preserve an advertised seasonal-specials page.",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        report = self.validate()
        self.assertIn("legacy.coverage", {finding.code for finding in report.errors})

    def test_exact_owner_approved_legacy_redirect_passes(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["legacy_routes"] = [{
            "source_url_path": "/old-menu",
            "handling": "permanent-redirect",
            "destination_url_path": "/menu.html",
            "purpose": "Preserve old menu bookmarks.",
            "owner_approved": True,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (self.root / "_redirects").write_text("/old-menu /menu.html 301\n", encoding="utf-8")
        report = self.validate()
        self.assertTrue(report.passed, report.to_markdown())

    def test_unapproved_or_undeclared_redirects_fail(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["legacy_routes"] = [{
            "source_url_path": "/old-menu",
            "handling": "permanent-redirect",
            "destination_url_path": "/menu.html",
            "purpose": "Preserve old menu bookmarks.",
            "owner_approved": False,
        }]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (self.root / "_redirects").write_text("/old-menu /menu.html 301\n/unlisted /privacy.html 301\n", encoding="utf-8")
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("legacy.unapproved", codes)
        self.assertIn("legacy.redirect_unapproved", codes)

    def test_blanket_home_redirect_and_redirect_chain_fail(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["legacy_routes"] = [
            {
                "source_url_path": "/old-one",
                "handling": "permanent-redirect",
                "destination_url_path": "/old-two",
                "purpose": "First old route.",
                "owner_approved": True,
            },
            {
                "source_url_path": "/old-two",
                "handling": "permanent-redirect",
                "destination_url_path": "/menu.html",
                "purpose": "Second old route.",
                "owner_approved": True,
            },
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (self.root / "_redirects").write_text(
            "/* / 301\n/old-one /old-two 301\n/old-two /menu.html 301\n",
            encoding="utf-8",
        )
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("legacy.redirect_blanket_home", codes)
        self.assertIn("legacy.redirect_chain", codes)

    def test_redirect_loop_fails(self) -> None:
        manifest_path = self.root / "site-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["legacy_routes"] = [
            {"source_url_path": "/old-a", "handling": "permanent-redirect", "destination_url_path": "/old-b", "purpose": "Old A.", "owner_approved": True},
            {"source_url_path": "/old-b", "handling": "permanent-redirect", "destination_url_path": "/old-a", "purpose": "Old B.", "owner_approved": True},
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (self.root / "_redirects").write_text("/old-a /old-b 301\n/old-b /old-a 301\n", encoding="utf-8")
        report = self.validate()
        self.assertIn("legacy.redirect_loop", {finding.code for finding in report.errors})

    def test_icon_family_files_head_links_and_webmanifest_are_enforced(self) -> None:
        (self.root / "assets" / "icons" / "icon-512.png").unlink()
        index = self.root / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace('<link rel="manifest" href="/site.webmanifest">', ""), encoding="utf-8")
        webmanifest = self.root / "site.webmanifest"
        data = json.loads(webmanifest.read_text(encoding="utf-8"))
        data["icons"] = data["icons"][:1]
        webmanifest.write_text(json.dumps(data), encoding="utf-8")
        report = self.validate()
        codes = {finding.code for finding in report.errors}
        self.assertIn("icon.missing", codes)
        self.assertIn("icon.png_dimensions", codes)
        self.assertIn("icon.head_links", codes)
        self.assertIn("icon.webmanifest_icons", codes)

    def test_favicon_ico_must_contain_16_32_and_48_sizes(self) -> None:
        (self.root / "assets" / "icons" / "favicon.ico").write_bytes(b"not-an-icon")
        report = self.validate()
        self.assertIn("icon.ico_invalid", {finding.code for finding in report.errors})


if __name__ == "__main__":
    unittest.main()
