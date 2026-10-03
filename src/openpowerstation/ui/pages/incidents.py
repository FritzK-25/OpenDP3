"""Incidents: protected windows and the full session event timeline."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..widgets import Card, label, table


class IncidentsPage(QWidget):
    KEY = "incidents"
    TITLE = "Incidents"
    SUBTITLE = "Protected retention windows and every recognised timeline event"

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        split = QSplitter(Qt.Orientation.Horizontal)

        incident_card = Card("Protected windows")
        incident_card.body.addWidget(label(
            "An incident is a protected time window with trigger reasons, not a "
            "confirmed diagnosis.", "faint", wrap=True))
        self.incident_table = table(["ID", "Window (s)", "Coverage"])
        self.incident_table.itemSelectionChanged.connect(self.on_selection)
        incident_card.body.addWidget(self.incident_table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.mark_button = QPushButton("Mark incident")
        self.mark_button.clicked.connect(window.mark)
        self.export_button = QPushButton("Export selected incident…")
        self.export_button.clicked.connect(window.export)
        self.delete_button = QPushButton("Delete selected incident protection")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(window.delete_incident)
        for button in (self.mark_button, self.export_button, self.delete_button):
            actions.addWidget(button)
        actions.addStretch()
        incident_card.body.addLayout(actions)
        split.addWidget(incident_card)

        events_card = Card("Timeline events")
        self.events_table = table(["Elapsed", "Event", "Detail (private)"])
        events_card.body.addWidget(self.events_table, 1)
        events_card.body.addWidget(label(
            "Details and free-text notes are private to this machine. Exports carry "
            "event categories only.", "faint", wrap=True))
        split.addWidget(events_card)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([520, 760])
        layout.addWidget(split, 1)

    def selected(self):
        item = self.incident_table.item(self.incident_table.currentRow(), 0)
        return int(item.text()) if item else None

    def on_selection(self):
        # Qt clears the selection while tearing the window down, firing this signal
        # against half-destroyed widgets. Nothing here is useful once closing.
        if self.window.closing:
            return
        self.update_actions()
        self.window.focus_incident()

    def update_actions(self):
        if self.window.closing:
            return
        has_selection = self.selected() is not None
        self.delete_button.setEnabled(has_selection)
        self.export_button.setEnabled(bool(self.window.snap.get("count")))
        self.mark_button.setEnabled(self.window.can_mark())

    def update_snapshot(self, snap, now_t):
        self.incident_table.blockSignals(True)
        selected = self.selected()
        self.incident_table.setRowCount(len(snap["incidents"]))
        for i, incident in enumerate(snap["incidents"]):
            coverage = "Window complete" if incident["complete"] else "Incomplete window"
            if incident["gaps"]:
                coverage += " · gaps"
            values = [str(incident["id"]),
                      f"{incident['start_t']:.0f}–{incident['end_t']:.0f}", coverage]
            for j, value in enumerate(values):
                self.incident_table.setItem(i, j, QTableWidgetItem(value))
            if selected == incident["id"]:
                self.incident_table.selectRow(i)
        self.incident_table.blockSignals(False)
        events = list(reversed(snap["events"][-100:]))
        self.events_table.setRowCount(len(events))
        for i, event in enumerate(events):
            values = [f"{event['t']:.3f}s", event["kind"].replace("_", " "), event["detail"]]
            for j, value in enumerate(values):
                self.events_table.setItem(i, j, QTableWidgetItem(value))
        self.update_actions()
