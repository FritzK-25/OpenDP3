"""About: versions, the qualification statement, and third-party licences."""
import sys
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QVBoxLayout, QWidget

from ... import DECODER_VERSION, __version__
from ...exporting import QUALIFICATION
from ..widgets import Card, label

QT_NOTICE = ("OpenPowerstation uses Qt/PySide6 under its open-source LGPLv3 option. Each "
             "dependency retains its own copyright and license.")


def licence_files():
    """Locate bundled licence texts in both frozen and source layouts."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[4]))
    roots = [base / "licenses"] if getattr(sys, "frozen", False) else [
        base / "THIRD_PARTY_LICENSES", base / "artifacts/package-licenses"]
    return base, sorted(p for root in roots if root.exists() for p in root.rglob("*") if p.is_file())


class AboutPage(QWidget):
    KEY = "about"
    TITLE = "About OpenPowerstation"
    SUBTITLE = "Versions, limits of this evidence, and bundled licences"

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        versions = Card("Build")
        versions.body.addWidget(label(f"Application version {__version__}", "muted"))
        versions.body.addWidget(label(f"Decoder version {DECODER_VERSION}", "muted"))
        versions.body.addWidget(label(
            "Local Bluetooth recorder. It does not bind, unbind, reset or modify "
            "firmware. It changes a device setting only while Allow control is on and "
            "a Home Assistant bridge relays a command: the DP3 AC outputs, and the "
            "Jackery's outputs and documented settings.", "faint", wrap=True))
        layout.addWidget(versions)

        limits = Card("What this recording can and cannot show")
        limits.body.addWidget(label(QUALIFICATION, "muted", wrap=True))
        layout.addWidget(limits)

        licences = Card("Third-party licenses")
        licences.body.addWidget(label(QT_NOTICE, "muted", wrap=True))
        self.choices = QComboBox()
        self.choices.setAccessibleName("License file")
        licences.body.addWidget(self.choices)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setAccessibleName("License text")
        licences.body.addWidget(self.view, 1)
        layout.addWidget(licences, 1)

        base, files = licence_files()
        for path in files:
            self.choices.addItem(str(path.relative_to(base)), path)
        self.choices.currentIndexChanged.connect(self.show_licence)
        self.show_licence(0)

    def show_licence(self, index):
        path = self.choices.itemData(index)
        if path:
            try:
                self.view.setPlainText(Path(path).read_text("utf-8", errors="replace"))
            except OSError:
                self.view.setPlainText("This license file could not be read.")
