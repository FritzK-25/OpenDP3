"""The headless install must not drag in the desktop.

A collector on the Pi runs the CLI, the BLE workers and the MQTT bridges. None
of those draw anything, but every one of them used to install Qt, pyqtgraph,
matplotlib and numpy because `pyproject.toml` declared them as core
dependencies. These tests pin the split so it cannot quietly close again.
"""
from pathlib import Path
import subprocess
import sys
import tomllib

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
# Chart rendering for evidence exports: a CLI capability, not a desktop one.
RENDER_PACKAGES = {"matplotlib", "numpy"}
# The desktop window itself.
DESKTOP_PACKAGES = {"pyside6", "pyqtgraph"}
# Windows version-resource generation in packaging/prepare.py.
PACKAGING_PACKAGES = {"pillow"}
OPTIONAL_PACKAGES = RENDER_PACKAGES | DESKTOP_PACKAGES | PACKAGING_PACKAGES

# Imported by a collector, a bridge or the CLI on the headless production path.
HEADLESS_MODULES = (
    "opendp3.cli",
    "opendp3.bridge",
    "opendp3.jackery_bridge",
    "opendp3.recorder",
    "opendp3.storage",
    "opendp3.queries",
    "opendp3.protocol",
    "opendp3.decoder",
    "opendp3.config",
    "opendp3.runtime",
)


def requirement_names(requirements):
    """Distribution names, lowercased, with version specifiers stripped."""
    names = set()
    for requirement in requirements:
        name = requirement.split(";")[0].strip()
        for separator in ("[", ">", "<", "=", "!", "~", " "):
            name = name.split(separator)[0]
        if name:
            names.add(name.strip().lower())
    return names


def metadata():
    return tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def extra_closure(name, seen=None):
    """Every package an extra installs, following `opendp3[...]` self-references."""
    extras = metadata()["optional-dependencies"]
    seen = set() if seen is None else seen
    if name in seen:
        return set()
    seen.add(name)
    resolved = set()
    for requirement in extras[name]:
        if requirement.split("[")[0].strip().lower() == "opendp3":
            inner = requirement[requirement.index("[") + 1:requirement.index("]")]
            for referenced in inner.split(","):
                resolved |= extra_closure(referenced.strip(), seen)
        else:
            resolved |= requirement_names([requirement])
    return resolved


def run_headless(script):
    """Run `script` in a subprocess with every optional package unimportable.

    That is the production add-on's environment: core dependencies only, no
    rendering and no desktop.
    """
    blocker = f"""
import sys
BLOCKED = {sorted(OPTIONAL_PACKAGES)!r}

class Blocker:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0].lower()
        if root in BLOCKED:
            raise ModuleNotFoundError(f"blocked for test: {{name}}", name=name.split(".")[0])
        return None

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, {str(SOURCE)!r})
"""
    return subprocess.run(
        [sys.executable, "-c", blocker + script],
        capture_output=True, text=True, timeout=120,
    )


def test_core_dependencies_exclude_every_optional_package():
    core = requirement_names(metadata()["dependencies"])
    assert not (core & OPTIONAL_PACKAGES), (
        "core dependencies ship to the production add-on; rendering and desktop "
        f"packages belong in an extra: {sorted(core & OPTIONAL_PACKAGES)}"
    )


def test_gui_extra_carries_the_desktop_and_its_charts():
    """The desktop's exports page calls the same evidence export the CLI does."""
    assert DESKTOP_PACKAGES | RENDER_PACKAGES <= extra_closure("gui")


def test_charts_extra_does_not_drag_in_the_desktop():
    """`opendp3 export` is a CLI command; rendering evidence must not need Qt."""
    assert RENDER_PACKAGES <= extra_closure("charts")
    assert not (extra_closure("charts") & DESKTOP_PACKAGES)


def test_plain_test_environment_can_run_everything_but_the_window():
    """The export privacy assertions must not depend on a desktop install.

    They are the boundary that keeps identifiers, notes, firmware text and raw
    payloads out of shareable evidence, so a normal `[test]` run has to exercise
    them rather than skip them.
    """
    test = extra_closure("test")
    assert RENDER_PACKAGES <= test, "test_exports.py renders charts"
    assert PACKAGING_PACKAGES <= test, "test_versioning.py builds the resource"
    assert not (test & DESKTOP_PACKAGES), "only the Qt modules need the window"
    assert "pytest-qt" not in test
    assert "pytest-qt" in extra_closure("test-gui")
    assert DESKTOP_PACKAGES <= extra_closure("test-gui")


def test_windows_resource_pillow_dependency_is_declared():
    """packaging/prepare.py used to get Pillow transitively through matplotlib.

    With matplotlib optional, that accident is gone, so the requirement has to
    be declared or the Windows version resource stops building.
    """
    assert "PIL" in (PROJECT / "packaging" / "prepare.py").read_text(encoding="utf-8")
    assert PACKAGING_PACKAGES <= extra_closure("packaging")


def test_headless_modules_import_without_the_desktop_packages():
    result = run_headless(
        "import importlib\n"
        f"for name in {HEADLESS_MODULES!r}:\n"
        "    importlib.import_module(name)\n"
        "print('imported')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_headless_cli_runs_without_the_desktop_packages():
    result = run_headless(
        "from opendp3.cli import main\n"
        "raise SystemExit(main(['--help']))\n"
    )
    assert result.returncode == 0, result.stderr


def test_desktop_command_explains_the_missing_extra():
    result = run_headless(
        "from opendp3.cli import main\n"
        "code = main(['gui'])\n"
        "print('exit', code)\n"
    )
    assert "exit 1" in result.stdout, result.stdout + result.stderr
    # An operator error, not a traceback.
    assert "Traceback" not in result.stderr
    assert "opendp3[gui]" in result.stderr
    assert "PySide6" in result.stderr


def test_export_explains_the_missing_extra_at_the_chart_boundary(tmp_path):
    """`exporting` imports fine without matplotlib and only needs it to plot.

    So the failure surfaces deep inside `export_evidence`, not at the import in
    `cli`. Drive a real recording through it to prove the operator still gets a
    named extra rather than a traceback.
    """
    database = tmp_path / "demo.sqlite"
    destination = tmp_path / "out"
    result = run_headless(
        "from opendp3.demo import make_demo\n"
        "from opendp3.cli import main\n"
        f"make_demo({str(database)!r})\n"
        f"code = main(['export', {str(database)!r}, {str(destination)!r}])\n"
        "print('exit', code)\n"
    )
    assert "exit 1" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "matplotlib" in result.stderr
    assert "opendp3[charts]" in result.stderr


def test_each_guard_names_an_extra_that_supplies_what_it_is_missing():
    """The two guards live in different modules and name different extras.

    Qt comes from `gui`, chart rendering from `charts`. A guard that pointed at
    an extra not carrying the package it just failed on would send an operator
    to install the wrong thing.
    """
    for module, extra, package in (("cli.py", "gui", "pyside6"),
                                   ("exporting.py", "charts", "matplotlib")):
        source = (SOURCE / "opendp3" / module).read_text(encoding="utf-8")
        assert f"pip install 'opendp3[{extra}]'" in source, module
        assert package in extra_closure(extra), (module, extra, package)


def test_production_requirements_match_the_core_dependencies():
    """home-assistant/opendp3 pins the same set the package declares, and nothing more."""
    headless = PROJECT / "home-assistant" / "opendp3" / "requirements-headless.txt"
    pinned = requirement_names(
        line for line in headless.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    assert pinned == requirement_names(metadata()["dependencies"]), (
        "the add-on's pinned headless environment and the package's core "
        "dependencies have drifted"
    )
