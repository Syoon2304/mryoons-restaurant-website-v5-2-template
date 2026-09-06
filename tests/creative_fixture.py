from __future__ import annotations

import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OVERLAY = REPO_ROOT / "tests" / "fixtures" / "creative-freedom-site"
BASE_ICONS = REPO_ROOT / "tests" / "fixtures" / "valid-site" / "assets" / "icons"


def build_creative_site(target: Path) -> Path:
    """Build the text-owned creative fixture and add the validated binary icon family."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(OVERLAY, target)
    shutil.copytree(BASE_ICONS, target / "assets" / "icons")
    return target
