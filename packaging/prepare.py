"""Generate the icon, the Windows version resource, and dependency notices.

Never copies local data. The version resource is generated rather than tracked so
the application version has exactly one source: src/openpowerstation/__init__.py.

The dependency notices come from the lock, not from whatever happens to be
installed: requirements.lock for the runtime, and packaging/requirements-build.txt
for PyInstaller, whose bootloader is part of the executable. The executable
bundles what is installed, so an environment that differs from those pins is
refused rather than shipped with notices for something else. The notices folder
is rebuilt on every run, so a package dropped from the lock takes its notice
with it.
"""
from importlib.metadata import distributions
from pathlib import Path
import json
import re
import shutil
import sys
from PIL import Image, ImageDraw

from project_version import file_version_tuple, read as read_version

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "artifacts/package-licenses"
LOCKS = (ROOT / "requirements.lock", ROOT / "packaging/requirements-build.txt")
# Never bundled, so they ship no notice: the test suite, the build tooling other
# than PyInstaller, pip, and the editable openpowerstation install, which needs no pin.
SKIP = {"openpowerstation", "pytest", "pytest-asyncio", "pytest-qt",
        "pyinstaller-hooks-contrib", "setuptools", "pip", "altgraph", "pefile",
        "pywin32-ctypes", "iniconfig", "pluggy"}
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")
INCLUDE = re.compile(r"(?:-r|--requirement)\s+(\S+)")


class LockMismatch(Exception):
    """The lock and the environment cannot produce notices that match the build."""


def canonical(name: str) -> str:
    """PEP 503 normalisation, so `PySide6_Addons` and `pyside6-addons` agree."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path, pins: dict | None = None) -> dict:
    """Collect canonical name -> version from a requirements file, following -r.

    Anything but an exact pin is refused: with a range, the notices would depend
    on what pip happened to pick.
    """
    pins = {} if pins is None else pins
    for number, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
        text = line.split("#", 1)[0].strip()
        include, pin = INCLUDE.fullmatch(text), PIN.fullmatch(text)
        if include:
            read_pins(path.parent / include[1], pins)
        elif pin:
            name = canonical(pin[1])
            if pins.setdefault(name, pin[2]) != pin[2]:
                raise LockMismatch(f"{path.name}:{number}: {pin[1]} is pinned to both "
                                   f"{pins[name]} and {pin[2]}")
        elif text:
            raise LockMismatch(f"{path.name}:{number}: {text} is not an exact name==version pin")
    return pins


def locked_pins() -> dict:
    pins = {}
    for path in LOCKS:
        read_pins(path, pins)
    return pins


def installed() -> dict:
    """Canonical name -> the distribution an import loads: the first on sys.path."""
    found = {}
    for dist in distributions():
        if dist.metadata["Name"]:
            found.setdefault(canonical(dist.metadata["Name"]), dist)
    return found


def shipped(pins: dict, environment: dict) -> list:
    """The distributions to write notices for, once the environment is the lock."""
    wanted = {name: version for name, version in pins.items() if name not in SKIP}
    problems = [f"pinned but not installed: {name}=={version}"
                for name, version in sorted(wanted.items()) if name not in environment]
    problems += [f"installed at {environment[name].version}, pinned {version}: {name}"
                 for name, version in sorted(wanted.items())
                 if name in environment and environment[name].version != version]
    problems += [f"installed but not pinned: {dist.metadata['Name']}=={dist.version}"
                 for name, dist in sorted(environment.items())
                 if name not in pins and name not in SKIP]
    if problems:
        raise LockMismatch(
            "the environment does not match the lock, so the executable would not "
            "bundle what the notices list:\n  " + "\n  ".join(problems) + "\nRebuild "
            ".venv from requirements.lock and packaging/requirements-build.txt "
            "(docs/PACKAGING.md, Rebuild).")
    return sorted((environment[name] for name in wanted),
                  key=lambda d: d.metadata["Name"].lower())


def write_notices(folder: Path, dists: list) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    index = []
    for dist in dists:
        name = dist.metadata["Name"]
        entry = {"name": name, "version": dist.version,
                 "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See package metadata/notices",
                 "project_urls": dist.metadata.get_all("Project-URL") or [],
                 "files": []}
        for relative in dist.files or []:
            if any(term in relative.name.lower() for term in ("license", "copying", "notice")):
                source = Path(dist.locate_file(relative))
                if not source.is_file() or source.suffix in {".pyc", ".pyd", ".dll"}:
                    continue
                target = folder / name / str(relative).replace("../", "").replace("..\\", "")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                entry["files"].append(str(target.relative_to(folder)))
        index.append(entry)
    (folder / "DEPENDENCIES.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    # Python's license lives next to its runtime executable/base prefix.
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, folder / "Python-LICENSE.txt")


def draw_icon() -> Image.Image:
    canvas = Image.new("RGBA", (256, 256), "#101923")
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((34, 54, 218, 210), radius=30, fill="#172b38", outline="#67ddbe", width=12)
    d.rounded_rectangle((91, 30, 165, 55), radius=10, fill="#67ddbe")
    d.line([(55,139),(83,139),(104,97),(129,173),(153,122),(178,139),(199,139)], fill="#67ddbe", width=12, joint="curve")
    return canvas


def version_resource(version: str) -> str:
    """Windows VERSIONINFO, generated from the single version source."""
    numeric = "(" + ",".join(str(n) for n in file_version_tuple(version)) + ")"
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeric}, prodvers={numeric}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'OpenPowerstation local flight recorder'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'OpenPowerstation'),
      StringStruct('OriginalFilename', 'OpenPowerstation.exe'),
      StringStruct('ProductName', 'OpenPowerstation'),
      StringStruct('ProductVersion', '{version}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ]
)
"""


def main() -> int:
    # Cleared before anything can be refused, so a failed run leaves no notices
    # from an earlier environment behind for a build to bundle.
    if NOTICES.exists():
        shutil.rmtree(NOTICES)
    try:
        dists = shipped(locked_pins(), installed())
    except LockMismatch as error:
        print(f"prepare.py: {error}", file=sys.stderr)
        return 1
    write_notices(NOTICES, dists)
    version = read_version()
    (ROOT / "packaging/version_info.txt").write_text(version_resource(version), encoding="ascii")
    draw_icon().save(ROOT / "packaging/OpenPowerstation.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"Prepared icon, version resource {version}, and dependency notices; "
          "no user configuration or recordings included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
