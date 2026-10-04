"""docs/PROTOCOL_SCHEMA.md is generated, so it must match what the generator renders.

scripts/generate_protocol_reference.py has a --check mode that nothing ran. A
descriptor or field-map change could therefore leave the published reference
describing a schema the decoder no longer has. Comparing here puts that check in
the suite every OpenPowerstation change already runs.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_protocol_reference.py"
REFERENCE = ROOT / "docs" / "PROTOCOL_SCHEMA.md"


def test_protocol_schema_reference_matches_the_generator(monkeypatch):
    # The generator puts src/ on sys.path when it loads; keep that local.
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("generate_protocol_reference", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert REFERENCE.read_text(encoding="utf-8") == generator.render(), (
        "docs/PROTOCOL_SCHEMA.md is stale; run scripts/generate_protocol_reference.py")
