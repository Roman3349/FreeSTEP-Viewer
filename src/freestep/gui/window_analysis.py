"""The COP, stabilogram, speed and stabilometry tabs."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..postura import Acquisition
from ..stabilometry import analyze_all, evaluate_romberg, speed, sway
from .i18n import tr
from .texts import acquisition_style, directions_note, stabilometry_html
from .widgets import AP_COLOR, COP_TOTAL_COLOR, ML_COLOR, CopPlot, StabilogramPlot
from .window_base import WindowBase


class AnalysisMixin(WindowBase):
    """The COP, stabilogram, speed and stabilometry tabs."""

    def _build_cop_tab(self) -> QWidget:
        """Create the tab with the COP trajectory (statokinesigram)."""
        self.cop_plot = CopPlot()
        self.cop_origin = QComboBox()
        self.cop_origin.addItems([tr("Plate corner"), tr("Plate centre (0, 0)"), tr("Mean position (0, 0)")])
        self.cop_origin.setToolTip(
            tr("Origin of the coordinates: plate corner (as stored), plate centre or the mean of every trajectory")
        )
        self.cop_origin.setCurrentIndex(min(max(self._cop_origin, 0), self.cop_origin.count() - 1))
        self.cop_plot.origin = self.cop_origin.currentIndex()
        self.cop_origin.currentIndexChanged.connect(self._on_cop_origin)
        self.ellipse_check = QCheckBox(tr("95% ellipse"))
        self.ellipse_check.setToolTip(tr("Confidence ellipse of the total COP, as in the Stabilometry tab"))
        self.ellipse_check.setChecked(self._show_ellipse)
        self.cop_plot.show_ellipse = self._show_ellipse
        self.ellipse_check.toggled.connect(self._on_show_ellipse)
        cop_controls = QHBoxLayout()
        cop_controls.addWidget(QLabel(tr("Origin")))
        cop_controls.addWidget(self.cop_origin)
        cop_controls.addSpacing(12)
        cop_controls.addWidget(self.ellipse_check)
        cop_controls.addStretch(1)
        cop_tab = QWidget()
        cop_layout = QVBoxLayout(cop_tab)
        cop_layout.setContentsMargins(0, 4, 0, 0)
        cop_layout.addLayout(cop_controls)
        cop_layout.addWidget(self.cop_plot, 1)
        return cop_tab

    def _build_stabilometry_tab(self) -> QWidget:
        """Create the tab with the analysis settings and the stabilometric parameters."""
        self.lowpass = QDoubleSpinBox()
        self.lowpass.setRange(0.0, 12.0)
        self.lowpass.setSingleStep(0.5)
        self.lowpass.setDecimals(1)
        self.lowpass.setSuffix(tr(" Hz"))
        self.lowpass.setSpecialValueText(tr("unfiltered"))
        self.lowpass.setValue(self._lowpass_hz)
        self.lowpass.setToolTip(tr("Cut-off of the zero-phase low-pass filter of the COP (4th order Butterworth)"))
        self.lowpass.valueChanged.connect(self._on_analysis_options_changed)
        self.eyes_closed_first = QCheckBox(tr("Eyes closed first"))
        self.eyes_closed_first.setChecked(self._eyes_closed_first)
        self.eyes_closed_first.setToolTip(
            tr(
                "Romberg test: FreeSTEP does not store the eye condition and measures eyes open first "
                "(classic protocol); check when the first acquisition was with eyes closed"
            )
        )
        self.eyes_closed_first.toggled.connect(self._on_analysis_options_changed)
        self.detrend = QCheckBox(tr("Remove trend"))
        self.detrend.setChecked(self._detrend)
        self.detrend.setToolTip(
            tr("Remove the linear trend (slow drift) of the COP instead of only its mean; affects the sway parameters")
        )
        self.detrend.toggled.connect(self._on_analysis_options_changed)
        self.stabilometry_view = QTextBrowser()
        analysis_controls = QHBoxLayout()
        analysis_controls.addWidget(QLabel(tr("Low-pass filter")))
        analysis_controls.addWidget(self.lowpass)
        analysis_controls.addSpacing(12)
        analysis_controls.addWidget(self.detrend)
        analysis_controls.addSpacing(12)
        analysis_controls.addWidget(self.eyes_closed_first)
        analysis_controls.addStretch(1)
        analysis = QWidget()
        analysis_layout = QVBoxLayout(analysis)
        analysis_layout.setContentsMargins(0, 4, 0, 0)
        analysis_layout.addLayout(analysis_controls)
        analysis_layout.addWidget(self.stabilometry_view, 1)
        return analysis

    def _build_signal_tabs(self) -> tuple[QWidget, QWidget]:
        """Create the stabilogram and speed curve tabs."""
        self.stabilogram = StabilogramPlot()
        self.stabilogram.seeked.connect(self._seek)
        stabilogram_note = QLabel(
            tr(
                "COP of the whole plate minus its mean (or trend), filtered as set in the Stabilometry tab. "
                "Click to seek."
            )
            + " "
            + directions_note()
        )
        stabilogram_note.setWordWrap(True)
        # One check box per tab, kept in sync by _on_compare
        self.compare_checks = []
        for _ in range(2):
            check = QCheckBox(tr("Compare acquisitions"))
            check.setToolTip(tr("Draw the other acquisitions of the exam (e.g. eyes open and closed) in the same plot"))
            check.setChecked(self._compare)
            check.toggled.connect(self._on_compare)
            self.compare_checks.append(check)
        stabilogram_tab = QWidget()
        stabilogram_layout = QVBoxLayout(stabilogram_tab)
        stabilogram_layout.setContentsMargins(0, 4, 0, 0)
        stabilogram_layout.addWidget(stabilogram_note)
        stabilogram_layout.addWidget(self.compare_checks[0])
        stabilogram_layout.addWidget(self.stabilogram, 1)

        self.speed_curve = StabilogramPlot()
        self.speed_curve.symmetric = False
        self.speed_curve.seeked.connect(self._seek)
        speed_note = QLabel(
            tr(
                "Instantaneous speed of the COP of the whole plate, filtered as set in the Stabilometry tab; "
                "its mean is the mean velocity. Click to seek."
            )
        )
        speed_note.setWordWrap(True)
        speed_tab = QWidget()
        speed_layout = QVBoxLayout(speed_tab)
        speed_layout.setContentsMargins(0, 4, 0, 0)
        speed_layout.addWidget(speed_note)
        speed_layout.addWidget(self.compare_checks[1])
        speed_layout.addWidget(self.speed_curve, 1)
        return stabilogram_tab, speed_tab

    @Slot(bool)
    def _on_show_ellipse(self, show: bool) -> None:
        """Show or hide the confidence ellipse in the COP plot."""
        self._show_ellipse = show
        self._settings.setValue("show_ellipse", "true" if show else "false")
        self.cop_plot.show_ellipse = show
        self.cop_plot.update()

    @Slot(bool)
    def _on_compare(self, compare: bool) -> None:
        """Draw the other acquisitions in the stabilogram and the speed curve, or only the selected one."""
        self._compare = compare
        self._settings.setValue("compare_acquisitions", "true" if compare else "false")
        for check in self.compare_checks:
            check.blockSignals(True)
            check.setChecked(compare)
            check.blockSignals(False)
        self._update_stabilogram()

    @Slot(int)
    def _on_cop_origin(self, origin: int) -> None:
        """Move the origin of the COP plot (plate corner, plate centre or mean position)."""
        self._cop_origin = origin
        self._settings.setValue("cop_origin", origin)
        self.cop_plot.origin = origin
        self.cop_plot.update()

    @Slot()
    def _on_analysis_options_changed(self) -> None:
        """Remember the filter / Romberg order and recompute the stabilometric parameters."""
        self._lowpass_hz = self.lowpass.value()
        self._eyes_closed_first = self.eyes_closed_first.isChecked()
        self._detrend = self.detrend.isChecked()
        self._settings.setValue("lowpass_hz", self._lowpass_hz)
        self._settings.setValue("detrend", "true" if self._detrend else "false")
        self._settings.setValue("eyes_closed_first", "true" if self._eyes_closed_first else "false")
        self._update_stabilometry()
        self._update_history()

    def _update_stabilometry(self) -> None:
        """Compute the stabilometric parameters and the Romberg test of the shown exam and show them."""
        if self._current is None:
            self._stabilometry, self._romberg = [], None
            self.stabilometry_view.clear()
            return
        post = self._current.postura
        self._stabilometry = analyze_all(post, self._lowpass_hz or None, detrend=self._detrend)
        self._romberg = evaluate_romberg(
            post, self._stabilometry, self._current.exam.exam_type, self._eyes_closed_first
        )
        self.eyes_closed_first.setEnabled(self._romberg is not None and not self._romberg.order_from_labels)
        self.stabilometry_view.setHtml(stabilometry_html(post, self._stabilometry, self._romberg))
        self._update_stabilogram()

    def _update_stabilogram(self) -> None:
        """Show the COP displacement and speed of the selected acquisition over time and its ellipse in the COP plot."""
        acq = self._current_acquisition()
        result = self._stabilometry[acq.index] if acq is not None and acq.index < len(self._stabilometry) else None
        if result is None:
            self.cop_plot.set_ellipse(None)
        else:
            self.cop_plot.set_ellipse(
                (result.mean_x_mm / 10, result.mean_y_mm / 10),
                result.ellipse_major_mm / 10,
                result.ellipse_minor_mm / 10,
                result.ellipse_angle_deg,
            )
        signals = self._signals(acq) if acq is not None else None
        if acq is None or signals is None:
            self.stabilogram.clear()
            self.speed_curve.clear()
            return
        time, cop, curve, first = signals
        others = []
        if self._compare and self._current is not None:
            for other in self._current.postura.acquisitions:
                if other.index != acq.index and (other_signals := self._signals(other)) is not None:
                    others.append((other, other_signals))
        if others:
            # Colour by acquisition (as in the History tab), the same in all panels
            label, colour = self._acquisition_style(acq)
            styles = [self._acquisition_style(other) for other, _ in others]
            displacement = [
                (name, c, t, [o[:, 0], o[:, 1]]) for (name, c), (_, (t, o, _, _)) in zip(styles, others, strict=True)
            ]
            speeds = [(name, c, t[1:], [v]) for (name, c), (_, (t, _, v, _)) in zip(styles, others, strict=True)]
            self.stabilogram.set_data(
                time,
                [(tr("ML (x)"), colour, cop[:, 0]), (tr("AP (y)"), colour, cop[:, 1])],
                "mm",
                first,
                label=label,
                others=displacement,
            )
            self.speed_curve.set_data(
                time[1:], [(tr("COP speed"), colour, curve)], "mm/s", first + 1, label=label, others=speeds
            )
        else:
            self.stabilogram.set_data(
                time, [(tr("ML (x)"), ML_COLOR, cop[:, 0]), (tr("AP (y)"), AP_COLOR, cop[:, 1])], "mm", first
            )
            # The speed of a step belongs to the frame it ends at
            self.speed_curve.set_data(time[1:], [(tr("COP speed"), COP_TOTAL_COLOR, curve)], "mm/s", first + 1)
        for plot in (self.stabilogram, self.speed_curve):
            plot.set_current(self.slider.value())

    def _signals(
        self, acq: Acquisition
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], int] | None:
        """Return ``(times, COP displacement, speed, first frame)`` of an acquisition, ``None`` when too short."""
        try:
            cop, first = sway(acq, self._lowpass_hz or None, detrend=self._detrend)
        except ValueError:
            return None
        time = (first + np.arange(len(cop))) / acq.sampling_rate_hz  # uniform, like the analysis
        return time, cop, speed(cop, acq.sampling_rate_hz), first

    def _acquisition_style(self, acq: Acquisition) -> tuple[str, QColor]:
        """Legend label and colour of an acquisition compared in the stabilogram and the speed curve."""
        return acquisition_style(acq.index, self._romberg)
