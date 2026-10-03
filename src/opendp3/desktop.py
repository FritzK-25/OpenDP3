"""Standalone desktop startup and an isolated, offline packaging smoke test."""
import argparse
import json
import os
from pathlib import Path
import sys


def default_root() -> Path:
    from .config import data_dir
    # Reuse this workstation's existing data, but never write to _MEIPASS.
    beside = Path(sys.executable).resolve().parent / "data"
    if getattr(sys, "frozen", False) and beside.is_dir():
        return beside
    return data_dir()


def smoke_test(destination: Path) -> int:
    """No configuration reads, scan, connection, login, or real-device writes."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    destination.mkdir(parents=True, exist_ok=False)
    os.environ["MPLCONFIGDIR"] = str(destination / "matplotlib")
    result = {"frozen": bool(getattr(sys, "frozen", False))}
    app = window = None
    try:
        import asyncio
        import time
        import importlib
        result["qt_imports"] = []
        for module in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
            importlib.import_module(module)
            result["qt_imports"].append(module)
        from PySide6.QtWidgets import QApplication
        from .ble import adapter_status
        from .demo import make_demo
        from .exporting import export_evidence
        from .gui import Window
        from .validation import verify_recording
        from .vendor.encryption import Type7Encryption
        from .protocol import OutboundGate
        from Crypto.Cipher import AES
        import bleak.backends.winrt.client
        import bleak.backends.winrt.scanner
        import ecdsa
        import opendp3
        result["app_version"] = opendp3.__version__
        result["decoder_version"] = opendp3.DECODER_VERSION
        # Load the native crypto extension as well as the Python wrapper.
        AES.new(bytes(16), AES.MODE_CBC, bytes(16)).encrypt(bytes(16))
        # Exercise the ephemeral secp160r1 ECDH that ble.authenticate() performs.
        # Nothing in OpenDP3 signs with ecdsa; its signing timing leak
        # (GHSA-wj6h-64fc-37mp) needs many signatures from one long-term key.
        ours, theirs = (ecdsa.SigningKey.generate(curve=ecdsa.SECP160r1) for _ in range(2))
        assert (ecdsa.ECDH(ecdsa.SECP160r1, ours, theirs.get_verifying_key()).generate_sharedsecret_bytes()
                == ecdsa.ECDH(ecdsa.SECP160r1, theirs, ours.get_verifying_key()).generate_sharedsecret_bytes())
        result["adapter"] = asyncio.run(adapter_status())
        result["module_file"] = opendp3.__file__
        result["python_paths"] = list(sys.path)
        db = make_demo(destination / "synthetic.sqlite")
        result["replay"] = verify_recording(db)
        assert result["replay"]["mismatches"] == 0
        report, _ = export_evidence(db, destination / "export")
        assert report.is_file()
        app = QApplication.instance() or QApplication([])
        window = Window(destination, db)
        window.show()
        deadline = time.monotonic() + 15
        while window.snap.get("count") != 882 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.snap.get("count") == 882, "Desktop did not load synthetic recording"
        assert window.coverage_table.rowCount() > 25
        assert window.badge.text() == "SYNTHETIC DEMO"
        # A blank stylesheet means the theme never applied and the packaged build
        # would ship an unstyled window.
        assert window.styleSheet(), "Theme stylesheet did not apply"
        result["theme"] = window.theme
        assert window.grab().save(str(destination / "desktop.png"))
        result["desktop_frames"] = window.snap["count"]
        result["ok"] = True
    except Exception as exc:
        # No exception messages: future errors could contain device/account values.
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        import traceback
        result["error_module"] = getattr(exc, "name", None)
        if isinstance(exc, ImportError):
            # This isolated test never loads user configuration or device data.
            result["import_error"] = str(exc)
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            result["loaded_dlls"] = {}
            for name in ("python312.dll", "python3.dll", "msvcp140.dll", "vcruntime140.dll",
                         "Qt6Core.dll", "Qt6Gui.dll", "pyside6.abi3.dll", "shiboken6.abi3.dll"):
                handle = kernel.GetModuleHandleW(name)
                if handle:
                    buf = ctypes.create_unicode_buffer(4096)
                    kernel.GetModuleFileNameW(handle,buf,len(buf))
                    result["loaded_dlls"][name] = buf.value
        result["error_frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]
    finally:
        if window is not None:
            window.timer.stop()
            deadline = time.monotonic() + 10
            while window.jobs and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            window.force_exit = True
            window.close()
        (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenDP3 Windows desktop")
    parser.add_argument("database", nargs="?", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_test:
        return smoke_test(args.smoke_test.resolve())
    try:
        from .gui import launch
        return launch(args.data_dir or default_root(), args.database)
    except Exception as exc:
        # Windowed applications have no stderr. Show a credential-free error.
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            "OpenDP3 could not start (" + type(exc).__name__ + ").\n"
            "Check that the selected data folder is writable and has free space.\n"
            "Your recording files have not been deleted.",
            "OpenDP3 startup error", 0x10)
        return 1
