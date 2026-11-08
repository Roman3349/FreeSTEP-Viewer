"""Values of one parameter over the exams of a subject."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..i18n import tr
from .common import CURRENT_COLOR, draw_legend, draw_polyline, draw_readout, nice_step, readable


class HistoryPlot(QWidget):
    """Values of one parameter over the exams of a patient, one series per condition.

    The exams are evenly spaced on the x axis (oldest left) and labelled with
    their date; the shown exam is highlighted. Clicking an exam emits :attr:`selected`.

    Signals:
        selected(int): Index of the clicked exam.
    """

    selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty plot.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setMinimumSize(240, 200)
        self._labels: list[str] = []
        self._series: list[tuple[str, QColor, list[float | None]]] = []
        self._unit = ""
        self._current: int | None = None
        self._reference: float | None = None
        self._message = ""
        self._hover: QPointF | None = None
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Return the preferred size."""
        return QSize(400, 360)

    def set_message(self, text: str) -> None:
        """Remove the data and show ``text`` instead."""
        self._labels, self._series, self._message = [], [], text
        self.update()

    def set_data(
        self,
        labels: list[str],
        series: list[tuple[str, QColor, list[float | None]]],
        unit: str,
        current: int | None = None,
        reference: float | None = None,
    ) -> None:
        """Replace the plotted values.

        Args:
            labels: Axis label of every exam (a line break starts a new line).
            series: ``(legend label, colour, value per exam or None)`` per series.
            unit: Unit of the values (axis label).
            current: Index of the exam to highlight.
            reference: Value drawn as a dashed horizontal line (e.g. 1 for quotients).
        """
        self._labels, self._series, self._unit = labels, series, unit
        self._current, self._reference, self._message = current, reference, ""
        self.update()

    def _plot_rect(self) -> QRectF:
        """Area of the plot in widget coordinates."""
        return QRectF(self.rect()).adjusted(56, 34, -16, -44)

    def _x(self, rect: QRectF, index: int) -> float:
        """Horizontal position of an exam."""
        return rect.left() + (index + 0.5) / max(len(self._labels), 1) * rect.width()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint grid, the current exam, the series and the legend (Qt event handler)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        text_colour = self.palette().text().color()
        values = [v for _, _, vs in self._series for v in vs if v is not None]
        if not self._labels or not values:
            painter.setPen(text_colour)
            painter.drawText(
                self.rect().adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message or tr("No data"),
            )
            return

        lo, hi = min(values), max(values)
        if self._reference is not None:
            lo, hi = min(lo, self._reference), max(hi, self._reference)
        pad = (hi - lo) * 0.1 or max(abs(hi) * 0.1, 1.0)
        lo, hi = lo - pad, hi + pad
        rect = self._plot_rect()

        def y_px(v: float) -> float:
            return rect.bottom() - (v - lo) / (hi - lo) * rect.height()

        if self._current is not None and 0 <= self._current < len(self._labels):
            width = rect.width() / len(self._labels)
            highlight = QColor(CURRENT_COLOR)
            highlight.setAlpha(30)
            left = self._x(rect, self._current) - width / 2
            painter.fillRect(QRectF(left, rect.top(), width, rect.height()), highlight)
        step = nice_step(hi - lo, 5)
        for v in np.arange(np.ceil(lo / step) * step, hi, step):
            y = y_px(float(v))
            painter.setPen(QPen(QColor(128, 128, 128, 60), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(text_colour)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(QRectF(rect.left() - 54, y - 7, 50, 14), align, f"{v:g}")
        if self._reference is not None:
            painter.setPen(QPen(QColor(128, 128, 128, 200), 1, Qt.PenStyle.DashLine))
            y = y_px(self._reference)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for index, label in enumerate(self._labels):
            x = self._x(rect, index)
            painter.setPen(text_colour)
            painter.drawText(QRectF(x - 60, rect.bottom() + 4, 120, 36), Qt.AlignmentFlag.AlignHCenter, label)
        painter.setPen(QPen(QColor(128, 128, 128), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.setPen(text_colour)
        painter.drawText(QPointF(rect.left() - 50, rect.top() - 8), f"[{self._unit}]" if self._unit else "")

        base = self.palette().base().color()
        colours = [readable(colour, base) for _, colour, _ in self._series]
        for colour, (_, _, series_values) in zip(colours, self._series, strict=True):
            points = [
                QPointF(self._x(rect, i), y_px(v)) if v is not None else None for i, v in enumerate(series_values)
            ]
            run: list[QPointF] = []
            for point in [*points, None]:  # lines connect neighbouring exams that have a value
                if point is None:
                    draw_polyline(painter, run, QPen(colour, 1.5))
                    run = []
                else:
                    run.append(point)
            painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
            painter.setBrush(colour)
            for point in points:
                if point is not None:
                    painter.drawEllipse(point, 4, 4)
        entries = [(name, QPen(colour, 3)) for (name, _, _), colour in zip(self._series, colours, strict=True)]
        draw_legend(painter, QPointF(rect.left(), 6), entries, text_colour)
        self._paint_readout(painter, rect, colours)

    def _paint_readout(self, painter: QPainter, rect: QRectF, colours: list[QColor]) -> None:
        """Draw the values of the exam under the mouse."""
        hover = self._hover
        exam = self._index_at(hover.x()) if hover is not None and rect.contains(hover) else None
        if hover is None or exam is None:
            return
        lines: list[tuple[str, QColor | None]] = [(self._labels[exam].replace("\n", ", "), None)]
        for (name, _, series_values), colour in zip(self._series, colours, strict=True):
            value = series_values[exam]
            text = "–" if value is None else f"{value:.3g} {self._unit}".rstrip()
            lines.append((f"{name}: {text}", colour))
        draw_readout(painter, self, hover, lines)

    def _index_at(self, x: float) -> int | None:
        """Return the exam at a horizontal position, ``None`` outside the plot."""
        rect = self._plot_rect()
        if not self._labels or not rect.width():
            return None
        index = int((x - rect.left()) / rect.width() * len(self._labels))
        return index if 0 <= index < len(self._labels) else None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Emit :attr:`selected` for the exam under the mouse (Qt event handler)."""
        index = self._index_at(event.position().x())
        if index is not None:
            self.selected.emit(index)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Show the values of the exam under the mouse (Qt event handler)."""
        self._hover = event.position()
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        """Hide the readout (Qt event handler)."""
        self._hover = None
        self.update()
