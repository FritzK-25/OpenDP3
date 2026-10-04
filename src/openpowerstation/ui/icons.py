"""Vector icons drawn with QPainter.

Deliberately code-only: no SVG or PNG assets means no new ``datas`` entry in
``packaging/OpenPowerstation.spec`` and nothing that can go missing from a frozen build.
Every function takes a colour from the active theme, so icons re-tint when the
user switches themes rather than staying stuck in the palette they were born in.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 40  # Drawn at 40px and downscaled by Qt; keeps strokes crisp on HiDPI.


def _canvas(size=SIZE):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pixmap, painter


def _stroke(painter, color, width=3.0):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return pen


def _icon(draw, color, size=SIZE):
    pixmap, painter = _canvas(size)
    try:
        draw(painter, color)
    finally:
        painter.end()
    return QIcon(pixmap)


# --- sidebar glyphs -------------------------------------------------------

def _overview(painter, color):
    _stroke(painter, color, 3)
    for x, y in ((8, 8), (23, 8), (8, 23), (23, 23)):
        painter.drawRoundedRect(QRectF(x, y, 9, 9), 2.5, 2.5)


def _live(painter, color):
    _stroke(painter, color, 3)
    path = QPainterPath(QPointF(6, 20))
    for point in ((12, 20), (16, 10), (21, 30), (26, 16), (29, 20), (34, 20)):
        path.lineTo(QPointF(*point))
    painter.drawPath(path)


def _incidents(painter, color):
    _stroke(painter, color, 3)
    path = QPainterPath(QPointF(20, 7))
    path.lineTo(QPointF(34, 31))
    path.lineTo(QPointF(6, 31))
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(QPointF(20, 16), QPointF(20, 22))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(20, 26.5), 1.4, 1.4)


def _exports(painter, color):
    _stroke(painter, color, 3)
    painter.drawRoundedRect(QRectF(7, 21, 26, 12), 3, 3)
    painter.drawLine(QPointF(20, 7), QPointF(20, 22))
    path = QPainterPath(QPointF(14, 16))
    path.lineTo(QPointF(20, 22))
    path.lineTo(QPointF(26, 16))
    painter.drawPath(path)


def _settings(painter, color):
    _stroke(painter, color, 3)
    for y, knob in ((12, 26), (20, 15), (28, 23)):
        painter.drawLine(QPointF(7, y), QPointF(33, y))
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(knob, y), 3.2, 3.2)
        painter.setBrush(Qt.BrushStyle.NoBrush)


def _about(painter, color):
    _stroke(painter, color, 3)
    painter.drawEllipse(QPointF(20, 20), 13, 13)
    painter.drawLine(QPointF(20, 19), QPointF(20, 27))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(20, 13.5), 1.6, 1.6)


# --- stat-card chips ------------------------------------------------------

def _temperature(painter, color):
    _stroke(painter, color, 3)
    painter.drawLine(QPointF(20, 9), QPointF(20, 24))
    painter.drawEllipse(QPointF(20, 28), 5, 5)


def _battery(painter, color):
    _stroke(painter, color, 3)
    painter.drawRoundedRect(QRectF(7, 13, 22, 14), 3, 3)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(31, 17, 3, 6), 1.5, 1.5)
    painter.drawRoundedRect(QRectF(10, 16, 10, 8), 1.5, 1.5)


def _inflow(painter, color):
    _stroke(painter, color, 3)
    painter.drawLine(QPointF(31, 9), QPointF(31, 31))
    painter.drawLine(QPointF(9, 20), QPointF(26, 20))
    path = QPainterPath(QPointF(20, 14))
    path.lineTo(QPointF(26, 20))
    path.lineTo(QPointF(20, 26))
    painter.drawPath(path)


def _outflow(painter, color):
    _stroke(painter, color, 3)
    painter.drawLine(QPointF(9, 9), QPointF(9, 31))
    painter.drawLine(QPointF(14, 20), QPointF(31, 20))
    path = QPainterPath(QPointF(25, 14))
    path.lineTo(QPointF(31, 20))
    path.lineTo(QPointF(25, 26))
    painter.drawPath(path)


# --- header controls ------------------------------------------------------

def _sun(painter, color):
    _stroke(painter, color, 3)
    painter.drawEllipse(QPointF(20, 20), 7, 7)
    for x1, y1, x2, y2 in ((20, 4, 20, 9), (20, 31, 20, 36), (4, 20, 9, 20), (31, 20, 36, 20),
                           (9, 9, 12, 12), (28, 28, 31, 31), (28, 12, 31, 9), (9, 31, 12, 28)):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _moon(painter, color):
    path = QPainterPath()
    path.addEllipse(QPointF(20, 20), 13, 13)
    cut = QPainterPath()
    cut.addEllipse(QPointF(27, 13), 12, 12)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(path.subtracted(cut))


def _more(painter, color):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    for x in (11, 20, 29):
        painter.drawEllipse(QPointF(x, 20), 2.6, 2.6)


GLYPHS = {
    "overview": _overview,
    "live": _live,
    "incidents": _incidents,
    "exports": _exports,
    "settings": _settings,
    "about": _about,
    "temperature": _temperature,
    "battery": _battery,
    "inflow": _inflow,
    "outflow": _outflow,
    "sun": _sun,
    "moon": _moon,
    "more": _more,
}


def glyph(name: str, color: str) -> QIcon:
    """Return the named glyph tinted with ``color``; unknown names draw nothing."""
    return _icon(GLYPHS.get(name, lambda painter, color: None), color)


def app_icon(tokens: dict) -> QIcon:
    """Window and tray icon: a battery mark on the theme's surface colour."""
    pixmap, painter = _canvas(64)
    try:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens["surface"]))
        painter.drawRoundedRect(QRectF(0, 0, 64, 64), 12, 12)
        painter.setBrush(QColor(tokens["accent"]))
        painter.drawRoundedRect(QRectF(13, 18, 36, 28), 6, 6)
        painter.drawRoundedRect(QRectF(50, 26, 5, 12), 2, 2)
    finally:
        painter.end()
    return QIcon(pixmap)
