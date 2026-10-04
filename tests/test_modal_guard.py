"""conftest.no_modal_dialogs turns a modal dialog into a test failure.

Offscreen, nothing can dismiss a modal dialog, so one opened by a test blocks
the run until CI cancels the job, with no traceback saying which test it was.
These are every modal entry point the desktop GUI uses.
"""
import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

CALLS = {
    "QMessageBox.warning": lambda: QMessageBox.warning(None, "OpenPowerstation", "text"),
    "QMessageBox.question": lambda: QMessageBox.question(None, "OpenPowerstation", "text"),
    "QFileDialog.getOpenFileName": lambda: QFileDialog.getOpenFileName(None, "Open"),
    "QFileDialog.getExistingDirectory": lambda: QFileDialog.getExistingDirectory(None, "Folder"),
    "QInputDialog.getText": lambda: QInputDialog.getText(None, "Mark incident", "Note:"),
    "QDialog.exec": lambda: QDialog().exec(),
}


@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
def test_a_modal_dialog_fails_the_test_instead_of_hanging(qtbot, call):
    with pytest.raises(pytest.fail.Exception, match="modal dialog"):
        call()
