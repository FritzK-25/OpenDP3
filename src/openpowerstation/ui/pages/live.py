"""Live telemetry: full-width field coverage and the decoded frame at the cursor."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget

from ..widgets import Card, label, table


class LivePage(QWidget):
    KEY = "live"
    TITLE = "Live telemetry"
    SUBTITLE = "Every mapped field, its freshness, and the raw frame under the cursor"

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        split = QSplitter(Qt.Orientation.Horizontal)

        coverage_card = Card("Field coverage")
        coverage_card.body.addWidget(label(
            "Ages are host receipt times, not verified device measurement times. "
            "A field that is not observed is unknown, not zero.", "faint", wrap=True))
        self.coverage_table = table(["Field", "Value", "Age / cadence", "Coverage"])
        coverage_card.body.addWidget(self.coverage_table, 1)
        split.addWidget(coverage_card)

        raw_card = Card("Decoded frame")
        self.raw_label = label("Local decoded fields · latest packet at cursor", "muted", wrap=True)
        raw_card.body.addWidget(self.raw_label)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setAccessibleName("Decoded frame JSON")
        raw_card.body.addWidget(self.raw_text, 1)
        raw_card.body.addWidget(label("Session notes (private)", "sectionTitle"))
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(110)
        self.notes.setAccessibleName("Session notes")
        raw_card.body.addWidget(self.notes)
        raw_card.body.addWidget(label(
            "Opaque payload bytes stay in the database and are never written to a "
            "shareable export.", "faint", wrap=True))
        split.addWidget(raw_card)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 520])
        layout.addWidget(split, 1)

    def update_snapshot(self, snap, now_t):
        rows = snap["coverage"]
        self.coverage_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            age = "—" if row["last_t"] is None else f"{max(0, now_t - row['last_t']):.1f}s old"
            if row["median_interval"] is not None:
                age += f" / {row['median_interval']:.2f}s median"
            values = [row["label"],
                      "—" if row["value"] is None else f"{row['value']:g} {row['unit']}",
                      age, row["status"]]
            for j, value in enumerate(values):
                self.coverage_table.setItem(i, j, QTableWidgetItem(value))
        raw = snap["raw"]
        if raw:
            self.raw_label.setText(
                f"Frame {raw['id']} · {raw['t']:.3f}s · {raw['status']}\n"
                "Local/private decoded fields; opaque bytes stay in the database.")
            self.raw_text.setPlainText(
                json.dumps(json.loads(raw["decoded"]), indent=2, ensure_ascii=False))
        session = snap["session"]
        self.notes.setPlainText(
            f"Firmware (private): {session['firmware'] or 'Unknown'}\n"
            f"Conditions: {session['conditions'] or 'Not entered'}\n"
            f"Decoder: {session['decoder']}")
