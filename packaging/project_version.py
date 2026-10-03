"""Read the project version straight from its single source.

Parsed with ast rather than imported: build tooling must be able to read the
version from a source tree that is not installed, and importing the package to
learn its version is a needless way for a build to fail.

Run as a script to print the version, which is how build.ps1 consumes it.
"""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "opendp3" / "__init__.py"


def read(name: str = "__version__", source: Path | None = None) -> str:
    """Return the string assigned to ``name`` at module level in the source."""
    path = source or SOURCE
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    raise ValueError(f"{name} in {path} is not a plain string literal.")
                return node.value.value
    raise ValueError(f"{name} not found in {path}.")


def file_version_tuple(version: str) -> tuple:
    """Windows VERSIONINFO wants four integers; semver gives three."""
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Expected a MAJOR.MINOR.PATCH version, got {version!r}.")
    return tuple(int(part) for part in parts) + (0,)


if __name__ == "__main__":
    print(read())
