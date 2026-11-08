"""Colours and drawing helpers shared by the widgets."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

#: Colour of the COP of the whole plate.
COP_TOTAL_COLOR = QColor(20, 20, 20)
#: Colour of the COP of the left foot.
COP_LEFT_COLOR = QColor(150, 0, 170)
#: Colour of the COP of the right foot.
COP_RIGHT_COLOR = QColor(0, 120, 120)
#: Colour of the confidence ellipse in :class:`CopPlot`.
ELLIPSE_COLOR = QColor(220, 120, 0)
#: :attr:`CopPlot.origin`: (0, 0) in the plate corner (coordinates as stored by FreeSTEP).
ORIGIN_PLATE = 0
#: :attr:`CopPlot.origin`: (0, 0) in the centre of the plate.
ORIGIN_PLATE_CENTRE = 1
#: :attr:`CopPlot.origin`: (0, 0) at the mean position of every series.
ORIGIN_MEAN = 2

#: Colour of the medio-lateral (x) signal in :class:`StabilogramPlot`.
ML_COLOR = QColor(31, 119, 180)
#: Colour of the antero-posterior (y) signal in :class:`StabilogramPlot`.
AP_COLOR = QColor(214, 95, 0)
#: Colour of the marker of the current frame in :class:`CopPlot`.
CURRENT_COLOR = QColor(220, 0, 0)


def draw_polyline(painter: QPainter, points: list[QPointF], pen: QPen) -> None:
    """Draw connected line segments through ``points`` (nothing for fewer than two)."""
    if len(points) < 2:
        return
    path = QPainterPath(points[0])
    for point in points[1:]:
        path.lineTo(point)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def draw_readout(painter: QPainter, widget: QWidget, anchor: QPointF, lines: list[tuple[str, QColor | None]]) -> None:
    """Draw a box with values read at the mouse position next to ``anchor``, kept inside the widget.

    Args:
        painter: Active painter.
        widget: The plot (its palette and size).
        anchor: Mouse position.
        lines: ``(text, colour of the sample line or None)`` per line.
    """
    metrics = painter.fontMetrics()
    swatch, padding, line_height = 14, 6, metrics.height()
    width = max(metrics.horizontalAdvance(text) + (swatch + 4 if colour else 0) for text, colour in lines) + 2 * padding
    height = len(lines) * line_height + 2 * padding
    x = anchor.x() + 14 if anchor.x() + 14 + width <= widget.width() else anchor.x() - 14 - width
    y = anchor.y() + 14 if anchor.y() + 14 + height <= widget.height() else anchor.y() - 14 - height
    box = QRectF(max(x, 0.0), max(y, 0.0), width, height)
    background = QColor(widget.palette().base().color())
    background.setAlpha(225)
    painter.setPen(QPen(QColor(128, 128, 128), 1))
    painter.setBrush(background)
    painter.drawRoundedRect(box, 3, 3)
    text_colour = widget.palette().text().color()
    for index, (text, colour) in enumerate(lines):
        top = box.top() + padding + index * line_height
        left = box.left() + padding
        if colour is not None:
            painter.setPen(QPen(colour, 3))
            painter.drawLine(QPointF(left, top + line_height / 2), QPointF(left + swatch, top + line_height / 2))
            left += swatch + 4
        painter.setPen(text_colour)
        painter.drawText(QPointF(left, top + metrics.ascent()), text)


def draw_legend(painter: QPainter, origin: QPointF, entries: list[tuple[str, QPen]], text_colour: QColor) -> None:
    """Draw a one-line legend: a line sample in the entry's pen and its label, left to right.

    Args:
        painter: Active painter.
        origin: Top-left corner of the legend.
        entries: ``(label, pen)`` per entry.
        text_colour: Colour of the labels.
    """
    x, y = origin.x(), origin.y()
    for label, pen in entries:
        painter.setPen(pen)
        painter.drawLine(QPointF(x, y + 7), QPointF(x + 16, y + 7))
        painter.setPen(text_colour)
        painter.drawText(QPointF(x + 20, y + 11), label)
        x += 20 + painter.fontMetrics().horizontalAdvance(label) + 14


#: Minimum difference of lightness between a line and the plot background (0…1).
MIN_LIGHTNESS_CONTRAST = 0.3
#: Difference of lightness from the background of a line adjusted by :func:`readable`.
ADJUSTED_LIGHTNESS_CONTRAST = 0.45


def readable(colour: QColor, background: QColor) -> QColor:
    """Return ``colour`` with its lightness changed when it would be hard to see on ``background``.

    The hue is kept, so e.g. the near-black COP of the whole plate becomes light
    grey in a dark theme while the feet stay purple and teal.
    """
    lightness, ground = colour.lightnessF(), background.lightnessF()
    if abs(lightness - ground) >= MIN_LIGHTNESS_CONTRAST:
        return colour
    target = ground + ADJUSTED_LIGHTNESS_CONTRAST if ground < 0.5 else ground - ADJUSTED_LIGHTNESS_CONTRAST
    return QColor.fromHslF(colour.hslHueF(), colour.hslSaturationF(), target, colour.alphaF())


def nice_step(span: float, count: int) -> float:
    """Return a 1/2/5 × 10^n grid step giving about ``count`` intervals over ``span``."""
    raw = max(span, 1e-9) / count
    magnitude = 10 ** np.floor(np.log10(raw))
    return float(next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw))
