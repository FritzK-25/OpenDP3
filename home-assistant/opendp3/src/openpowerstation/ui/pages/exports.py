"""Exported files: evidence bundles this viewer wrote to the default folder."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from ..widgets import Card, label, table

FOLDER = "exports"
EMPTY = ("No evidence bundles in the default folder yet. Use Export evidence to write "
         "one, then it appears here.")
SCOPE = ("Only bundles written to the default folder are listed. Bundles you exported "
         "somewhere else still exist; this viewer does not track them.")


def bundle_time(name: str):
    """Recover the export time from the folder name the app generates."""
    parts = name.split("-")
    if len(parts) >= 3:
        try:
            return datetime.strptime(parts[1] + parts[2], "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def list_bundles(root: Path):
    """Return each readable bundle under ``<root>/exports``, newest first.

    Unreadable or half-written bundles are skipped rather than raising: this page
    must never be the reason a viewer cannot open.
    """
    folder = Path(root) / FOLDER
    bundles = []
    try:
        entries = sorted(folder.iterdir(), reverse=True)
    except OSError:
        return bundles
    for entry in entries:
        try:
            summary = json.loads((entry / "summary.json").read_text("utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(summary, dict):
            continue
        bundles.append({"path": entry, "name": entry.name, "when": bundle_time(entry.name),
                        "summary": summary, "report": entry / "report.html"})
    return bundles


class ExportsPage(QWidget):
    KEY = "exports"
    TITLE = "Exported files"
    SUBTITLE = "Sanitised evidence bundles written by this viewer"
    COLUMNS = ["Exported", "Source", "Window (elapsed s)", "Coverage", "Decoder"]

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.bundles = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card = Card("Evidence bundles")
        self.empty = label(EMPTY, "muted", wrap=True)
        card.body.addWidget(self.empty)
        self.bundle_table = table(self.COLUMNS)
        self.bundle_table.itemSelectionChanged.connect(self.update_actions)
        card.body.addWidget(self.bundle_table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.export_button = QPushButton("Export evidence…")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(window.export)
        self.folder_button = QPushButton("Open bundle folder")
        self.folder_button.clicked.connect(lambda: self.open(lambda b: b["path"]))
        self.report_button = QPushButton("Open report")
        self.report_button.clicked.connect(lambda: self.open(lambda b: b["report"]))
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.reload)
        for button in (self.export_button, self.folder_button, self.report_button,
                       self.refresh_button):
            actions.addWidget(button)
        actions.addStretch()
        card.body.addLayout(actions)
        card.body.addWidget(label(SCOPE, "faint", wrap=True))
        layout.addWidget(card, 1)
        self.reload()

    def selected(self):
        row = self.bundle_table.currentRow()
        return self.bundles[row] if 0 <= row < len(self.bundles) else None

    def open(self, pick):
        bundle = self.selected()
        if bundle and Path(pick(bundle)).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pick(bundle))))

    def update_actions(self):
        bundle = self.selected()
        self.folder_button.setEnabled(bundle is not None)
        self.report_button.setEnabled(bool(bundle and bundle["report"].exists()))
        self.export_button.setEnabled(bool(self.window.snap.get("count")))

    def reload(self):
        self.bundles = list_bundles(self.window.root)
        # An empty grid reads as a broken page; show the explanation instead.
        self.empty.setVisible(not self.bundles)
        self.bundle_table.setVisible(bool(self.bundles))
        self.bundle_table.setRowCount(len(self.bundles))
        for i, bundle in enumerate(self.bundles):
            summary = bundle["summary"]
            when = bundle["when"].strftime("%Y-%m-%d %H:%M:%S") if bundle["when"] else bundle["name"]
            coverage = "Window complete" if summary.get("incident_window_complete") else "Incomplete window"
            if summary.get("contains_gaps"):
                coverage += " · gaps"
            values = [when,
                      "SYNTHETIC DEMO" if summary.get("synthetic") else "DP3 recording",
                      f"{summary.get('elapsed_start_s', 0):.0f}–{summary.get('elapsed_end_s', 0):.0f}",
                      coverage, str(summary.get("decoder", "unknown"))]
            for j, value in enumerate(values):
                self.bundle_table.setItem(i, j, QTableWidgetItem(value))
        self.update_actions()

    def update_snapshot(self, snap, now_t):
        self.update_actions()
