# Build on Windows x64 with the pinned Python 3.12 environment.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
import PySide6
import os
import sys

root = Path(SPECPATH).parent
# Some host runtimes inject native-tool PATH entries at Python process startup,
# after PowerShell supplied its environment. Restrict it again inside Python.
os.environ["PATH"] = os.pathsep.join([
    str(root / ".venv/Scripts"), str(Path(os.environ["SYSTEMROOT"]) / "System32"),
    os.environ["SYSTEMROOT"],
])
qt_dir = Path(PySide6.__file__).parent
# Qt and WinRT wheels carry different MSVC redistributable versions. Use one
# consistent, newer set for the entire process, including the Python runtime.
crt_names = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
             "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
crt_files = [(str(qt_dir / name), ".") for name in crt_names]
a = Analysis(
    [str(root / "packaging" / "windows_entry.py")],
    pathex=[str(root / "src")],
    binaries=crt_files,
    datas=[
        (str(root / "THIRD_PARTY_NOTICES.md"), "licenses"),
        (str(root / "THIRD_PARTY_LICENSES"), "licenses/upstream"),
        (str(root / "artifacts/package-licenses"), "licenses/dependencies"),
        (str(root / "README.md"), "help"),
        (str(root / "LICENSE"), "licenses"),
        (str(root / "docs/REFERENCE.md"), "help/docs"),
        (str(root / "docs/SECURITY.md"), "help/docs"),
        (str(root / "docs/SECURITY_REVIEW.md"), "help/docs"),
    ],
    hiddenimports=collect_submodules("winrt") + [
        "bleak.backends.winrt.client", "bleak.backends.winrt.scanner",
        "matplotlib.backends.backend_agg",
    ],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtWebEngineQuick", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in crt_names]
a.binaries += [(name, str(qt_dir / name), "BINARY") for name in crt_names]
allowed_sources = [root / ".venv", Path(sys.base_prefix), Path(os.environ["SYSTEMROOT"])]
unexpected = [entry[0] for entry in a.binaries
              if not any(Path(entry[1]).resolve().is_relative_to(folder.resolve())
                         for folder in allowed_sources)]
if unexpected:
    raise RuntimeError("Unapproved native dependency source: " + ", ".join(unexpected))
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="OpenDP3", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=True,
    icon=str(root / "packaging/OpenDP3.ico"),
    version=str(root / "packaging/version_info.txt"),
)
