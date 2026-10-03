"""Reusable presentation widgets.

Widgets that need colours beyond what a Qt stylesheet can reach (painted icons,
pyqtgraph canvases) expose ``apply_theme(name)``. The window re-themes by walking
its children and calling that method wherever it exists, so a new themed widget
only has to implement it to join in.
"""
import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QMenu, QSizePolicy, QTableWidget, QVBoxLayout,
)

from . import icons
from .charts import CHART_HELP, ChartPlotWidget, ChartViewBox, ClickComboBox, ElapsedAxis, value_range
from .theme import tokens

NO_VALUE = "—"


def table(headers):
    """Read-only, row-selecting table. Moved verbatim from the original gui.table."""
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setAlternatingRowColors(False)
    widget.setShowGrid(False)
    widget.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return widget


def label(text, role=None, wrap=False):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


class Card(QFrame):
    """Rounded surface panel. Optional heading and a vertical body layout."""

    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 15, 18, 15)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(label(title, "sectionTitle"))


class Pill(QLabel):
    """Small status chip. ``tone`` is a stylesheet property, not a hardcoded colour."""

    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Fixed, not Maximum: beside a long title the pill would otherwise be
        # squeezed and clip its own state text.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setText(text)
        self.set_tone(tone)

    # Horizontal padding and border from the stylesheet's QLabel#pill rule. Qt does
    # not fold stylesheet padding into a QLabel's size hint, so a long state string
    # would be clipped beside the page title without reserving the room here.
    CHROME = 26

    def setText(self, text):  # noqa: N802 - Qt naming
        super().setText((text or "").upper())
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(self.text()) + self.CHROME)

    def set_tone(self, tone):
        self.setProperty("tone", tone)
        # Property selectors only take effect after the style is recomputed.
        self.style().unpolish(self)
        self.style().polish(self)


class IconButton(QPushButton):
    """Flat square button whose glyph re-tints with the theme."""

    def __init__(self, glyph, tooltip="", parent=None):
        super().__init__(parent)
        self.setObjectName("iconButton")
        self.glyph = glyph
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or glyph)
        self.setIconSize(QSize(17, 17))
        self.setFlat(True)

    def set_glyph(self, glyph, theme):
        self.glyph = glyph
        self.apply_theme(theme)

    def apply_theme(self, theme):
        self.setIcon(icons.glyph(self.glyph, tokens(theme)["muted"]))


class NavButton(QPushButton):
    """One sidebar destination. Checked state is driven by the window, not by Qt."""

    def __init__(self, key, text, glyph, parent=None):
        super().__init__(" " + text, parent)
        self.setObjectName("nav")
        self.key = key
        self.glyph = glyph
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.setIconSize(QSize(17, 17))

    def apply_theme(self, theme):
        palette = tokens(theme)
        self.setIcon(icons.glyph(self.glyph, palette["accent"] if self.isChecked() else palette["muted"]))


class Sidebar(QFrame):
    """Brand, destinations, and a text-only device summary.

    There is deliberately no product photograph here: the footer states what the
    viewer is attached to, which is the part that carries information.
    """

    ENTRIES = [
        ("overview", "Overview", "overview"),
        ("live", "Live telemetry", "live"),
        ("incidents", "Incidents", "incidents"),
        ("exports", "Exported files", "exports"),
        ("settings", "Settings", "settings"),
        ("about", "About", "about"),
    ]

    def __init__(self, version, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(212)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(4)
        brand = QHBoxLayout()
        self.mark = QLabel()
        self.mark.setFixedSize(30, 30)
        self.mark.setScaledContents(True)
        brand.addWidget(self.mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("OpenPowerstation", "brand"))
        names.addWidget(label("Local flight recorder", "brandSub"))
        brand.addLayout(names)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(16)
        self.buttons = {}
        for key, text, glyph in self.ENTRIES:
            button = NavButton(key, text, glyph, self)
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch()
        self.device = QFrame()
        self.device.setObjectName("deviceCard")
        device_layout = QVBoxLayout(self.device)
        device_layout.setContentsMargins(14, 12, 14, 12)
        device_layout.setSpacing(3)
        self.device_name = label("DELTA Pro 3", "deviceName")
        self.device_detail = label("No device configured", "faint", wrap=True)
        self.device_link = label("Local Bluetooth only", "faint", wrap=True)
        for widget in (self.device_name, self.device_detail, self.device_link):
            device_layout.addWidget(widget)
        layout.addWidget(self.device)
        layout.addWidget(label(f"Version {version}", "faint"))

    def set_device(self, name, detail, link):
        self.device_name.setText(name)
        self.device_detail.setText(detail)
        self.device_link.setText(link)

    def apply_theme(self, theme):
        self.mark.setPixmap(icons.app_icon(tokens(theme)).pixmap(30, 30))


class StatCard(Card):
    """Headline reading with a tinted glyph chip, a value, and an observation age."""

    def __init__(self, key, title, glyph, tone, parent=None):
        super().__init__(parent=parent)
        self.key = key
        self.glyph = glyph
        self.tone = tone
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(11)
        self.chip = QFrame()
        self.chip.setObjectName("chip")
        self.chip.setFixedSize(38, 38)
        chip_layout = QVBoxLayout(self.chip)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_icon = QLabel()
        self.chip_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip_layout.addWidget(self.chip_icon)
        top.addWidget(self.chip)
        top.addWidget(label(title, "cardLabel"), 1, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(top)
        self.value = label(NO_VALUE, "value")
        self.age = label("Not observed", "age")
        self.body.addWidget(self.value)
        self.body.addWidget(self.age)
        self.setAccessibleName(title)

    def set_reading(self, value, unit, last_t, now_t):
        """Format one coverage row. Mirrors the precision rules of the CLI report."""
        decimals = 1 if unit in ("°C", "%") else 0
        self.value.setText(NO_VALUE if value is None else f"{value:.{decimals}f} {unit}")
        # Receipt times can briefly run ahead of the snapshot clock; never show a
        # negative age, which would read as a measurement from the future.
        self.age.setText("Not observed" if last_t is None
                         else f"Last observed {max(0, now_t - last_t):.1f}s ago")

    def apply_theme(self, theme):
        palette = tokens(theme)
        self.chip.setStyleSheet(f"QFrame#chip{{background:{palette[self.tone + '_soft']};border-radius:12px}}")
        self.chip_icon.setPixmap(icons.glyph(self.glyph, palette[self.tone]).pixmap(19, 19))


class ChartPanel(QFrame):
    """A pyqtgraph plot in a card, with the mockup's legend row above it.

    pyqtgraph only themes its canvas; axis lines, ticks and labels keep library
    defaults and turn invisible on a white background, so every one is set here.

    ``axis_label`` is reserved for the bottom-most panel of an x-linked stack:
    repeating the shared time axis on every chart only eats vertical room.
    """

    fieldsChanged = Signal(str, bool)
    fieldsAllChanged = Signal(bool)
    focusRequested = Signal(str)
    scaleChanged = Signal()

    def __init__(self, group, title, axis_label=False, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.group = group
        self.title = title
        self.legend_html = ""
        self.value_limits = None
        self._scale_window = None
        self.field_actions = {}
        self._field_signature = None
        self.outside_scale = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(3)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.heading = label(title, "sectionTitle")
        self.legend = QLabel("")
        self.legend.setMinimumWidth(0)
        self.legend.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.heading)
        header.addStretch()
        self.fields_button = QPushButton("Fields")
        self.fields_button.setObjectName("chartControl")
        self.fields_button.setAccessibleName(f"Choose {title} fields")
        self.fields_menu = QMenu(self.fields_button)
        self.fields_button.setMenu(self.fields_menu)
        header.addWidget(self.fields_button)
        self.scale_choice = ClickComboBox()
        self.scale_choice.setObjectName("chartControl")
        self.scale_choice.setAccessibleName(f"{title} vertical scale")
        for text, mode in (("Y: Fit data", "fit"), ("Y: Context", "context"), ("Y: Hold", "hold")):
            self.scale_choice.addItem(text, mode)
        self.scale_choice.setCurrentIndex(0)
        self.scale_choice.setToolTip("Fit data: show variation in visible fields. Context: include zero / full percentages. Hold: freeze this scale; clipped values are flagged.")
        self.scale_choice.currentIndexChanged.connect(self._change_scale)
        self.scale_choice.setVisible(group != "state")
        header.addWidget(self.scale_choice)
        self.focus_button = QPushButton("Focus")
        self.focus_button.setObjectName("chartControl")
        self.focus_button.setToolTip("Give this chart the available space. Time stays linked when returning to all charts.")
        self.focus_button.clicked.connect(lambda: self.focusRequested.emit(self.group))
        header.addWidget(self.focus_button)
        layout.addLayout(header)
        layout.addWidget(self.legend)
        self.plot = ChartPlotWidget(viewBox=ChartViewBox(), axisItems={"bottom": ElapsedAxis("bottom")})
        self.plot.setMinimumHeight(100)
        self.plot.getPlotItem().hideButtons()
        # Equal gutters keep linked timestamps at exactly the same pixel in all
        # panels, regardless of the number of digits in their value labels.
        self.plot.getAxis("left").setWidth(104)
        for side in ("left", "bottom"):
            self.plot.getAxis(side).enableAutoSIPrefix(False)
        if axis_label:
            self.plot.setLabel("bottom", "Elapsed receipt time (h:mm:ss)")
        self.plot.setMenuEnabled(False)
        layout.addWidget(self.plot, 1)
        self.setAccessibleName(title)
        self.plot.setToolTip(CHART_HELP)

    def _change_scale(self, _index):
        if self.scale_choice.currentData() != "hold":
            self.value_limits = None
        self.scaleChanged.emit()

    def set_fields(self, entries):
        signature = tuple(entries)
        selected = sum(checked for _, _, checked in entries)
        self.fields_button.setText(f"Fields {selected}/{len(entries)}")
        self.fields_button.setEnabled(bool(entries))
        if signature == self._field_signature or self.fields_menu.isVisible():
            return
        self._field_signature = signature
        self.fields_menu.clear()
        self.field_actions.clear()
        self.fields_menu.addAction("Show all fields", lambda: self.fieldsAllChanged.emit(True))
        self.fields_menu.addAction("Hide all fields", lambda: self.fieldsAllChanged.emit(False))
        self.fields_menu.addSeparator()
        for key, name, checked in entries:
            action = self.fields_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(checked)
            action.triggered.connect(lambda visible, key=key: self.fieldsChanged.emit(key, visible))
            self.field_actions[key] = action

    def fit_values(self, bounds, window=None, following=False):
        mode = self.scale_choice.currentData()
        self.outside_scale = False
        target = value_range(self.group, bounds, mode if mode != "hold" else "fit")
        if mode == "hold" and self.value_limits is not None:
            low, high = self.value_limits
            self.outside_scale = bounds is not None and (bounds[0] < low or bounds[1] > high)
            return
        if self.value_limits is not None and bounds is not None:
            low, high = self.value_limits
            # Keep quiet live readings from jittering a few pixels, but let a
            # departed anomaly stop flattening the chart. Navigation fits anew.
            if following and low <= bounds[0] <= bounds[1] <= high and high - low <= 1.5 * (target[1] - target[0]):
                return
            if window == self._scale_window and target == self.value_limits:
                return
        self._scale_window = window
        if bounds is not None:
            self.value_limits = target
        self.plot.setYRange(*target, padding=0)

    def set_legend(self, html):
        self.legend_html = html
        self.legend.setText(html)

    def apply_theme(self, theme):
        palette = tokens(theme)
        self.plot.setBackground(palette["plot_bg"])
        self.plot.showGrid(x=True, y=True, alpha=palette["grid_alpha"])
        for side in ("left", "bottom"):
            axis = self.plot.getAxis(side)
            axis.setPen(pg.mkPen(palette["plot_axis"], width=1))
            axis.setTextPen(pg.mkPen(palette["plot_label"]))
