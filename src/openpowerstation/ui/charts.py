"""Time-only chart navigation and stable, unit-aware value ranges."""
from bisect import bisect_left
import math

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QScrollArea, QSlider

from ..queries import gap


CHART_HELP = "Scroll to move down the charts · Ctrl + wheel: zoom time · Drag: pan time · Hover: inspect samples"


def elapsed_text(seconds, decimals=0):
    seconds = round(max(0, seconds), decimals)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    width = 2 if decimals == 0 else 3 + decimals
    text = f"{int(minutes):02}:{seconds:0{width}.{decimals}f}"
    return f"{int(hours)}:{text}" if hours else text


class ElapsedAxis(pg.AxisItem):
    def tickValues(self, minimum, maximum, size):
        target = (maximum - minimum) / max(2, size / 100)
        steps = (.001, .002, .005, .01, .02, .05, .1, .2, .5, 1, 2, 5, 10,
                 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400)
        step = next((step for step in steps if step >= target), steps[-1])
        first = math.ceil(minimum / step) * step
        count = max(0, int((maximum - first) / step) + 1)
        return [(step, [first + i * step for i in range(min(count, 1000))])]

    def tickStrings(self, values, scale, spacing):
        decimals = max(0, min(3, math.ceil(-math.log10(spacing)))) if spacing > 0 else 0
        return [elapsed_text(value, decimals) for value in values]


class ChartPlotWidget(pg.PlotWidget):
    hovered = Signal(float)
    hoverLeft = Signal()

    def wheelEvent(self, event):
        # Handle this at the viewport, before GraphicsScene/AxisItem can reinterpret
        # the same wheel event. Plain scrolling bubbles to the chart scroll area.
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            parent = self.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    parent.wheelEvent(event)
                    event.accept()
                    return
                parent = parent.parentWidget()
            event.ignore()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y() * 3
        event.accept()
        if delta:
            steps = max(-10, min(10, delta / 120))
            scene_pos = self.mapToScene(event.position().toPoint())
            self.getViewBox().zoom_time(0.85 ** steps, scene_pos)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        scene_pos = self.mapToScene(event.position().toPoint())
        view = self.getViewBox()
        if event.buttons() == Qt.MouseButton.NoButton and view.sceneBoundingRect().contains(scene_pos):
            self.hovered.emit(view.mapSceneToView(scene_pos).x())

    def leaveEvent(self, event):
        self.hoverLeft.emit()
        super().leaveEvent(event)


class ChartViewBox(pg.ViewBox):
    def __init__(self):
        super().__init__(enableMenu=False)
        self.setMouseEnabled(x=True, y=False)
        self.setMouseMode(self.PanMode)
        self.disableAutoRange()

    def wheelEvent(self, event, axis=None):
        event.ignore()

    def zoom_time(self, factor, scene_pos=None):
        left, right = self.viewRange()[0]
        center = self.mapSceneToView(scene_pos) if scene_pos is not None else pg.Point((left + right) / 2, 0)
        center.setX(min(right, max(left, center.x())))
        self.scaleBy(x=factor, center=center)
        self.sigRangeChangedManually.emit([True, False])

    def mouseDragEvent(self, event, axis=None):
        # Remove the library's secondary right-drag scaling gesture as well.
        if event.button() == Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event, axis=0)
        else:
            event.accept()


class ClickComboBox(QComboBox):
    """Scrolling past a selector must not switch recordings or view presets."""
    def wheelEvent(self, event):
        event.ignore()


class PlaybackSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


def value_range(group, bounds, mode="context"):
    """Round outward, keep useful context, and never clip reported outliers.

    Context uses full percentages, zero-based power and estimated times, and a
    ten-degree temperature span. Fit data allows finer variation, with small
    minimum spans for flat readings. These are display choices, not device
    safety limits.
    """
    defaults = {"temperature": (0, 40), "power": (0, 100), "soc": (0, 100),
                "voltage": (0, 250), "frequency": (45, 65), "duration": (0, 10), "state": (0, 5)}
    if bounds is None:
        return defaults[group]
    low, high = bounds
    if mode == "context" and group == "soc":
        low, high = min(0, low), max(100, high)
        if (low, high) == (0, 100):
            return low, high
    elif mode == "context" and group in ("power", "duration", "state"):
        low, high = min(0, low), max(0, high)
    minimum = ({"temperature": 10, "power": 100, "soc": 100, "voltage": 20, "frequency": 2,
                "duration": 5, "state": 5} if mode == "context"
               else {"temperature": 2, "power": 20, "soc": .2, "voltage": 2, "frequency": .2,
                     "duration": .5, "state": 1})[group]
    span = max(minimum, high - low)
    middle = (low + high) / 2
    lower, upper = middle - span * .58, middle + span * .58
    if mode == "context" and group in ("power", "duration", "state"):
        if low == 0:
            lower = 0
            upper = max(minimum, high * 1.08)
        elif high == 0:
            lower = min(-minimum, low * 1.08)
            upper = 0
    raw_step = (upper - lower) / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(n for n in (1, 2, 5, 10) if n * magnitude >= raw_step) * magnitude
    return math.floor(lower / step) * step, math.ceil(upper / step) * step


def sample_value(value):
    return str(int(value)) if float(value).is_integer() else f"{value:.9g}"


def nearest_sample(points, seconds, edge_tolerance=0, thinning=None):
    """Return an actual adjacent sample, never interpolate across an evidence gap.

    Receipt time is included in the inspector, since independent fields need
    not arrive together. Outside a field's coverage we do not carry values on.
    What counts as a gap is the charts' own rule (queries.gap), so the
    inspector reaches exactly as far as a drawn line does.
    """
    if not points:
        return None
    index = bisect_left(points, seconds, key=lambda p: p["t"])
    if index < len(points) and points[index]["t"] == seconds:
        candidate = points[index]
    elif index == 0 or index == len(points):
        candidate = points[0] if index == 0 else points[-1]
        if abs(candidate["t"] - seconds) > edge_tolerance:
            return None
    else:
        before, after = points[index - 1:index + 1]
        if (gap(before, after, thinning)
                or before["quality"] == "repeated_unverified" or after["quality"] == "repeated_unverified"):
            return None
        candidate = min((before, after), key=lambda p: abs(p["t"] - seconds))
    return candidate if candidate["quality"] != "repeated_unverified" else None
