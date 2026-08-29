import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_site import validate_json_schema  # noqa: E402


class WorkflowContractTests(unittest.TestCase):
    def test_rollback_detects_the_staged_restore_against_head(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "rollback-website.yml").read_text(encoding="utf-8")
        self.assertIn("git diff --quiet HEAD -- public", workflow)
        self.assertNotIn("git diff --quiet -- public", workflow)

    def test_safe_legacy_url_handoff_matches_its_closed_schema(self) -> None:
        schema = json.loads((ROOT / "infrastructure" / "legacy-url-plan.schema.json").read_text(encoding="utf-8"))
        handoff = json.loads((ROOT / "handoff" / "LEGACY_URL_PLAN.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_json_schema(handoff, schema), [])

    def test_public_contract_versions_are_v5_2(self) -> None:
        manifest = json.loads((ROOT / "public" / "site-manifest.json").read_text(encoding="utf-8"))
        version = json.loads((ROOT / "public" / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "2.1")
        self.assertEqual(manifest["workflow_version"], "5.2")
        self.assertEqual(manifest["repository_package_spec"], "5.2")
        self.assertEqual(version["workflow_version"], "5.2")
        self.assertEqual(version["repository_package_spec"], "5.2")


if __name__ == "__main__":
    unittest.main()
