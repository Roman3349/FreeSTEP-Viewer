"""Pressure heat map of one frame with COP markers, trail, load quadrants and a colour bar."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...analysis.load import LoadRegions
from ..colormap import LUT, colorize
from ..i18n import tr
from .common import COP_LEFT_COLOR, COP_RIGHT_COLOR, COP_TOTAL_COLOR

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
        show_regions: Draw the load quadrants given by :meth:`set_regions`.
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
        self.show_regions = True
        #: Number of plates in a row shown side by side (gait recordings); their boundaries replace the midline.
        self.plates = 1
        self._regions: LoadRegions | None = None
        self._shares: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None)

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

    def set_regions(
        self,
        regions: LoadRegions | None,
        shares: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None),
    ) -> None:
        """Set the load quadrants drawn over the image (``None`` removes them).

        Args:
            regions: Split between the feet and their front and rear halves, with their COPs.
            shares: Load shares in % of the left front, left rear, right front and right rear quadrant.
        """
        self._regions, self._shares = regions, shares
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

        regions = self._regions if self.show_regions else None
        self._paint_outline(painter, rect, regions)

        if self.show_cop:
            self._paint_trail(painter, rect)
            for colour, cop in self._cops:
                if cop is None or not np.all(np.isfinite(cop)) or not np.any(cop):
                    continue
                point = self._cm_to_widget(rect, float(cop[0]), float(cop[1]))
                painter.setPen(QPen(Qt.GlobalColor.white, 2))
                painter.setBrush(colour)
                painter.drawEllipse(point, 5, 5)
        if regions is not None:
            self._paint_region_labels(painter, rect, regions)

        self._paint_colorbar(painter, rect)

    def _paint_outline(self, painter: QPainter, rect: QRectF, regions: LoadRegions | None) -> None:
        """Draw the plate outline with the midline (COP halves at columns / 2), the plate boundaries or quadrants."""
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.drawRect(rect)
        for plate in range(1, self.plates):
            x = rect.left() + plate / self.plates * rect.width()
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if regions is not None:
            self._paint_region_lines(painter, rect, regions)
        elif self.plates == 1:
            painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()))

    def _paint_trail(self, painter: QPainter, rect: QRectF) -> None:
        """Draw the COP trail, interrupted at frames without load (they have no COP)."""
        if self._trail is None or len(self._trail) < 2:
            return
        path = QPainterPath()
        gap = True
        for x, y in self._trail:
            if not (x or y) or not (np.isfinite(x) and np.isfinite(y)):
                gap = True
            elif gap:
                path.moveTo(self._cm_to_widget(rect, x, y))
                gap = False
            else:
                path.lineTo(self._cm_to_widget(rect, x, y))
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1.2))
        painter.drawPath(path)

    def _cell_to_widget(self, rect: QRectF, column: float, row: float) -> QPointF:
        """Convert a position in cells (boundaries at integers) to widget coordinates inside ``rect``."""
        assert self._values is not None
        rows, cols = self._values.shape
        return QPointF(rect.left() + column / cols * rect.width(), rect.top() + row / rows * rect.height())

    def _paint_region_lines(self, painter: QPainter, rect: QRectF, regions: LoadRegions) -> None:
        """Draw the split between the feet and between the front and rear half of each foot."""
        painter.setPen(QPen(QColor(60, 60, 60), 1.2, Qt.PenStyle.DashLine))
        split = self._cell_to_widget(rect, regions.split_column, 0).x()
        painter.drawLine(QPointF(split, rect.top()), QPointF(split, rect.bottom()))
        for foot in (regions.left, regions.right):
            if foot is not None:
                painter.drawLine(
                    self._cell_to_widget(rect, foot.first_column - 1, foot.middle_row),
                    self._cell_to_widget(rect, foot.last_column + 2, foot.middle_row),
                )

    def _paint_region_labels(self, painter: QPainter, rect: QRectF, regions: LoadRegions) -> None:
        """Draw the COPs of the plate and the feet, the load shares of the quadrants and of the sides of the plate."""
        cell_w, cell_h = self._cell_mm
        markers = [
            (COP_LEFT_COLOR, regions.left.cop if regions.left else None),
            (COP_RIGHT_COLOR, regions.right.cop if regions.right else None),
            (COP_TOTAL_COLOR, regions.cop),
        ]
        for colour, cop in markers:
            if cop is not None:
                point = self._cm_to_widget(rect, cop[0] * cell_w / 10, cop[1] * cell_h / 10)
                painter.setPen(QPen(Qt.GlobalColor.white, 2))
                painter.setBrush(colour)
                painter.drawEllipse(point, 5, 5)

        # Quadrants in the corners of the plate (left / right foot, front at the top),
        # sums of the sides in the middle of the edges
        left_front, left_rear, right_front, right_rear = self._shares

        def total(a: float | None, b: float | None) -> float | None:
            return None if a is None and b is None else (a or 0.0) + (b or 0.0)

        labels = (
            (left_front, -1, -1),
            (left_rear, -1, 1),
            (right_front, 1, -1),
            (right_rear, 1, 1),
            (total(left_front, left_rear), -1, 0),
            (total(right_front, right_rear), 1, 0),
            (total(left_front, right_front), 0, -1),
            (total(left_rear, right_rear), 0, 1),
        )
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for share, horizontal, vertical in labels:
            if share is None:
                continue
            text = f"{share:.1f} %"
            width, height = metrics.horizontalAdvance(text) + 8, metrics.height() + 2
            x = {-1: rect.left() + 6, 0: rect.center().x() - width / 2, 1: rect.right() - 6 - width}[horizontal]
            y = {-1: rect.top() + 6, 0: rect.center().y() - height / 2, 1: rect.bottom() - 6 - height}[vertical]
            box = QRectF(x, y, width, height)
            painter.setPen(QPen(QColor(60, 60, 60), 1))
            painter.setBrush(QColor(255, 255, 255, 220))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        font.setBold(False)
        painter.setFont(font)

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
