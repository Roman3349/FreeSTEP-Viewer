"""The pressure heat map: display mode, frame selection and playback."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget

from ..postura import Acquisition
from .exam import MODE_FRAME, show_on_heatmap
from .i18n import tr
from .widgets import COP_LEFT_COLOR, COP_RIGHT_COLOR, COP_TOTAL_COLOR, HeatmapView
from .window_base import WindowBase


class PlaybackMixin(WindowBase):
    """The pressure heat map: display mode, frame selection and playback."""

    def _build_pressure_view(self) -> QWidget:
        """Create the pressure heat map with the playback controls."""
        self.heatmap = HeatmapView()
        self.heatmap.hovered.connect(self._on_hover)
        for attribute, value in self._view_options.items():
            setattr(self.heatmap, attribute, value)

        self.acquisition = QComboBox()
        self.acquisition.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.acquisition.setToolTip(tr("Acquisition (e.g. eyes open / eyes closed)"))
        self.acquisition.currentIndexChanged.connect(self._on_acquisition_changed)

        self.mode = QComboBox()
        self.mode.addItems([tr("Frame"), tr("Maximum"), tr("Mean")])
        self.mode.currentIndexChanged.connect(self._refresh_frame)
        self.mode.currentIndexChanged.connect(self._update_frame_controls)

        self.play_button = QToolButton()
        self.play_button.setText("▶")
        self.play_button.setToolTip(tr("Play / pause (Space)"))
        self.play_button.clicked.connect(self._toggle_play)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._refresh_frame)

        self.frame_label = QLabel("–")
        self.frame_label.setMinimumWidth(110)

        self.fps = QSpinBox()
        self.fps.setRange(1, 200)
        self.fps.setValue(25)
        self.fps.setSuffix(tr(" fps"))
        self.fps.setToolTip(tr("Playback speed in frames per second (set to the sampling rate of the acquisition)"))
        self.fps.valueChanged.connect(self._update_timer)

        self.trail = QSpinBox()
        self.trail.setRange(0, 5000)
        self.trail.setValue(50)
        self.trail.setPrefix(tr("trail "))
        self.trail.setToolTip(tr("Number of previous frames drawn as the COP trail"))
        self.trail.valueChanged.connect(self._refresh_frame)

        controls = QHBoxLayout()
        controls.addWidget(self.acquisition)
        controls.addWidget(self.mode)
        controls.addWidget(self.play_button)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.frame_label)
        controls.addWidget(self.fps)
        controls.addWidget(self.trail)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.heatmap, 1)
        left_layout.addLayout(controls)
        return left

    def _set_exam_controls_enabled(self, enabled: bool) -> None:
        """Enable the playback controls and export actions (only useful with a loaded exam)."""
        for widget in (self.acquisition, self.mode, self.play_button, self.slider, self.fps, self.trail):
            widget.setEnabled(enabled)
        for action in self.export_actions:
            action.setEnabled(enabled)
        self._update_frame_controls()

    @Slot()
    def _update_frame_controls(self) -> None:
        """Enable playback, its speed and the time axis only for single frames (not for the maximum / mean image)."""
        enabled = self._current is not None and self.mode.currentIndex() == MODE_FRAME
        if not enabled:
            self._stop()
        for widget in (self.play_button, self.slider, self.fps):
            widget.setEnabled(enabled)
        for action in self._frame_actions:
            action.setEnabled(enabled)

    @Slot(int)
    def _seek(self, frame: int) -> None:
        """Show a frame (clicked in the stabilogram); pauses playback."""
        self._stop()
        self.mode.setCurrentIndex(MODE_FRAME)
        self.slider.setValue(frame)

    def _current_acquisition(self) -> Acquisition | None:
        """Return the acquisition selected in the selection box."""
        if self._current is None:
            return None
        acquisitions = self._current.postura.acquisitions
        return acquisitions[min(max(self.acquisition.currentIndex(), 0), len(acquisitions) - 1)]

    @Slot()
    def _on_acquisition_changed(self) -> None:
        """Show the selected acquisition from its first frame."""
        acq = self._current_acquisition()
        if acq is None:
            return
        self._stop()
        self.slider.blockSignals(True)
        self.slider.setRange(0, acq.frame_count - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.cop_plot.plate_cm = (acq.header.width_cm, acq.header.height_cm)
        self.cop_plot.set_series(
            [
                (tr("total"), COP_TOTAL_COLOR, acq.cop_total),
                (tr("left"), COP_LEFT_COLOR, acq.cop_left),
                (tr("right"), COP_RIGHT_COLOR, acq.cop_right),
            ]
        )
        self.fps.setValue(max(1, round(acq.sampling_rate_hz)) if acq.sampling_rate_hz else self.fps.value())
        self._update_stabilogram()
        self._refresh_frame()

    @Slot()
    def _refresh_frame(self) -> None:
        """Redraw the heat map for the current frame / display mode and move the COP marker."""
        loaded, acq = self._current, self._current_acquisition()
        if loaded is None or acq is None:
            return
        frame = min(self.slider.value(), acq.frame_count - 1)
        mode = self.mode.currentIndex()
        show_on_heatmap(self.heatmap, loaded, acq, frame, mode, trail=self.trail.value())
        if mode == MODE_FRAME:
            sums = loaded.pressure_sums[acq.index]
            self.frame_label.setText(f"{frame + 1} / {acq.frame_count}  {acq.time_s[frame]:.2f} s  Σ {sums[frame]}")
        else:
            self.frame_label.setText(tr("all frames"))
        self.cop_plot.set_current(frame)
        self.stabilogram.set_current(frame)
        self.speed_curve.set_current(frame)

    @Slot(int, int, int)
    def _on_hover(self, col: int, row: int, value: int) -> None:
        """Show the position and value of the sensor under the mouse in the status bar."""
        if col < 0 or self._current is None:
            return
        h = self._current.postura.header
        self.statusBar().showMessage(
            tr(
                "x {x} cm (col {col}), y {y} cm (row {row}): {value}",
                x=f"{col * h.cell_width_mm / 10:.1f}",
                col=col,
                y=f"{row * h.cell_height_mm / 10:.1f}",
                row=row,
                value=value,
            )
        )

    def _toggle_play(self) -> None:
        """Start or pause playback (frame mode only); starting at the end rewinds."""
        if self._current is None or self.mode.currentIndex() != MODE_FRAME:
            return
        if self._timer.isActive():
            self._stop()
        else:
            if self.slider.value() >= self.slider.maximum():
                self.slider.setValue(0)
            self._update_timer()
            self._timer.start()
            self.play_button.setText("⏸")

    def _stop(self) -> None:
        """Pause playback."""
        self._timer.stop()
        self.play_button.setText("▶")

    def _update_timer(self) -> None:
        """Apply the playback speed to the timer."""
        self._timer.setInterval(max(1, round(1000 / self.fps.value())))

    def _advance_frame(self) -> None:
        """Show the next frame; stop at the last one (timer handler)."""
        if self.slider.value() >= self.slider.maximum():
            self._stop()
        else:
            self.slider.setValue(self.slider.value() + 1)
