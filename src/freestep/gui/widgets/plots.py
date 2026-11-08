"""COP trajectory (statokinesigram) and COP signals over time (stabilogram, speed curve)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..i18n import tr
from .common import (
    CURRENT_COLOR,
    ELLIPSE_COLOR,
    ORIGIN_MEAN,
    ORIGIN_PLATE,
    ORIGIN_PLATE_CENTRE,
    draw_legend,
    draw_polyline,
    draw_readout,
    nice_step,
    readable,
)


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
        colours = [readable(colour, base) for _, colour, _ in self._series]
        for colour, data in zip(colours, valid, strict=True):
            draw_polyline(painter, [to_px(x, y) for x, y in data], QPen(colour, 1))
        if ellipse is not None:
            draw_polyline(painter, [to_px(x, y) for x, y in ellipse], QPen(ELLIPSE_COLOR, 1.5, Qt.PenStyle.DashLine))
        for data, mask in zip(shown, masks, strict=True):
            if 0 <= self._current < len(data) and mask[self._current]:
                painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
                painter.setBrush(CURRENT_COLOR)
                painter.drawEllipse(to_px(*data[self._current]), 4, 4)
        painter.setClipping(False)

        entries = [(label, QPen(colour, 3)) for (label, _, _), colour in zip(self._series, colours, strict=True)]
        if ellipse is not None:
            entries.append((tr("95% ellipse"), QPen(ELLIPSE_COLOR, 2, Qt.PenStyle.DashLine)))
        draw_legend(painter, QPointF(plot.left(), margin), entries, text_colour)

        if self._hover is not None and plot.contains(self._hover):
            painter.setPen(QPen(QColor(128, 128, 128, 160), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(self._hover.x(), plot.top()), QPointF(self._hover.x(), plot.bottom()))
            painter.drawLine(QPointF(plot.left(), self._hover.y()), QPointF(plot.right(), self._hover.y()))
            x = centre[0] + ((self._hover.x() - plot.left()) / plot.width() - 0.5) * span
            y = centre[1] + ((self._hover.y() - plot.top()) / plot.height() - 0.5) * span
            draw_readout(painter, self, self._hover, [(f"x = {x:.2f} cm", None), (f"y = {y:.2f} cm", None)])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Show the position under the mouse (Qt event handler)."""
        self._hover = event.position()
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        """Hide the readout (Qt event handler)."""
        self._hover = None
        self.update()


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
        t_step = nice_step(t1 - t0, 6)
        low = -limit if self.symmetric else 0.0
        v_step = nice_step(limit - low, 4)
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
                entries = [(self._label, QPen(readable(colour, base), 3))]
                entries += [(name, QPen(readable(c, base), 3)) for name, c, _, _ in others]
                x = rect.left() + painter.fontMetrics().horizontalAdvance(heading) + 24
                draw_legend(painter, QPointF(x, rect.top() - 18), entries, text_colour)

            painter.setClipRect(rect)
            curves = [(c, t, other_values[index]) for _, c, t, other_values in others] + [(colour, time, values)]
            for curve_colour, curve_time, curve_values in curves:  # the shown acquisition on top
                draw_polyline(
                    painter,
                    [to_px(float(t), float(v)) for t, v in zip(curve_time, curve_values, strict=True)],
                    QPen(readable(curve_colour, base), 1),
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
                lines.append((f"{label}: {value:.2f} {self._unit}", readable(colour, base)))
        draw_readout(painter, self, hover, lines)

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
