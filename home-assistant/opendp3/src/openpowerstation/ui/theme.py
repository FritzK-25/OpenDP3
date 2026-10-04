"""Design tokens and the single Qt stylesheet shared by both themes.

This is the only module in :mod:`openpowerstation.ui` allowed to contain colour literals.
``LIGHT`` and ``DARK`` must always carry identical key sets: the stylesheet is one
``string.Template`` rendered against whichever mapping is active, so a token missing
from one theme raises rather than silently painting the wrong colour.
"""
from string import Template

LIGHT = {
    "bg": "#f4f5f7",
    "surface": "#ffffff",
    "elevated": "#f0f2f5",
    "border": "#e3e6eb",
    "border_strong": "#cdd3db",
    "text": "#16191f",
    "muted": "#646d7a",
    "faint": "#8b939f",
    "disabled": "#a3aab4",
    "accent": "#6c5ce7",
    "accent_text": "#ffffff",
    "accent_soft": "#eeebfd",
    "danger": "#d92d43",
    "danger_soft": "#fdecee",
    "warn": "#b45309",
    "warn_soft": "#fdf3e3",
    "ok": "#067a5b",
    "ok_soft": "#e6f6f0",
    "info": "#1d63d1",
    "info_soft": "#e8f0fe",
    "selection": "#ddd8fb",
    "scroll": "#ccd2da",
    "plot_bg": "#ffffff",
    "plot_axis": "#98a1ad",
    "plot_label": "#646d7a",
    "marker": "#b45309",
    "grid_alpha": 0.16,
}

DARK = {
    "bg": "#0d141c",
    "surface": "#151f2a",
    "elevated": "#1d2a37",
    "border": "#26333f",
    "border_strong": "#3a4b5b",
    "text": "#e6edf5",
    "muted": "#9aabbc",
    "faint": "#7a8b9c",
    "disabled": "#5c6c7c",
    "accent": "#8b7cf0",
    "accent_text": "#10111f",
    "accent_soft": "#241f3d",
    "danger": "#ff6b7f",
    "danger_soft": "#3a1c23",
    "warn": "#e0a458",
    "warn_soft": "#33261a",
    "ok": "#4fd1a5",
    "ok_soft": "#12312a",
    "info": "#6fa8f5",
    "info_soft": "#16283d",
    "selection": "#2f3d63",
    "scroll": "#34434f",
    "plot_bg": "#111a24",
    "plot_axis": "#4b5c6c",
    "plot_label": "#9aabbc",
    "marker": "#e0a458",
    "grid_alpha": 0.13,
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "light"

# Chart series. The dark set is tuned for a near-black canvas and is unreadable on
# white, so each theme carries its own ordering-stable palette.
SERIES = {
    "light": ["#2563eb", "#0f9d76", "#c2620a", "#c02a86", "#6d4bd8", "#c02b31", "#0e7f96", "#526078"],
    "dark": ["#67ddbe", "#68aaf0", "#f4bd76", "#dd86cd", "#a2b9ff", "#e88f82", "#c5e477", "#d8dde6"],
}

TEMPLATE = Template("""
QWidget {background:$bg;color:$text;font-family:'Segoe UI','Inter',sans-serif;font-size:12px}
QMainWindow, QDialog {background:$bg}
QToolTip {background:$surface;color:$text;border:1px solid $border_strong;padding:4px}

QFrame#sidebar {background:$bg;border-right:1px solid $border}
QLabel#brand {font-size:15px;font-weight:700;color:$text}
QLabel#brandSub {color:$muted;font-size:11px}
QPushButton#nav {background:transparent;border:0;border-radius:8px;padding:9px 12px;
                 text-align:left;color:$muted;font-size:13px}
QPushButton#nav:hover {background:$elevated;color:$text}
QPushButton#nav:checked {background:$accent_soft;color:$accent;font-weight:600}
QFrame#deviceCard {background:$surface;border:1px solid $border;border-radius:12px}
QFrame#deviceCard QLabel {background:transparent}
QLabel#deviceName {font-size:13px;font-weight:600;color:$text}

QLabel#title {font-size:24px;font-weight:650;color:$text}
QLabel#subtitle {color:$muted;font-size:13px}
QLabel#muted {color:$muted}
QLabel#faint {color:$faint;font-size:11px}
QLabel#sectionTitle {font-size:13px;font-weight:650;color:$text}

QLabel#pill {background:$elevated;border:1px solid $border;border-radius:11px;
             padding:4px 11px;color:$muted;font-size:11px;font-weight:700}
QLabel#pill[tone="accent"] {background:$accent_soft;border-color:$accent_soft;color:$accent}
QLabel#pill[tone="ok"] {background:$ok_soft;border-color:$ok_soft;color:$ok}
QLabel#pill[tone="warn"] {background:$warn_soft;border-color:$warn_soft;color:$warn}
QLabel#pill[tone="danger"] {background:$danger_soft;border-color:$danger_soft;color:$danger}

QFrame#card {background:$surface;border:1px solid $border;border-radius:14px}
QFrame#card QLabel {background:transparent}
QFrame#chip {background:$elevated;border-radius:12px}
QLabel#cardLabel {color:$muted;font-size:11px;font-weight:700;letter-spacing:1px}
QLabel#value {font-size:26px;font-weight:600;color:$text}
QLabel#age {color:$faint;font-size:11px}

QPushButton {background:$surface;border:1px solid $border_strong;border-radius:8px;
             padding:8px 14px;color:$text}
QPushButton:hover {background:$elevated}
QPushButton:pressed {background:$border}
QPushButton:checked {background:$accent_soft;color:$accent;border-color:$accent;font-weight:650}
QPushButton:disabled {color:$disabled;border-color:$border;background:$bg}
QPushButton::menu-indicator {width:0;height:0;image:none}
QPushButton#chartControl, QComboBox#chartControl {padding:3px 7px;font-size:11px;border-radius:5px}
QPushButton#primary {background:$accent;color:$accent_text;font-weight:650;border:1px solid $accent}
QPushButton#primary:hover {background:$accent;border-color:$accent_text}
QPushButton#primary:disabled {background:$bg;color:$disabled;border:1px solid $border}
QPushButton#danger {color:$danger;border:1px solid $danger_soft;background:$danger_soft}
QPushButton#danger:hover {background:$danger_soft;border-color:$danger}
QPushButton#danger:disabled {color:$disabled;background:$bg;border:1px solid $border}
QPushButton#iconButton {background:transparent;border:1px solid $border;border-radius:8px;padding:6px 9px}
QPushButton#iconButton:hover {background:$elevated}
QPushButton#link {background:transparent;border:0;color:$accent;padding:2px 4px;text-align:left}
QPushButton#link:hover {color:$text}

QLineEdit, QComboBox {background:$surface;border:1px solid $border_strong;
             border-radius:8px;padding:7px 9px;color:$text;selection-background-color:$selection;
             selection-color:$text}
/* Spin boxes keep a small radius: styling their sub-controls at all suppresses the
   native step arrows, and a large radius clips the ones Qt draws for us. */
QAbstractSpinBox {background:$surface;border:1px solid $border_strong;border-radius:4px;
             padding:7px 2px 7px 9px;color:$text;selection-background-color:$selection;
             selection-color:$text}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {border-color:$accent}
QLineEdit:disabled, QComboBox:disabled {color:$disabled;background:$bg}
/* No QComboBox::drop-down rule on purpose: styling that sub-control suppresses
   the arrow Qt would otherwise draw, leaving a field that does not read as a
   dropdown at all. The same applies to QAbstractSpinBox's step buttons. */
QComboBox QAbstractItemView {background:$surface;border:1px solid $border_strong;
             selection-background-color:$selection;selection-color:$text;outline:0}

QTableWidget, QPlainTextEdit, QListWidget {background:$surface;border:1px solid $border;
             border-radius:12px;color:$text;gridline-color:$border;
             selection-background-color:$selection;selection-color:$text}
QTableWidget::item {padding:5px 4px}
QFrame#card QTableWidget, QFrame#card QPlainTextEdit, QFrame#card QListWidget {
             border:1px solid $border;border-radius:10px}
QFrame#card QHeaderView::section {background:$surface}
QHeaderView::section {background:$surface;color:$muted;padding:8px 6px;border:0;
             border-bottom:1px solid $border;font-weight:650}
QTableCornerButton::section {background:$surface;border:0}

QTabWidget::pane {border:1px solid $border;border-radius:12px;background:$surface;top:-1px}
QTabBar::tab {background:transparent;padding:8px 14px;color:$muted;border:0;
             border-bottom:2px solid transparent;margin-right:2px}
QTabBar::tab:selected {color:$accent;border-bottom:2px solid $accent;font-weight:650}
QTabBar::tab:hover {color:$text}

QMenuBar {background:$bg;color:$muted;border-bottom:1px solid $border}
QMenuBar::item:selected {background:$elevated;color:$text}
QMenu {background:$surface;border:1px solid $border_strong;padding:4px}
QMenu::item {padding:6px 22px;border-radius:6px}
QMenu::item:selected {background:$accent_soft;color:$accent}

QSlider::groove:horizontal {background:$border;height:5px;border-radius:3px}
QSlider::sub-page:horizontal {background:$accent;height:5px;border-radius:3px}
QSlider::handle:horizontal {background:$accent;width:14px;height:14px;margin:-5px 0;border-radius:7px}
QSlider::handle:horizontal:disabled {background:$disabled}

QSplitter::handle {background:transparent;width:14px}
QScrollBar:vertical {background:transparent;width:10px;margin:2px}
QScrollBar::handle:vertical {background:$scroll;border-radius:5px;min-height:28px}
QScrollBar:horizontal {background:transparent;height:10px;margin:2px}
QScrollBar::handle:horizontal {background:$scroll;border-radius:5px;min-width:28px}
QScrollBar::add-line, QScrollBar::sub-line {width:0;height:0}
QScrollBar::add-page, QScrollBar::sub-page {background:transparent}
QScrollArea {border:0;background:$bg}
QGraphicsView {border:0;background:transparent}
QCheckBox {color:$text}
""")


def tokens(name: str) -> dict:
    """Return the token mapping for ``name``, falling back to the default theme."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def series(name: str) -> list:
    return SERIES.get(name, SERIES[DEFAULT_THEME])


def stylesheet(name: str) -> str:
    """Render the shared Qt stylesheet for one theme.

    ``substitute`` (not ``safe_substitute``) is deliberate: an unknown token must
    fail loudly in tests rather than reach a user as an unstyled widget.
    """
    return TEMPLATE.substitute(tokens(name))
