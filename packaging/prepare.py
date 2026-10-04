"""Generate the icon, the Windows version resource, and dependency notices.

Never copies local data. The version resource is generated rather than tracked so
the application version has exactly one source: src/openpowerstation/__init__.py.
"""
from importlib.metadata import distributions
from pathlib import Path
import json
import shutil
from PIL import Image, ImageDraw

from project_version import file_version_tuple, read as read_version

root = Path(__file__).resolve().parent.parent
folder = root / "artifacts/package-licenses"
folder.mkdir(parents=True, exist_ok=True)
skip = {"openpowerstation", "pytest", "pytest-asyncio", "pytest-qt",
        "pyinstaller-hooks-contrib", "setuptools", "pip", "altgraph", "pefile",
        "pywin32-ctypes", "iniconfig", "pluggy"}
index = []
for dist in sorted(distributions(), key=lambda d:d.metadata["Name"].lower()):
    name = dist.metadata["Name"]
    if name.lower() in skip:
        continue
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
import sys
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, folder / "Python-LICENSE.txt")
canvas = Image.new("RGBA", (256, 256), "#101923")
d = ImageDraw.Draw(canvas)
d.rounded_rectangle((34, 54, 218, 210), radius=30, fill="#172b38", outline="#67ddbe", width=12)
d.rounded_rectangle((91, 30, 165, 55), radius=10, fill="#67ddbe")
d.line([(55,139),(83,139),(104,97),(129,173),(153,122),(178,139),(199,139)], fill="#67ddbe", width=12, joint="curve")
# Windows VERSIONINFO, generated from the single version source.
version = read_version()
numeric = "(" + ",".join(str(n) for n in file_version_tuple(version)) + ")"
(root / "packaging/version_info.txt").write_text(f"""VSVersionInfo(
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
""", encoding="ascii")
canvas.save(root / "packaging/OpenPowerstation.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print(f"Prepared icon, version resource {version}, and dependency notices; "
      "no user configuration or recordings included.")
