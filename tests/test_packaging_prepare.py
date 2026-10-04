"""packaging/prepare.py ships the notices for the lock, and only into its own tree.

The executable bundles artifacts/package-licenses wholesale (OpenPowerstation.spec), so
what prepare.py writes there is what Help > Third-party licenses shows. It used
to index every distribution in whichever environment last ran it, and never
cleared the folder. This suite also ran it inside the repository, so a test run
from any virtualenv rewrote the notices the next build would ship: test tools
that are never bundled were listed, a pinned dependency missing from that
environment had none, and a package dropped from the lock kept its notice.

Every run here uses a copy of prepare.py in a throwaway project tree, so the
suite never writes into the repository.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import openpowerstation

ROOT = Path(__file__).resolve().parents[1]
# The generated files a run inside the repository would write.
OUTPUTS = (ROOT / "artifacts/package-licenses", ROOT / "packaging/version_info.txt",
           ROOT / "packaging/OpenPowerstation.ico")
# Snapshot of the running environment, taken by the interpreter prepare.py runs
# under, so both see the same search path. First on sys.path wins, as on import.
FREEZE = """
import json, re
from importlib.metadata import distributions
found = {}
for dist in distributions():
    name = dist.metadata["Name"]
    if name:
        found.setdefault(re.sub(r"[-_.]+", "-", name).lower(), [name, dist.version])
print(json.dumps(sorted(found.values())))
"""
ABSENT = "opendp3-test-package-that-is-not-installed"


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    """Every installed distribution as a [name, version] pin."""
    completed = subprocess.run([sys.executable, "-c", FREEZE],
                               cwd=tmp_path_factory.mktemp("freeze"),
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    pins = json.loads(completed.stdout)
    assert any(canonical(name) == "pillow" for name, _ in pins), "prepare.py draws with Pillow"
    return pins


def project(base, lock, build=()):
    """A minimal copy of the tree prepare.py resolves everything against.

    The lock has the real one's shape: requirements.lock includes the pins.
    """
    root = base / "project"
    (root / "packaging").mkdir(parents=True)
    (root / "src/openpowerstation").mkdir(parents=True)
    for name in ("prepare.py", "project_version.py"):
        shutil.copyfile(ROOT / "packaging" / name, root / "packaging" / name)
    shutil.copyfile(ROOT / "src/openpowerstation/__init__.py", root / "src/openpowerstation/__init__.py")
    (root / "requirements.lock").write_text("# Entry point.\n-r requirements.txt\n",
                                            encoding="utf-8")
    (root / "requirements.txt").write_text("".join(f"{line}\n" for line in lock),
                                           encoding="utf-8")
    (root / "packaging/requirements-build.txt").write_text(
        "".join(f"{line}\n" for line in build), encoding="utf-8")
    return root


def pins(environment, *, drop=(), override=None):
    override = override or {}
    return [f"{name}=={override.get(canonical(name), version)}"
            for name, version in environment if canonical(name) not in drop]


def prepare(root):
    return subprocess.run([sys.executable, str(root / "packaging/prepare.py")], cwd=root,
                          capture_output=True, text=True, timeout=300)


def plant_stale_notice(root):
    stale = root / "artifacts/package-licenses/Retired-Package"
    stale.mkdir(parents=True)
    (stale / "LICENSE").write_text("a package the lock no longer pins\n", encoding="utf-8")
    return stale


def shipped_names(root):
    index = root / "artifacts/package-licenses/DEPENDENCIES.json"
    return {canonical(entry["name"]) for entry in json.loads(index.read_text("utf-8"))}


def snapshot(paths):
    return {path: path.stat().st_mtime_ns if path.exists() else None for path in paths}


def test_notices_come_from_the_lock_and_the_bootloader_pin(tmp_path, environment):
    """PyInstaller's bootloader is in the executable; the test tools are not."""
    root = project(tmp_path, pins(environment, drop={"pillow"}),
                   build=pins([p for p in environment if canonical(p[0]) == "pillow"]))
    completed = prepare(root)
    assert completed.returncode == 0, completed.stderr
    names = shipped_names(root)
    assert "pillow" in names, "a build-file pin ships its notice"
    assert not names & {"pytest", "pluggy", "iniconfig", "pip"}, "test and build tools"
    assert names <= {canonical(name) for name, _ in environment}


def test_a_package_dropped_from_the_lock_takes_its_notice_with_it(tmp_path, environment):
    root = project(tmp_path, pins(environment))
    stale = plant_stale_notice(root)
    completed = prepare(root)
    assert completed.returncode == 0, completed.stderr
    assert not stale.exists(), "OpenPowerstation.spec bundles the whole folder, stale notices included"


@pytest.mark.parametrize("case,expected", [
    ("pinned, not installed", ABSENT),
    ("installed, not pinned", "pillow"),
    ("installed at another version", "0.0.1"),
])
def test_an_environment_that_is_not_the_lock_is_refused(tmp_path, environment, case,
                                                         expected):
    """The executable bundles what is installed, so notices from the lock are only
    right when the two agree. A refused run leaves no notices for a build to ship.
    """
    lock = {
        "pinned, not installed": pins(environment) + [f"{ABSENT}==1.0"],
        "installed, not pinned": pins(environment, drop={"pillow"}),
        "installed at another version": pins(environment, override={"pillow": "0.0.1"}),
    }[case]
    root = project(tmp_path, lock)
    stale = plant_stale_notice(root)
    completed = prepare(root)
    assert completed.returncode != 0, f"{case} was accepted"
    assert expected in completed.stderr.lower()  # Pillow's metadata name varies in case
    assert "Traceback" not in completed.stderr
    assert not stale.parent.exists()


def test_a_requirement_that_is_not_an_exact_pin_is_refused(tmp_path, environment):
    """A range would make the notices depend on what pip happened to pick."""
    root = project(tmp_path, pins(environment, drop={"pillow"}) + ["pillow>=10"])
    completed = prepare(root)
    assert completed.returncode != 0
    assert "pillow>=10" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_prepare_regenerates_its_outputs_deterministically(tmp_path, environment):
    """prepare.py must be safe to re-run; the build calls it every time."""
    before = snapshot(OUTPUTS)
    root = project(tmp_path, pins(environment))
    outputs = []
    for _ in range(2):
        completed = prepare(root)
        assert completed.returncode == 0, completed.stderr
        outputs.append(((root / "packaging/version_info.txt").read_text("utf-8"),
                        (root / "artifacts/package-licenses/DEPENDENCIES.json")
                        .read_text("utf-8")))
    assert outputs[0] == outputs[1], "prepare.py is not deterministic"
    assert f"StringStruct('FileVersion', '{openpowerstation.__version__}')" in outputs[0][0]
    assert snapshot(OUTPUTS) == before, "a run in a copy wrote into the repository"


def test_the_real_lock_pins_every_declared_runtime_dependency():
    """The lock parses, and nothing the package needs at run time goes unnoticed.

    Loading prepare.py must not run it: it used to do all its work at import.
    """
    before = snapshot(OUTPUTS)
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        spec = importlib.util.spec_from_file_location("prepare_under_test",
                                                      ROOT / "packaging/prepare.py")
        prepare_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare_module)
    finally:
        sys.path.remove(str(ROOT / "packaging"))
    assert snapshot(OUTPUTS) == before, "importing prepare.py wrote build outputs"

    locked = prepare_module.locked_pins()
    project_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    extras = project_metadata["optional-dependencies"]
    declared = project_metadata["dependencies"] + [
        requirement for extra in ("charts", "gui", "packaging") for requirement in extras[extra]]
    runtime = {canonical(re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0])
               for requirement in declared} - {"openpowerstation"}
    runtime.add("pyinstaller")  # the bootloader is part of the executable
    assert runtime <= set(locked), sorted(runtime - set(locked))
    assert not runtime & prepare_module.SKIP, sorted(runtime & prepare_module.SKIP)
