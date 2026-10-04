"""The Home Assistant app builds from its own folder, so it carries a copy of the
sources. That copy must never drift from the real thing."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_ha_app  # noqa: E402


def test_home_assistant_app_copy_matches_the_sources():
    assert sync_ha_app.drift() == [], "run: python scripts/sync_ha_app.py"


def test_home_assistant_app_version_matches_the_package():
    import openpowerstation
    config = (ROOT / "home-assistant" / "opendp3" / "config.yaml").read_text("utf-8")
    assert f'version: "{openpowerstation.__version__}"' in config


def test_source_digest_order_does_not_depend_on_the_platform():
    # The digest hashes files in tree_files() order. Sorting Path objects
    # orders them differently on Windows, so the recorded digest only ever
    # matched one OS; plain string order is the same everywhere.
    files = list(sync_ha_app.tree_files(ROOT / "src"))
    assert files == sorted(files)
