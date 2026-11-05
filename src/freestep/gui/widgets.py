"""Custom painted widgets: pressure heat map and COP trajectory plot.

Both widgets paint everything themselves with :class:`~PySide6.QtGui.QPainter`,
so no plotting library is needed. Coordinates follow the image convention:
x grows to the right, y grows downward (row 0 at the top).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .colormap import LUT, colorize
from .i18n import tr

#: Colour of the COP of the whole plate.
COP_TOTAL_COLOR = QColor(20, 20, 20)
#: Colour of the COP of the left foot.
COP_LEFT_COLOR = QColor(150, 0, 170)
#: Colour of the COP of the right foot.
COP_RIGHT_COLOR = QColor(0, 120, 120)
#: Colour of the marker of the current frame in :class:`CopPlot`.
CURRENT_COLOR = QColor(220, 0, 0)

_COLORBAR_WIDTH = 14
_COLORBAR_GAP = 10
_COLORBAR_LABEL_WIDTH = 34


# Vertical colour bar image (max value at the top), created on first use.
_COLORBAR: QImage | None = None


def _colorbar_image() -> QImage:
    """Return the 1 px wide colour bar image of :data:`~freestep.gui.colormap.LUT`."""
    global _COLORBAR  # noqa: PLW0603 — lazily created, needs a QGuiApplication
    if _COLORBAR is None:
        lut = np.ascontiguousarray(LUT[::-1].reshape(len(LUT), 1, 4))
        _COLORBAR = QImage(lut.data, 1, len(LUT), 4, QImage.Format.Format_ARGB32).copy()
    return _COLORBAR


class HeatmapView(QWidget):
    """Pressure heat map of one frame with COP markers, an optional COP trail and a colour bar.

    The plate keeps its physical aspect ratio. Display options are plain
    attributes; call :meth:`update` after changing them.

    Attributes:
        smooth: Interpolate between sensor cells instead of drawing sharp cells.
        show_grid: Draw the sensor grid.
        show_cop: Draw the COP markers and the trail.
    """

    #: Emitted on mouse move: column, row, value (or -1, -1, -1 when outside).
    hovered = Signal(int, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty view (shows a placeholder text until :meth:`set_frame`).

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._values: NDArray[np.generic] | None = None
        self._image: QImage | None = None
        self._max_value = 255.0
        self._cell_mm = (5.0, 5.0)
        self._cops: list[tuple[QColor, NDArray[np.floating] | None]] = []
        self._trail: NDArray[np.floating] | None = None
        self.smooth = False
        self.show_grid = False
        self.show_cop = True

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Return the preferred size."""
        return QSize(720, 600)

    def clear(self) -> None:
        """Remove the frame and show the placeholder text."""
        self._values = None
        self._image = None
        self._cops = []
        self._trail = None
        self.update()

    def set_frame(
        self,
        values: NDArray[np.generic],
        max_value: float,
        cell_mm: tuple[float, float],
        cops: list[tuple[QColor, NDArray[np.floating] | None]] | None = None,
        trail: NDArray[np.floating] | None = None,
    ) -> None:
        """Show a frame.

        Args:
            values: Pressure values, shape ``(rows, columns)``; the array is
                referenced, not copied (used for the hover values).
            max_value: Value mapped to the top of the colour scale.
            cell_mm: Cell width and height in mm (sets the aspect ratio).
            cops: Markers as ``(colour, (x, y) in cm)``; ``None`` or all-zero
                positions are skipped.
            trail: COP positions ``(n, 2)`` in cm drawn as a polyline.
        """
        self._values = values
        self._max_value = max_value
        self._cell_mm = cell_mm
        rgba = colorize(values, max_value)
        rows, cols = values.shape
        # copy() detaches the image from the numpy buffer
        self._image = QImage(rgba.data, cols, rows, cols * 4, QImage.Format.Format_ARGB32).copy()
        self._cops = cops or []
        self._trail = trail
        self.update()

    def image(self) -> QImage | None:
        """Render the current content at the widget size, e.g. for saving as PNG.

        Returns:
            The rendered image, or ``None`` when no frame is shown.
        """
        if self._image is None:
            return None
        out = QImage(self.size(), QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.white)
        painter = QPainter(out)
        self._paint(painter)
        painter.end()
        return out

    # -- geometry -------------------------------------------------------------

    def _target_rect(self) -> QRectF:
        """Area of the plate in widget coordinates (keeps the physical aspect ratio)."""
        if self._values is None:
            return QRectF()
        rows, cols = self._values.shape
        width_mm, height_mm = cols * self._cell_mm[0], rows * self._cell_mm[1]
        avail = QRectF(self.rect()).adjusted(8, 8, -(8 + _COLORBAR_GAP + _COLORBAR_WIDTH + _COLORBAR_LABEL_WIDTH), -8)
        scale = min(avail.width() / width_mm, avail.height() / height_mm)
        w, h = width_mm * scale, height_mm * scale
        return QRectF(avail.left() + (avail.width() - w) / 2, avail.top() + (avail.height() - h) / 2, w, h)

    def _cm_to_widget(self, rect: QRectF, x_cm: float, y_cm: float) -> QPointF:
        """Convert a COP position in cm to widget coordinates inside ``rect``."""
        assert self._values is not None
        rows, cols = self._values.shape
        # COP is index * cell size, i.e. it refers to the cell's top-left corner;
        # shift by half a cell to the cell centre.
        cx = x_cm * 10 / self._cell_mm[0] + 0.5
        cy = y_cm * 10 / self._cell_mm[1] + 0.5
        return QPointF(rect.left() + cx / cols * rect.width(), rect.top() + cy / rows * rect.height())

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint the widget (Qt event handler)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        self._paint(painter)
        painter.end()

    def _paint(self, painter: QPainter) -> None:
        """Paint the heat map, grid, COP and colour bar with ``painter`` (widget or image)."""
        if self._image is None or self._values is None:
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No exam loaded"))
            return
        rect = self._target_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
        painter.drawImage(rect, self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rows, cols = self._values.shape
        if self.show_grid:
            painter.setPen(QPen(QColor(0, 0, 0, 25), 0))
            for c in range(1, cols):
                x = rect.left() + c / cols * rect.width()
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            for r in range(1, rows):
                y = rect.top() + r / rows * rect.height()
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # Plate outline and midline (the COP halves split at columns / 2)
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()))

        if self.show_cop:
            if self._trail is not None and len(self._trail) > 1:
                path = QPainterPath(self._cm_to_widget(rect, *self._trail[0]))
                for x, y in self._trail[1:]:
                    path.lineTo(self._cm_to_widget(rect, x, y))
                painter.setPen(QPen(QColor(0, 0, 0, 140), 1.2))
                painter.drawPath(path)
            for colour, cop in self._cops:
                if cop is None or not np.all(np.isfinite(cop)) or not np.any(cop):
                    continue
                point = self._cm_to_widget(rect, float(cop[0]), float(cop[1]))
                painter.setPen(QPen(Qt.GlobalColor.white, 2))
                painter.setBrush(colour)
                painter.drawEllipse(point, 5, 5)

        self._paint_colorbar(painter, rect)

    def _paint_colorbar(self, painter: QPainter, rect: QRectF) -> None:
        """Paint the colour bar with value labels to the right of the plate ``rect``."""
        bar = QRectF(rect.right() + _COLORBAR_GAP, rect.top(), _COLORBAR_WIDTH, rect.height())
        painter.drawImage(bar, _colorbar_image())
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(bar)
        painter.setPen(self.palette().text().color())
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = bar.bottom() - fraction * bar.height()
            painter.drawText(
                QRectF(bar.right() + 3, y - 8, _COLORBAR_LABEL_WIDTH, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{fraction * self._max_value:.0f}",
            )

    # -- mouse ----------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Emit :attr:`hovered` for the cell under the mouse (Qt event handler)."""
        rect = self._target_rect()
        pos = event.position()
        if self._values is None or not rect.contains(pos):
            self.hovered.emit(-1, -1, -1)
            return
        rows, cols = self._values.shape
        col = min(int((pos.x() - rect.left()) / rect.width() * cols), cols - 1)
        row = min(int((pos.y() - rect.top()) / rect.height() * rows), rows - 1)
        self.hovered.emit(col, row, int(self._values[row, col]))


class CopPlot(QWidget):
    """Stabilogram: COP trajectories over the whole recording with equal axis scales.

    The view is zoomed to the data; all-zero positions (frames without load)
    are ignored. The position of the current frame is marked on every series.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty plot.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setMinimumSize(240, 200)
        self._series: list[tuple[str, QColor, NDArray[np.floating]]] = []
        self._current = 0

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Return the preferred size."""
        return QSize(400, 360)

    def clear(self) -> None:
        """Remove all series."""
        self._series = []
        self.update()

    def set_series(self, series: list[tuple[str, QColor, NDArray[np.floating]]]) -> None:
        """Replace the plotted series.

        Args:
            series: ``(legend label, colour, positions)`` per series; positions
                have shape ``(frames, 2)`` as (x, y) in cm.
        """
        self._series = [(label, colour, np.asarray(data, dtype=np.float64)) for label, colour, data in series]
        self.update()

    def set_current(self, frame: int) -> None:
        """Mark the position of a frame.

        Args:
            frame: Zero-based frame index.
        """
        self._current = frame
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint grid, axes, trajectories, current positions and legend (Qt event handler)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        text_colour = self.palette().text().color()
        if not self._series:
            painter.setPen(text_colour)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No COP data"))
            return

        valid = [d[np.all(np.isfinite(d), axis=1) & np.any(d != 0, axis=1)] for _, _, d in self._series]
        points = np.concatenate([v for v in valid if len(v)]) if any(len(v) for v in valid) else np.zeros((1, 2))
        lo, hi = points.min(axis=0), points.max(axis=0)
        span = max(float((hi - lo).max()), 0.5) * 1.1
        centre = (lo + hi) / 2

        margin_left, margin_bottom, margin = 46, 34, 12
        area = QRectF(self.rect()).adjusted(margin_left, margin + 18, -margin, -margin_bottom)
        side = min(area.width(), area.height())
        plot = QRectF(area.left() + (area.width() - side) / 2, area.top() + (area.height() - side) / 2, side, side)

        def to_px(x: float, y: float) -> QPointF:
            """Convert a position in cm to widget coordinates."""
            return QPointF(
                plot.left() + ((x - centre[0]) / span + 0.5) * plot.width(),
                plot.top() + ((y - centre[1]) / span + 0.5) * plot.height(),  # y grows downward like the image
            )

        # Grid with "nice" step
        raw_step = span / 5
        magnitude = 10 ** np.floor(np.log10(raw_step))
        step = float(next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw_step))
        painter.setPen(QPen(QColor(128, 128, 128, 60), 1))
        x_min, x_max = centre[0] - span / 2, centre[0] + span / 2
        y_min, y_max = centre[1] - span / 2, centre[1] + span / 2
        for x in np.arange(np.ceil(x_min / step) * step, x_max, step):
            p = to_px(float(x), y_min)
            painter.setPen(QPen(QColor(128, 128, 128, 60), 1))
            painter.drawLine(QPointF(p.x(), plot.top()), QPointF(p.x(), plot.bottom()))
            painter.setPen(text_colour)
            painter.drawText(QRectF(p.x() - 30, plot.bottom() + 4, 60, 14), Qt.AlignmentFlag.AlignCenter, f"{x:g}")
        for y in np.arange(np.ceil(y_min / step) * step, y_max, step):
            p = to_px(x_min, float(y))
            painter.setPen(QPen(QColor(128, 128, 128, 60), 1))
            painter.drawLine(QPointF(plot.left(), p.y()), QPointF(plot.right(), p.y()))
            painter.setPen(text_colour)
            painter.drawText(
                QRectF(plot.left() - 44, p.y() - 7, 40, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{y:g}",
            )
        painter.setPen(QPen(QColor(128, 128, 128), 1))
        painter.drawRect(plot)
        painter.setPen(text_colour)
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 18, plot.width(), 14), Qt.AlignmentFlag.AlignCenter, "x [cm]"
        )

        painter.setClipRect(plot)
        for (_, colour, _), data in zip(self._series, valid, strict=True):
            if len(data) < 2:
                continue
            path = QPainterPath(to_px(*data[0]))
            for x, y in data[1:]:
                path.lineTo(to_px(x, y))
            painter.setPen(QPen(colour, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        for _, _, data in self._series:
            if 0 <= self._current < len(data) and np.any(data[self._current]):
                painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
                painter.setBrush(CURRENT_COLOR)
                painter.drawEllipse(to_px(*data[self._current]), 4, 4)
        painter.setClipping(False)

        # Legend
        x = plot.left()
        for label, colour, _ in self._series:
            painter.setPen(QPen(colour, 3))
            painter.drawLine(QPointF(x, margin + 7), QPointF(x + 16, margin + 7))
            painter.setPen(text_colour)
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QPointF(x + 20, margin + 11), label)
            x += 20 + width + 14
