"""State of the main window shared by its parts.

:class:`~freestep.gui.app.MainWindow` is put together from mixins, one per area
(opening exams, playback, analysis, history, export). They all derive from
:class:`WindowBase`, which declares the attributes the window creates and the
methods one part calls in another, so that every part can be type checked.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSettings, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QWidget,
)

from ..archive import FreeStepExport, FreFile
from ..history import ExamAnalysis
from ..postura import Acquisition
from ..stabilometry import Romberg, Stabilometry
from .exam import LoadedExam
from .tasks import Task
from .widgets import CopPlot, HeatmapView, HistoryPlot, StabilogramPlot

#: Tree item data role holding the exam key: ``("exam", exam_id)``, ``("file", path)`` or ``("patient", id)``.
EXAM_ROLE = Qt.ItemDataRole.UserRole


class WindowBase(QMainWindow):
    """Attributes and cross-part methods of the main window (implemented by the mixins)."""

    # Background work and the open data
    _pool: QThreadPool
    _tasks: set[Task]
    _export: FreeStepExport | None
    _export_name: str
    _current: LoadedExam | None
    _loading_exam_id: object
    _current_key: object
    _password: str
    _timer: QTimer

    # Settings
    _settings: QSettings
    _language_pref: str
    _view_options: dict[str, bool]
    _lowpass_hz: float
    _eyes_closed_first: bool
    _detrend: bool
    _show_ellipse: bool
    _compare: bool
    _cop_origin: int
    _history_parameter: str

    # Analysis results
    _stabilometry: list[Stabilometry | None]
    _romberg: Romberg | None
    _history_cache: dict[tuple[object, ...], ExamAnalysis]
    _history_patient: int | None
    _history_analyses: list[ExamAnalysis]
    _history_cancel: threading.Event | None

    # Actions
    _window_actions: list[QAction]
    _frame_actions: list[QAction]
    export_actions: list[QAction]
    batch_action: QAction

    # Widgets
    tree: QTreeWidget
    splitter: QSplitter
    tabs: QTabWidget
    heatmap: HeatmapView
    acquisition: QComboBox
    mode: QComboBox
    play_button: QToolButton
    slider: QSlider
    frame_label: QLabel
    fps: QSpinBox
    trail: QSpinBox
    cop_plot: CopPlot
    cop_origin: QComboBox
    ellipse_check: QCheckBox
    info: QTextBrowser
    preview: QLabel
    lowpass: QDoubleSpinBox
    eyes_closed_first: QCheckBox
    detrend: QCheckBox
    stabilometry_view: QTextBrowser
    stabilogram: StabilogramPlot
    speed_curve: StabilogramPlot
    compare_checks: list[QCheckBox]
    history_title: QLabel
    history_parameter: QComboBox
    history_export: QPushButton
    history_plot: HistoryPlot
    history_tab: QWidget

    # Methods one part calls in another; the mixins implement them.

    def _run(
        self, func: Callable[[], object], done: Callable[[Any], None], fail: Callable[[BaseException], None]
    ) -> None:
        raise NotImplementedError

    def _error(self, title: str, exc: BaseException) -> None:
        raise NotImplementedError

    def _load(self, open_fre: Callable[[], FreFile], key: object) -> None:
        raise NotImplementedError

    def _stop(self) -> None:
        raise NotImplementedError

    def _seek(self, frame: int) -> None:
        raise NotImplementedError

    def _set_exam_controls_enabled(self, enabled: bool) -> None:
        raise NotImplementedError

    def _current_acquisition(self) -> Acquisition | None:
        raise NotImplementedError

    def _on_acquisition_changed(self) -> None:
        raise NotImplementedError

    def _update_stabilometry(self) -> None:
        raise NotImplementedError

    def _update_stabilogram(self) -> None:
        raise NotImplementedError

    def _update_history(self) -> None:
        raise NotImplementedError
