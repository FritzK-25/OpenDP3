"""Install the repository's git hooks.

Hooks live in scripts/hooks/ so they are tracked and reviewable; .git/hooks is
not part of the repository, so each clone has to opt in by running this once.

    .venv/Scripts/python.exe scripts/install_hooks.py
    .venv/Scripts/python.exe scripts/install_hooks.py --uninstall
"""
import argparse
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "hooks"
TARGET = ROOT / ".git" / "hooks"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)

    if not TARGET.is_dir():
        print(f"No git hooks directory at {TARGET}. Is this a git repository?", file=sys.stderr)
        return 1

    for hook in sorted(SOURCE.iterdir()):
        if not hook.is_file():
            continue
        destination = TARGET / hook.name
        if args.uninstall:
            if destination.exists():
                destination.unlink()
                print(f"removed {destination.relative_to(ROOT)}")
            continue
        shutil.copyfile(hook, destination)
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        print(f"installed {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
