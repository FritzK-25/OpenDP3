"""Run only the copied executable with a clean import environment; no hardware I/O."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import CodeType
from PyInstaller.archive.readers import CArchiveReader

from project_version import read as read_source

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--executable", type=Path, default=root / "OpenPowerstation.exe")
source = parser.parse_args().executable.resolve()
workspace = Path(tempfile.mkdtemp(prefix="standalone-", dir=root / "artifacts"))
executable = workspace / "OpenPowerstation.exe"
shutil.copyfile(source, executable)
archive = CArchiveReader(str(executable))
assert not any(name.lower().endswith((".sqlite", ".sqlite-wal", ".sqlite-shm"))
               or Path(name).name.lower() == "config.json" for name in archive.toc)
pyz = archive.open_embedded_archive("PYZ.pyz")
encryption = pyz.extract("openpowerstation.vendor.encryption")


def compiled_names(code):
    """Inspect this build's module without importing or executing its code."""
    assert isinstance(code, CodeType)
    names = set(code.co_names)
    for value in code.co_consts:
        if isinstance(value, CodeType):
            names.add(value.co_name)
            names.update(compiled_names(value))
        elif isinstance(value, str):
            assert "BEGIN PUBLIC KEY" not in value
    return names


names = compiled_names(encryption)
assert {"Type1Encryption", "Type7Encryption", "EncryptionStrategy"} <= names
assert not {"Session", "_developer_pubkey", "_counter_nonce"} & names
environment = os.environ.copy()
for key in list(environment):
    if key.upper() in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PATH"}:
        environment.pop(key)
environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
out = workspace / "result"
completed = subprocess.run([str(executable), "--smoke-test", str(out)],
                           cwd=workspace, env=environment, timeout=90,
                           creationflags=subprocess.CREATE_NO_WINDOW)
assert completed.returncode == 0, f"Frozen smoke test failed; inspect {out / 'result.json'}"
result = json.loads((out / "result.json").read_text("utf-8"))
assert result["ok"] and result["frozen"]
# Expected values come from the source tree, so a version bump never leaves a
# stale literal here to be discovered by a failing release.
assert result["app_version"] == read_source("__version__")
assert result["decoder_version"] == ("dp3-mr521/" + read_source("DECODER_SCHEMA_VERSION")
                                    + "+" + read_source("UPSTREAM_REVISION")[:12])
assert result["desktop_frames"] == 882 and result["replay"]["mismatches"] == 0
assert all("_MEI" in path for path in result["python_paths"])
assert "_MEI" in result["module_file"]
(root / "artifacts/packaging-verification.json").write_text(
    json.dumps({"ok": True, "smoke_result": str(out / "result.json"),
                "executable": str(source), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "app_version": result["app_version"], "decoder_version": result["decoder_version"],
                "developer_diagnostics_crypto_absent": True,
                "isolated_python_paths": True, "private_files_not_bundled": True,
                "adapter": result["adapter"]}, indent=2), encoding="utf-8")
print(json.dumps({"ok": True, "output": str(out)}, indent=2))
