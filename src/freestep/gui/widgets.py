"""Custom painted widgets: pressure heat map, COP trajectory plot and stabilogram.

Both widgets paint everything themselves with :class:`~PySide6.QtGui.QPainter`,
so no plotting library is needed. Coordinates follow the image convention:
x grows to the right, y grows downward (row 0 at the top).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..stabilometry import LoadRegions
from .colormap import LUT, colorize
from .i18n import tr

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

        # Plate outline and midline (the COP halves split at columns / 2) or the load quadrants
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.drawRect(rect)
        regions = self._regions if self.show_regions else None
        if regions is None:
            painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()))
        else:
            self._paint_region_lines(painter, rect, regions)

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
        if regions is not None:
            self._paint_region_labels(painter, rect, regions)

        self._paint_colorbar(painter, rect)

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


class CopPlot(QWidget):
    """Stabilogram: COP trajectories over the whole recording with equal axis scales.

    The view is zoomed to the data; all-zero positions (frames without load)
    are ignored. The position of the current frame is marked on every series.

    Attributes:
        origin: Where (0, 0) is: :data:`ORIGIN_PLATE` (the plate corner, as stored),
            :data:`ORIGIN_PLATE_CENTRE` (every series is shifted by half the plate,
            :attr:`plate_cm`) or :data:`ORIGIN_MEAN` (every series is shifted by its
            mean position). In the latter two, (0, 0) is in the centre of the plot.
        plate_cm: Width and height of the plate in cm (for :data:`ORIGIN_PLATE_CENTRE`).
        show_ellipse: Draw the confidence ellipse given by :meth:`set_ellipse`
            (it belongs to the first series and moves with it).
        span_cm: Fixed width and height of the shown area with a centred origin
            (e.g. the same scale for several plots); ``None`` zooms to the data.
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
        self.origin = ORIGIN_PLATE
        self.plate_cm = (0.0, 0.0)
        self.show_ellipse = True
        self.span_cm: float | None = None
        self._ellipse: tuple[NDArray[np.float64], float, float, float] | None = None
        self._hover: QPointF | None = None
        self.setMouseTracking(True)

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

    def set_ellipse(
        self, centre_cm: tuple[float, float] | None, major_cm: float = 0, minor_cm: float = 0, angle_deg: float = 0
    ) -> None:
        """Set the confidence ellipse of the first series (``centre_cm=None`` removes it).

        Args:
            centre_cm: Centre (x, y) in cm, in the coordinates of the series.
            major_cm: Semi-major axis in cm.
            minor_cm: Semi-minor axis in cm.
            angle_deg: Angle of the major axis from the x axis.
        """
        centre = None if centre_cm is None else np.asarray(centre_cm, dtype=np.float64)
        self._ellipse = None if centre is None else (centre, major_cm, minor_cm, angle_deg)
        self.update()

    def _offsets(self, masks: list[NDArray[np.bool_]]) -> list[NDArray[np.float64]]:
        """Return the shift of every series that moves it to :attr:`origin`.

        Args:
            masks: Valid (loaded) positions of every series; the mean uses only these.
        """
        if self.origin == ORIGIN_MEAN:
            return [
                np.asarray(d[mask].mean(axis=0), dtype=np.float64) if mask.any() else np.zeros(2)
                for (_, _, d), mask in zip(self._series, masks, strict=True)
            ]
        if self.origin == ORIGIN_PLATE_CENTRE:
            return [np.asarray(self.plate_cm, dtype=np.float64) / 2 for _ in self._series]
        return [np.zeros(2) for _ in self._series]

    def _ellipse_points(self, offset: NDArray[np.float64]) -> NDArray[np.float64] | None:
        """Return the outline of the ellipse (shifted like the first series), or ``None`` when not shown."""
        if not self.show_ellipse or self._ellipse is None:
            return None
        centre, major, minor, angle = self._ellipse
        t = np.linspace(0, 2 * np.pi, 97)
        phi = np.radians(angle)
        u, v = np.array([np.cos(phi), np.sin(phi)]), np.array([-np.sin(phi), np.cos(phi)])
        return centre - offset + np.outer(major * np.cos(t), u) + np.outer(minor * np.sin(t), v)

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

        masks = [np.all(np.isfinite(d), axis=1) & np.any(d != 0, axis=1) for _, _, d in self._series]
        centred = self.origin != ORIGIN_PLATE
        offsets = self._offsets(masks)
        shown = [d - offset for (_, _, d), offset in zip(self._series, offsets, strict=True)]
        valid = [d[mask] for d, mask in zip(shown, masks, strict=True)]
        ellipse = self._ellipse_points(offsets[0])
        parts = [v for v in valid if len(v)] + ([ellipse] if ellipse is not None else [])
        points = np.concatenate(parts) if parts else np.zeros((1, 2))
        lo, hi = points.min(axis=0), points.max(axis=0)
        if centred:  # symmetric around (0, 0), which is in the centre
            span = self.span_cm or max(2 * float(np.abs(points).max()), 0.5) * 1.1
            centre = np.zeros(2)
        else:
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
        if centred:  # axes through (0, 0)
            painter.setPen(QPen(QColor(128, 128, 128, 160), 1, Qt.PenStyle.DashLine))
            origin = to_px(0.0, 0.0)
            painter.drawLine(QPointF(origin.x(), plot.top()), QPointF(origin.x(), plot.bottom()))
            painter.drawLine(QPointF(plot.left(), origin.y()), QPointF(plot.right(), origin.y()))
        painter.setPen(text_colour)
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 18, plot.width(), 14), Qt.AlignmentFlag.AlignCenter, "x [cm]"
        )

        painter.setClipRect(plot)
        base = self.palette().base().color()
        colours = [_readable(colour, base) for _, colour, _ in self._series]
        for colour, data in zip(colours, valid, strict=True):
            _draw_polyline(painter, [to_px(x, y) for x, y in data], QPen(colour, 1))
        if ellipse is not None:
            _draw_polyline(painter, [to_px(x, y) for x, y in ellipse], QPen(ELLIPSE_COLOR, 1.5, Qt.PenStyle.DashLine))
        for data, mask in zip(shown, masks, strict=True):
            if 0 <= self._current < len(data) and mask[self._current]:
                painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
                painter.setBrush(CURRENT_COLOR)
                painter.drawEllipse(to_px(*data[self._current]), 4, 4)
        painter.setClipping(False)

        entries = [(label, QPen(colour, 3)) for (label, _, _), colour in zip(self._series, colours, strict=True)]
        if ellipse is not None:
            entries.append((tr("95% ellipse"), QPen(ELLIPSE_COLOR, 2, Qt.PenStyle.DashLine)))
        _draw_legend(painter, QPointF(plot.left(), margin), entries, text_colour)

        if self._hover is not None and plot.contains(self._hover):
            painter.setPen(QPen(QColor(128, 128, 128, 160), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(self._hover.x(), plot.top()), QPointF(self._hover.x(), plot.bottom()))
            painter.drawLine(QPointF(plot.left(), self._hover.y()), QPointF(plot.right(), self._hover.y()))
            x = centre[0] + ((self._hover.x() - plot.left()) / plot.width() - 0.5) * span
            y = centre[1] + ((self._hover.y() - plot.top()) / plot.height() - 0.5) * span
            _draw_readout(painter, self, self._hover, [(f"x = {x:.2f} cm", None), (f"y = {y:.2f} cm", None)])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Show the position under the mouse (Qt event handler)."""
        self._hover = event.position()
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        """Hide the readout (Qt event handler)."""
        self._hover = None
        self.update()


def _draw_polyline(painter: QPainter, points: list[QPointF], pen: QPen) -> None:
    """Draw connected line segments through ``points`` (nothing for fewer than two)."""
    if len(points) < 2:
        return
    path = QPainterPath(points[0])
    for point in points[1:]:
        path.lineTo(point)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _draw_readout(painter: QPainter, widget: QWidget, anchor: QPointF, lines: list[tuple[str, QColor | None]]) -> None:
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


def _draw_legend(painter: QPainter, origin: QPointF, entries: list[tuple[str, QPen]], text_colour: QColor) -> None:
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
_MIN_LIGHTNESS_CONTRAST = 0.3
#: Difference of lightness from the background of a line adjusted by :func:`_readable`.
_ADJUSTED_LIGHTNESS_CONTRAST = 0.45


def _readable(colour: QColor, background: QColor) -> QColor:
    """Return ``colour`` with its lightness changed when it would be hard to see on ``background``.

    The hue is kept, so e.g. the near-black COP of the whole plate becomes light
    grey in a dark theme while the feet stay purple and teal.
    """
    lightness, ground = colour.lightnessF(), background.lightnessF()
    if abs(lightness - ground) >= _MIN_LIGHTNESS_CONTRAST:
        return colour
    target = ground + _ADJUSTED_LIGHTNESS_CONTRAST if ground < 0.5 else ground - _ADJUSTED_LIGHTNESS_CONTRAST
    return QColor.fromHslF(colour.hslHueF(), colour.hslSaturationF(), target, colour.alphaF())


def _nice_step(span: float, count: int) -> float:
    """Return a 1/2/5 × 10^n grid step giving about ``count`` intervals over ``span``."""
    raw = max(span, 1e-9) / count
    magnitude = 10 ** np.floor(np.log10(raw))
    return float(next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw))


class StabilogramPlot(QWidget):
    """Stabilogram: COP displacement over time, one panel per direction (ML above AP).

    All panels share the time axis and the value scale, so the sway in both
    directions can be compared. The current frame is marked by a vertical line;
    clicking or dragging emits :attr:`seeked`. Signals of further acquisitions
    can be drawn under the shown one for comparison (e.g. eyes open and closed).

    Attributes:
        symmetric: The value scale is symmetric around 0 (displacement); otherwise
            it starts at 0 (non-negative values such as the speed).

    Signals:
        seeked(int): Frame index of the acquisition under the mouse.
    """

    seeked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty plot.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setMinimumSize(240, 200)
        self._time: NDArray[np.float64] | None = None
        self._panels: list[tuple[str, QColor, NDArray[np.float64]]] = []
        self._label = ""
        self._others: list[tuple[str, QColor, NDArray[np.float64], list[NDArray[np.float64]]]] = []
        self._unit = ""
        self._offset = 0
        self._current = 0
        self._hover: QPointF | None = None
        self.symmetric = True
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Return the preferred size."""
        return QSize(400, 360)

    def clear(self) -> None:
        """Remove the data."""
        self._time = None
        self._panels = []
        self._others = []
        self.update()

    def set_data(
        self,
        time_s: NDArray[np.floating],
        panels: list[tuple[str, QColor, NDArray[np.floating]]],
        unit: str,
        frame_offset: int = 0,
        *,
        label: str = "",
        others: Sequence[tuple[str, QColor, NDArray[np.floating], Sequence[NDArray[np.floating]]]] = (),
    ) -> None:
        """Replace the plotted signals.

        Args:
            time_s: Sample times in s, shape ``(n,)``.
            panels: ``(title, colour, values (n,))`` per panel, top to bottom.
            unit: Unit of the values (axis label).
            frame_offset: Frame index of the first sample (the signals may leave
                out unloaded frames at the start of the acquisition).
            label: Legend label of the signals, shown with ``others``.
            others: Signals of further acquisitions drawn under the signals for
                comparison: ``(legend label, colour, times (m,), values (m,) per panel)``.
        """
        self._time = np.asarray(time_s, dtype=np.float64)
        self._panels = [(title, colour, np.asarray(v, dtype=np.float64)) for title, colour, v in panels]
        self._label = label
        self._others = [
            (name, colour, np.asarray(t, dtype=np.float64), [np.asarray(v, dtype=np.float64) for v in values])
            for name, colour, t, values in others
        ]
        self._unit = unit
        self._offset = frame_offset
        self.update()

    def set_current(self, frame: int) -> None:
        """Mark the time of a frame.

        Args:
            frame: Zero-based frame index of the acquisition.
        """
        self._current = frame
        self.update()

    # -- geometry -------------------------------------------------------------

    _MARGIN_LEFT, _MARGIN_RIGHT, _MARGIN_TOP, _MARGIN_BOTTOM, _GAP = 46, 12, 22, 34, 22

    def _panel_rects(self) -> list[QRectF]:
        """Areas of the panels in widget coordinates."""
        area = QRectF(self.rect()).adjusted(
            self._MARGIN_LEFT, self._MARGIN_TOP, -self._MARGIN_RIGHT, -self._MARGIN_BOTTOM
        )
        count = max(len(self._panels), 1)
        height = (area.height() - self._GAP * (count - 1)) / count
        return [QRectF(area.left(), area.top() + i * (height + self._GAP), area.width(), height) for i in range(count)]

    def _time_range(self) -> tuple[float, float]:
        """First and last time of the shown and the compared signals (the time axis)."""
        times = [t for t in [self._time, *(t for _, _, t, _ in self._others)] if t is not None and len(t) >= 2]
        return min(float(t[0]) for t in times), max(float(t[-1]) for t in times)

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint the panels with grid, signals and the current time (Qt event handler)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        text_colour = self.palette().text().color()
        time = self._time
        if time is None or len(time) < 2 or not self._panels:
            painter.setPen(text_colour)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No COP data"))
            return

        others = [(name, colour, t, values) for name, colour, t, values in self._others if len(t) >= 2]
        t0, t1 = self._time_range()
        signals = [v for _, _, v in self._panels] + [v for *_, values in others for v in values]
        limit = max(0.1, *(float(np.abs(v).max()) for v in signals)) * 1.1
        t_step = _nice_step(t1 - t0, 6)
        low = -limit if self.symmetric else 0.0
        v_step = _nice_step(limit - low, 4)
        grid_pen = QPen(QColor(128, 128, 128, 60), 1)
        current = self._current - self._offset

        for index, (rect, (title, colour, values)) in enumerate(zip(self._panel_rects(), self._panels, strict=True)):

            def to_px(t: float, v: float, rect: QRectF = rect) -> QPointF:
                """Convert (time, value) to widget coordinates inside the panel."""
                return QPointF(
                    rect.left() + (t - t0) / (t1 - t0) * rect.width(),
                    rect.bottom() - (v - low) / (limit - low) * rect.height(),
                )

            for t in np.arange(np.ceil(t0 / t_step) * t_step, t1 + 1e-9, t_step):
                x = to_px(float(t), 0).x()
                painter.setPen(grid_pen)
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                if index == len(self._panels) - 1:
                    painter.setPen(text_colour)
                    painter.drawText(QRectF(x - 30, rect.bottom() + 4, 60, 14), Qt.AlignmentFlag.AlignCenter, f"{t:g}")
            for v in np.arange(np.ceil(low / v_step) * v_step, limit + 1e-9, v_step):
                y = to_px(t0, float(v)).y()
                painter.setPen(QPen(QColor(128, 128, 128, 140 if abs(v) < 1e-9 else 60), 1))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(text_colour)
                painter.drawText(
                    QRectF(rect.left() - 44, y - 7, 40, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{v:g}",
                )
            painter.setPen(QPen(QColor(128, 128, 128), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            painter.setPen(text_colour)
            heading = f"{title} [{self._unit}]"
            painter.drawText(QPointF(rect.left(), rect.top() - 6), heading)
            base = self.palette().base().color()
            if others and index == 0:
                entries = [(self._label, QPen(_readable(colour, base), 3))]
                entries += [(name, QPen(_readable(c, base), 3)) for name, c, _, _ in others]
                x = rect.left() + painter.fontMetrics().horizontalAdvance(heading) + 24
                _draw_legend(painter, QPointF(x, rect.top() - 18), entries, text_colour)

            painter.setClipRect(rect)
            curves = [(c, t, other_values[index]) for _, c, t, other_values in others] + [(colour, time, values)]
            for curve_colour, curve_time, curve_values in curves:  # the shown acquisition on top
                _draw_polyline(
                    painter,
                    [to_px(float(t), float(v)) for t, v in zip(curve_time, curve_values, strict=True)],
                    QPen(_readable(curve_colour, base), 1),
                )
            if 0 <= current < len(time):
                x = to_px(float(time[current]), 0).x()
                painter.setPen(QPen(CURRENT_COLOR, 1.5))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setClipping(False)

        painter.setPen(text_colour)
        bottom = self._panel_rects()[-1]
        painter.drawText(
            QRectF(bottom.left(), bottom.bottom() + 18, bottom.width(), 14),
            Qt.AlignmentFlag.AlignCenter,
            tr("time [s]"),
        )
        self._paint_readout(painter, t0, t1)

    def _paint_readout(self, painter: QPainter, t0: float, t1: float) -> None:
        """Draw a guide line at the time under the mouse and the values of all signals at that time."""
        rects = self._panel_rects()
        hover = self._hover
        if hover is None or self._time is None or not rects[0].left() <= hover.x() <= rects[0].right():
            return
        t = t0 + (hover.x() - rects[0].left()) / rects[0].width() * (t1 - t0)
        painter.setPen(QPen(QColor(128, 128, 128, 200), 1, Qt.PenStyle.DotLine))
        for rect in rects:
            painter.drawLine(QPointF(hover.x(), rect.top()), QPointF(hover.x(), rect.bottom()))
        base = self.palette().base().color()
        curves = [(self._label, self._time, [(c, v) for _, c, v in self._panels])]
        curves += [(name, times, [(c, v) for v in values]) for name, c, times, values in self._others]
        lines: list[tuple[str, QColor | None]] = [(tr("time {value} s", value=f"{t:.2f}"), None)]
        for index, (title, _, _) in enumerate(self._panels):
            for name, times, signals in curves:
                if len(times) < 2 or not times[0] <= t <= times[-1]:
                    continue
                colour, values = signals[index]
                value = values[min(int(np.searchsorted(times, t)), len(values) - 1)]
                label = f"{title}, {name}" if self._others else title
                lines.append((f"{label}: {value:.2f} {self._unit}", _readable(colour, base)))
        _draw_readout(painter, self, hover, lines)

    # -- mouse ----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Seek to the clicked time (Qt event handler)."""
        self._seek(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Show the values under the mouse; seek while dragging (Qt event handler)."""
        self._hover = event.position()
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        """Hide the readout (Qt event handler)."""
        self._hover = None
        self.update()

    def _seek(self, event: QMouseEvent) -> None:
        """Emit :attr:`seeked` with the frame nearest to the mouse position."""
        time = self._time
        if time is None or len(time) < 2:
            return
        rect = self._panel_rects()[0]
        fraction = min(max((event.position().x() - rect.left()) / rect.width(), 0.0), 1.0)
        t0, t1 = self._time_range()
        t = t0 + fraction * (t1 - t0)
        self.seeked.emit(int(np.searchsorted(time, t).clip(0, len(time) - 1)) + self._offset)


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
        step = _nice_step(hi - lo, 5)
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
        colours = [_readable(colour, base) for _, colour, _ in self._series]
        for colour, (_, _, series_values) in zip(colours, self._series, strict=True):
            points = [
                QPointF(self._x(rect, i), y_px(v)) if v is not None else None for i, v in enumerate(series_values)
            ]
            run: list[QPointF] = []
            for point in [*points, None]:  # lines connect neighbouring exams that have a value
                if point is None:
                    _draw_polyline(painter, run, QPen(colour, 1.5))
                    run = []
                else:
                    run.append(point)
            painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
            painter.setBrush(colour)
            for point in points:
                if point is not None:
                    painter.drawEllipse(point, 4, 4)
        entries = [(name, QPen(colour, 3)) for (name, _, _), colour in zip(self._series, colours, strict=True)]
        _draw_legend(painter, QPointF(rect.left(), 6), entries, text_colour)
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
        _draw_readout(painter, self, hover, lines)

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
