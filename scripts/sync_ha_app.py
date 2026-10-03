"""Copy the OpenPowerstation sources into the Home Assistant app's build context.

Home Assistant builds an app from its own folder only, so the package has to
sit inside ``home-assistant/opendp3/`` to be built. The copy is committed, and
this script is the only thing that writes it:

    python scripts/sync_ha_app.py            rewrite the copy
    python scripts/sync_ha_app.py --check    exit 1 if the copy has drifted

tests/test_ha_app_sync.py runs the check, so a source change that is not
mirrored fails the suite instead of shipping a stale app.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "home-assistant" / "opendp3"
COPIED = ["src", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LICENSES", "LICENSE"]
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")


TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".proto", ".pyi"}


def read_normalized(path: Path) -> bytes:
    """File bytes with CRLF folded to LF for text files.

    A Windows checkout with autocrlf rewrites line endings, which must neither
    count as drift nor change the recorded source digest.
    """
    data = path.read_bytes()
    if path.suffix in TEXT_SUFFIXES or not path.suffix:
        return data.replace(b"\r\n", b"\n")
    return data


def tree_files(base: Path) -> dict[str, bytes]:
    """Relative path -> bytes for every file under ``base`` that would be copied."""
    if base.is_file():
        return {base.name: read_normalized(base)}
    found = {}
    # Order by the POSIX path string, not by Path: Windows compares paths
    # case-insensitively part by part, which gave a different digest per OS.
    for path in sorted(base.rglob("*"), key=lambda path: path.relative_to(base).as_posix()):
        relative = path.relative_to(base)
        if path.is_file() and not any(part == "__pycache__" or part.endswith(".egg-info")
                                      for part in relative.parts) and path.suffix != ".pyc":
            found[relative.as_posix()] = read_normalized(path)
    return found


def source_digest() -> str:
    digest = hashlib.sha256()
    for name in COPIED:
        for relative, data in tree_files(ROOT / name).items():
            digest.update(f"{name}/{relative}\0".encode() + hashlib.sha256(data).digest())
    return digest.hexdigest()


def provenance() -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    import opendp3
    return {"repository": "FritzK-25/OpenPowerstation", "version": opendp3.__version__,
            "source_sha256": source_digest()}


def drift() -> list[str]:
    problems = []
    for name in COPIED:
        if tree_files(ROOT / name) != tree_files(APP / name):
            problems.append(name)
    recorded = APP / "SOURCE-PROVENANCE.json"
    if not recorded.exists() or json.loads(recorded.read_text("utf-8")) != provenance():
        problems.append("SOURCE-PROVENANCE.json")
    return problems


def sync() -> None:
    for name in COPIED:
        source, target = ROOT / name, APP / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if source.is_dir():
            shutil.copytree(source, target, ignore=IGNORED)
        else:
            shutil.copy2(source, target)
    (APP / "SOURCE-PROVENANCE.json").write_text(
        json.dumps(provenance(), indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift instead of fixing it")
    args = parser.parse_args(argv)
    if args.check:
        problems = drift()
        if problems:
            print("home-assistant/opendp3 is out of date: " + ", ".join(problems)
                  + ". Run: python scripts/sync_ha_app.py", file=sys.stderr)
            return 1
        print("home-assistant/opendp3 matches the sources.")
        return 0
    sync()
    print("Synced home-assistant/opendp3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
