"""Bump the application version. The only thing that should edit __version__.

    python scripts/bump_version.py patch      e.g. 1.4.2 -> 1.4.3
    python scripts/bump_version.py minor      e.g. 1.4.2 -> 1.5.0
    python scripts/bump_version.py major      e.g. 1.4.2 -> 2.0.0
    python scripts/bump_version.py 2.0.0      explicit target
    python scripts/bump_version.py patch --dry-run

Examples above use placeholder numbers on purpose: a literal current version in
this file would be one more place to forget to update.

Touches exactly one line in src/openpowerstation/__init__.py. Everything else — the
packaging metadata, the Windows version resource, the release filename and the
verification assertions — derives from it at build time.

DECODER_SCHEMA_VERSION is deliberately out of reach: it is recorded into every
session and evidence export, so it moves only when decoding behaviour changes,
and never as a side effect of shipping a release.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "openpowerstation" / "__init__.py"
PATTERN = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)
PARTS = {"major": 0, "minor": 1, "patch": 2}


def bump(current: tuple, kind: str) -> tuple:
    index = PARTS[kind]
    numbers = list(current)
    numbers[index] += 1
    for after in range(index + 1, 3):
        numbers[after] = 0
    return tuple(numbers)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("level", help="major, minor, patch, or an explicit MAJOR.MINOR.PATCH")
    parser.add_argument("--dry-run", action="store_true", help="print the change without writing")
    args = parser.parse_args(argv)

    text = SOURCE.read_text("utf-8")
    match = PATTERN.search(text)
    if not match:
        print(f"Could not find a __version__ assignment in {SOURCE}.", file=sys.stderr)
        return 1
    current = tuple(int(part) for part in match.groups())

    if args.level in PARTS:
        new = bump(current, args.level)
    else:
        explicit = args.level.split(".")
        if len(explicit) != 3 or not all(part.isdigit() for part in explicit):
            print(f"Expected major/minor/patch or MAJOR.MINOR.PATCH, got {args.level!r}.",
                  file=sys.stderr)
            return 1
        new = tuple(int(part) for part in explicit)

    old_text, new_text = ".".join(map(str, current)), ".".join(map(str, new))
    if new <= current:
        print(f"Refusing to move {old_text} backwards or sideways to {new_text}.", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"{old_text} -> {new_text} (dry run, nothing written)")
        return 0
    SOURCE.write_text(PATTERN.sub(f'__version__ = "{new_text}"', text, count=1), encoding="utf-8")
    print(f"{old_text} -> {new_text}")
    print(f"Next: .\\packaging\\release.ps1   (builds OpenPowerstation-{new_text}.exe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
