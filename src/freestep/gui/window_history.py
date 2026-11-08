"""The History tab: one parameter over all exams of a subject."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..batch import safe_name, write_summary_csv
from ..history import (
    ROMBERG_CONDITION,
    ROMBERG_PREFIX,
    ExamAnalysis,
    analyze_exam,
    analyze_exams,
    history_rows,
    patient_exams,
    series,
)
from ..models import Exam
from ..stabilometry import unit
from .i18n import ntr, tr
from .tasks import Progress
from .texts import history_parameters, plain_value
from .widgets import AP_COLOR, COP_LEFT_COLOR, COP_RIGHT_COLOR, COP_TOTAL_COLOR, ML_COLOR, HistoryPlot
from .window_base import WindowBase


class HistoryMixin(WindowBase):
    """The History tab: one parameter over all exams of a subject."""

    def _build_history_tab(self) -> None:
        """Create the History tab (:attr:`history_tab`)."""
        self.history_title = QLabel()
        self.history_parameter = QComboBox()
        for name, label in history_parameters():
            self.history_parameter.addItem(label, name)
        index = self.history_parameter.findData(self._history_parameter)
        self.history_parameter.setCurrentIndex(max(index, 0))
        self.history_parameter.currentIndexChanged.connect(self._on_history_parameter)
        self.history_export = QPushButton(tr("Export CSV…"))
        self.history_export.setToolTip(tr("All parameters of all exams of the subject"))
        self.history_export.clicked.connect(self._export_history)
        self.history_plot = HistoryPlot()
        self.history_plot.selected.connect(self._on_history_selected)
        history_note = QLabel(
            tr("Exams of the subject, oldest first; click an exam to show it. Settings of the Stabilometry tab apply.")
        )
        history_note.setWordWrap(True)
        history_controls = QHBoxLayout()
        history_controls.addWidget(self.history_parameter, 1)
        history_controls.addWidget(self.history_export)
        self.history_tab = QWidget()
        history_layout = QVBoxLayout(self.history_tab)
        history_layout.setContentsMargins(0, 4, 0, 0)
        history_layout.addWidget(self.history_title)
        history_layout.addLayout(history_controls)
        history_layout.addWidget(self.history_plot, 1)
        history_layout.addWidget(history_note)

    def _history_key(self, exam: Exam) -> tuple[object, ...]:
        """Cache key of an exam's analysis with the current analysis settings."""
        return (exam.id, self._lowpass_hz or None, self._detrend, self._eyes_closed_first)

    @Slot()
    def _update_history(self) -> None:
        """Show the history of :attr:`_history_patient` when its tab is visible.

        Exams not analysed yet with the current settings are decrypted in the
        worker thread first (a few seconds each); a newer request cancels it.
        """
        if self.tabs.currentWidget() is not self.history_tab:
            return
        export, patient_id = self._export, self._history_patient
        if export is None or patient_id is None:
            self.history_title.setText("")
            self.history_plot.set_message(tr("Open a FreeSTEP export (.zip) to see the history of a subject."))
            return
        patient = export.patient(patient_id)
        exams = patient_exams(export, patient_id)
        name = patient.name if patient is not None else str(patient_id)
        self.history_title.setText(ntr("{name}: {n} exam", "{name}: {n} exams", len(exams), name=name))
        current = self._current
        if current is not None and current.exam.patient_id == patient_id:
            key = self._history_key(current.exam)
            if key not in self._history_cache:
                self._history_cache[key] = analyze_exam(
                    current.postura,
                    current.exam,
                    lowpass_hz=self._lowpass_hz or None,
                    detrend=self._detrend,
                    eyes_closed_first=self._eyes_closed_first,
                )
        if self._history_cancel is not None:
            self._history_cancel.set()
            self._history_cancel = None
        missing = [e for e in exams if self._history_key(e) not in self._history_cache]
        if missing:
            self._analyse_history(exams, missing)
        else:
            self._history_analyses = [self._history_cache[self._history_key(e)] for e in exams]
            self._draw_history()

    def _analyse_history(self, exams: list[Exam], missing: list[Exam]) -> None:
        """Analyse ``missing`` in the worker thread, then show the history of ``exams``."""
        export = self._export
        assert export is not None
        self.history_plot.set_message(ntr("Analysing {n} exam…", "Analysing {n} exams…", len(missing)))
        cancel = threading.Event()
        self._history_cancel = cancel
        reporter = Progress()

        def changed(index: int, count: int, exam_id: object) -> None:
            text = tr("Analysing exam {id} ({n} of {count})…", id=exam_id, n=index + 1, count=count)
            self.statusBar().showMessage(text)

        reporter.changed.connect(changed)
        lowpass_hz, detrend, eyes_closed_first = self._lowpass_hz or None, self._detrend, self._eyes_closed_first

        def work() -> tuple[list[ExamAnalysis], list[tuple[Exam, str]]]:
            def report(index: int, count: int, exam: Exam) -> bool:
                reporter.changed.emit(index, count, exam.id)
                return not cancel.is_set()

            return analyze_exams(
                export,
                missing,
                lowpass_hz=lowpass_hz,
                detrend=detrend,
                eyes_closed_first=eyes_closed_first,
                progress=report,
            )

        def done(result: tuple[list[ExamAnalysis], list[tuple[Exam, str]]]) -> None:
            reporter.deleteLater()
            analyses, failed = result
            for analysis in analyses:
                self._history_cache[(analysis.exam.id, lowpass_hz, detrend, eyes_closed_first)] = analysis
            if cancel.is_set():
                return
            self._history_cancel = None
            self.statusBar().clearMessage()
            for exam, reason in failed:
                self.statusBar().showMessage(tr("Exam {id} skipped: {reason}", id=exam.id, reason=reason))
            keys = [self._history_key(e) for e in exams]
            self._history_analyses = [self._history_cache[k] for k in keys if k in self._history_cache]
            self._draw_history()

        def fail(exc: BaseException) -> None:
            reporter.deleteLater()
            if not cancel.is_set():
                self._error(tr("Cannot analyse the exams"), exc)

        self._run(work, done, fail)

    def _draw_history(self) -> None:
        """Plot the selected parameter of :attr:`_history_analyses`."""
        analyses = self._history_analyses
        parameter = str(self.history_parameter.currentData())
        names = {
            "eyes_open": tr("Eyes open"),
            "eyes_closed": tr("Eyes closed"),
            ROMBERG_CONDITION: tr("Romberg quotient"),
        }
        colours = {"eyes_open": ML_COLOR, "eyes_closed": AP_COLOR, ROMBERG_CONDITION: COP_TOTAL_COLOR}
        palette = [COP_LEFT_COLOR, COP_RIGHT_COLOR, ML_COLOR, AP_COLOR]
        plotted = []
        for i, (condition, values) in enumerate(series(analyses, parameter).items()):
            label = names.get(condition) or tr("Acquisition {number}", number=condition.removeprefix("acquisition_"))
            plotted.append((label, colours.get(condition, palette[i % len(palette)]), values))
        labels = [f"{plain_value(a.exam.exam_date)}\n{tr('Exam {id}', id=a.exam.id)}" for a in analyses]
        shown = self._current.exam.id if self._current is not None else None
        current = next((i for i, a in enumerate(analyses) if a.exam.id == shown), None)
        romberg = parameter.startswith(ROMBERG_PREFIX)
        reference = 1.0 if romberg else None  # quotient 1 = no difference between the eye conditions
        self.history_plot.set_data(labels, plotted, "" if romberg else unit(parameter), current, reference)

    @Slot(int)
    def _on_history_parameter(self, _index: int) -> None:
        """Remember the plotted parameter and redraw."""
        self._history_parameter = str(self.history_parameter.currentData())
        self._settings.setValue("history_parameter", self._history_parameter)
        if self._history_analyses:
            self._draw_history()

    @Slot(int)
    def _on_history_selected(self, index: int) -> None:
        """Show the exam clicked in the history plot."""
        if self._export is None or not 0 <= index < len(self._history_analyses):
            return
        exam_id = self._history_analyses[index].exam.id
        key = ("exam", exam_id)
        if exam_id is None or key == self._loading_exam_id or (self._current is not None and key == self._current_key):
            return
        export = self._export
        self._load(lambda: export.open_fre(exam_id), key)

    def _export_history(self) -> None:
        """Save all parameters of the shown patient's exams as CSV."""
        if not self._history_analyses or self._export is None or self._history_patient is None:
            return
        patient = self._export.patient(self._history_patient)
        stem = safe_name(patient.name) if patient is not None else str(self._history_patient)
        start = Path(str(self._settings.value("last_dir", str(Path.home())))) / f"{stem}_history.csv"
        path, _ = QFileDialog.getSaveFileName(self, tr("Export history"), str(start), tr("CSV (*.csv)"))
        if not path:
            return
        write_summary_csv(history_rows(self._history_analyses), path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))
