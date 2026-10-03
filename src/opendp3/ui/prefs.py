"""Viewer preferences in ``<root>/ui.json``.

Deliberately separate from :mod:`opendp3.config`: ``Config`` is strictly validated
and requires a paired DP3, so a viewer that has only ever opened a synthetic demo
has no ``config.json`` at all and would have nowhere to keep a theme choice.

Reads fail open. A corrupt or unreadable preferences file must never stop the
recorder from starting, so every error path returns the defaults.
"""
import json
import os
from pathlib import Path

from .theme import DEFAULT_THEME, THEMES

FILENAME = "ui.json"
DEFAULTS = {"theme": DEFAULT_THEME, "page": "overview"}


def load_prefs(root: Path) -> dict:
    """Return sanitised preferences, ignoring anything unrecognised on disk."""
    prefs = dict(DEFAULTS)
    try:
        values = json.loads((Path(root) / FILENAME).read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        return prefs
    if not isinstance(values, dict):
        return prefs
    if values.get("theme") in THEMES:
        prefs["theme"] = values["theme"]
    page = values.get("page")
    if isinstance(page, str) and page.isidentifier():
        prefs["page"] = page
    return prefs


def save_prefs(root: Path, prefs: dict) -> None:
    """Write preferences atomically. Failures are silent: this is not evidence."""
    path = Path(root) / FILENAME
    values = {key: prefs.get(key, default) for key, default in DEFAULTS.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(values, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    except OSError:
        pass
