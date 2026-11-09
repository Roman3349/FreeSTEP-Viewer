"""FreeSTEP Viewer — Qt (PySide6) desktop application.

Layout: patient/exam tree on the left, heat map with playback controls in the
middle, COP plot / exam info / preview tabs on the right. Decrypting and
decoding runs in a single worker thread, so the window stays responsive.

Run with ``freestep-viewer [file]`` or ``python -m freestep.gui [file]``.
"""

from __future__ import annotations

import html
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QMessageLogContext,
    QObject,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    QtMsgType,
    qInstallMessageHandler,
)
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFileOpenEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
)

from ..archive import DEFAULT_PASSWORD, FreeStepExport
from ..history import ExamAnalysis
from ..stabilometry import DEFAULT_LOWPASS_HZ, Romberg, Stabilometry
from .exam import LoadedExam
from .i18n import (
    AUTO_LANGUAGE,
    LANGUAGES,
    install_qt_translator,
    language,
    resolve_language,
    set_language,
    system_language,
    tr,
)
from .meta import APP_ID, APP_NAME, APP_URL, ICON_PATH
from .tasks import Task
from .texts import setting_float
from .widgets import ORIGIN_PLATE, ImagePanel
from .widgets.video import VideoPlayer
from .window_analysis import AnalysisMixin
from .window_export import ExportMixin
from .window_history import HistoryMixin
from .window_open import OpenMixin
from .window_playback import PlaybackMixin
from .window_video import VideoMixin

# -- main window ---------------------------------------------------------------


class MainWindow(OpenMixin, PlaybackMixin, VideoMixin, AnalysisMixin, HistoryMixin, ExportMixin):
    """Main window of the viewer.

    Holds at most one open export (:attr:`_export`) and one shown exam
    (:attr:`_current`). All texts go through :func:`~freestep.gui.i18n.tr`;
    switching the language rebuilds the widgets while keeping the loaded data.
    """

    def __init__(self) -> None:
        """Create the window with an empty view and restore the saved geometry."""
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.resize(1280, 800)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)  # ZipFile access is serialised
        self._tasks: set[Task] = set()
        self._export: FreeStepExport | None = None
        self._export_name = ""
        self._current: LoadedExam | None = None
        self._loading_exam_id: object = None
        self._current_key: object = None
        self._video_dir: tempfile.TemporaryDirectory[str] | None = None
        self._password = DEFAULT_PASSWORD
        self._settings = QSettings("FreeStepViewer", "FreeStepViewer")
        self._language_pref = str(self._settings.value("language", AUTO_LANGUAGE))
        self._view_options = {"smooth": False, "show_grid": False, "show_cop": True, "show_regions": True}
        self._lowpass_hz = setting_float(self._settings.value("lowpass_hz"), DEFAULT_LOWPASS_HZ)
        self._eyes_closed_first = str(self._settings.value("eyes_closed_first", "false")).lower() == "true"
        self._detrend = str(self._settings.value("detrend", "false")).lower() == "true"
        self._show_ellipse = str(self._settings.value("show_ellipse", "true")).lower() == "true"
        self._compare = str(self._settings.value("compare_acquisitions", "false")).lower() == "true"
        self._cop_origin = int(setting_float(self._settings.value("cop_origin"), ORIGIN_PLATE))
        self._stabilometry: list[Stabilometry | None] = []
        self._romberg: Romberg | None = None
        # History: analyses by (exam ID, low-pass, detrend, eyes closed first); the shown patient
        self._history_cache: dict[tuple[object, ...], ExamAnalysis] = {}
        self._history_patient: int | None = None
        self._history_analyses: list[ExamAnalysis] = []
        self._history_cancel: threading.Event | None = None
        self._history_parameter = str(self._settings.value("history_parameter", "ellipse_area_mm2"))
        self._window_actions: list[QAction] = []

        self._timer = QTimer(self)
        self._play_clock = QElapsedTimer()
        self._play_origin_s = 0.0
        self._timer.timeout.connect(self._advance_frame)
        self._frame_actions: list[QAction] = []  # playback and seeking shortcuts, see _build_actions
        self._build_ui()
        self._build_actions()
        self._set_exam_controls_enabled(False)
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # -- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the central widgets (replacing existing ones) with texts in the current language."""
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Subject / exam"), tr("Date"), tr("Type")])
        self.tree.setColumnWidth(0, 200)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.itemClicked.connect(self._on_item_activated)

        left = self._build_pressure_view()
        cop_tab = self._build_cop_tab()
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.images = ImagePanel()

        analysis = self._build_stabilometry_tab()

        self.tabs = QTabWidget()
        stabilogram_tab, speed_tab = self._build_signal_tabs()
        self.tabs.addTab(self.info, tr("Info"))
        self.tabs.addTab(cop_tab, tr("COP"))
        self.tabs.addTab(stabilogram_tab, tr("Stabilogram"))
        self.tabs.addTab(speed_tab, tr("Speed"))
        self._build_history_tab()

        self.tabs.addTab(analysis, tr("Stabilometry"))
        self.tabs.addTab(self.history_tab, tr("History"))
        self.tabs.currentChanged.connect(self._update_history)
        self.tabs.addTab(self.images, tr("Images"))
        self.exam_video = VideoPlayer()
        self.tabs.addTab(self.exam_video, tr("Video"))
        self.tabs.currentChanged.connect(self._pause_hidden_video)
        self.analysis_tabs = [cop_tab, stabilogram_tab, speed_tab, analysis]

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.tree)
        # The pressure views, or the images of an exam without pressure data
        self.exam_images = ImagePanel()
        self.main_view = QStackedWidget()
        self.main_view.addWidget(left)
        self.main_view.addWidget(self.exam_images)
        self.splitter.addWidget(self.main_view)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([280, 620, 380])
        self.setCentralWidget(self.splitter)
        self._update_tabs()
        self.statusBar().showMessage(tr("Open a FreeSTEP export (.zip) or exam (.fre) — File › Open or drag & drop"))

    def _build_actions(self) -> None:
        """Create the menus and keyboard shortcuts (replacing existing ones)."""
        self.menuBar().clear()
        for action in self._window_actions:
            self.removeAction(action)
        self._window_actions = []

        file_menu = self.menuBar().addMenu(tr("&File"))
        self._add_action(file_menu, tr("&Open…"), self.open_dialog, QKeySequence.StandardKey.Open)
        file_menu.addSeparator()
        self.export_actions = [
            self._add_action(file_menu, tr("Export COP to &CSV…"), self._export_csv),
            self._add_action(file_menu, tr("Export frames to &NPZ…"), self._export_npz),
            self._add_action(file_menu, tr("Export to &MATLAB…"), self._export_mat),
            self._add_action(file_menu, tr("Export &raw data to MATLAB…"), self._export_mat_raw),
        ]
        self.analysis_actions = [
            self._add_action(file_menu, tr("Export &stabilometric parameters to CSV…"), self._export_stabilometry),
            self._add_action(file_menu, tr("Create PDF &report…"), self._export_report),
        ]
        self.export_actions += [
            *self.analysis_actions,
            self._add_action(file_menu, tr("Save heat map as &PNG…"), self._export_png),
            self._add_action(file_menu, tr("Export all images to &ZIP…"), self._export_png_zip),
        ]
        file_menu.addSeparator()
        self.batch_action = self._add_action(file_menu, tr("&Batch export of all exams…"), self._batch_export)
        self.batch_action.setEnabled(self._export is not None)
        file_menu.addSeparator()
        self._add_action(file_menu, tr("&Quit"), self.close, QKeySequence.StandardKey.Quit)

        view_menu = self.menuBar().addMenu(tr("&View"))
        self._add_toggle(view_menu, tr("Smooth interpolation"), "smooth")
        self._add_toggle(view_menu, tr("Sensor grid"), "show_grid")
        self._add_toggle(view_menu, tr("COP markers"), "show_cop")
        self._add_toggle(view_menu, tr("Load quadrants (maximum / mean)"), "show_regions")
        view_menu.addSeparator()
        language_menu = view_menu.addMenu(tr("&Language"))
        group = QActionGroup(language_menu)
        system = QAction(tr("System ({language})", language=LANGUAGES[system_language()]), group, checkable=True)
        system.setChecked(self._language_pref == AUTO_LANGUAGE)
        system.triggered.connect(lambda _=False: self.change_language(AUTO_LANGUAGE))
        language_menu.addAction(system)
        language_menu.addSeparator()
        for code, name in LANGUAGES.items():
            action = QAction(name, group, checkable=True)
            action.setChecked(self._language_pref == code)
            action.triggered.connect(lambda _=False, c=code: self.change_language(c))
            language_menu.addAction(action)

        play = QAction(tr("Play / pause"), self)
        play.setShortcut(Qt.Key.Key_Space)
        play.triggered.connect(self._toggle_play)
        self._window_actions.append(play)
        self._frame_actions = [play]
        for key, delta in (
            (Qt.Key.Key_Right, 1),
            (Qt.Key.Key_Left, -1),
            (Qt.Key.Key_PageDown, 25),
            (Qt.Key.Key_PageUp, -25),
        ):
            step = QAction(self)
            step.setShortcut(key)
            step.triggered.connect(lambda _=False, d=delta: self.slider.setValue(self.slider.value() + d))
            self._window_actions.append(step)
            self._frame_actions.append(step)
        self.addActions(self._window_actions)

        help_menu = self.menuBar().addMenu(tr("&Help"))
        self._add_action(help_menu, tr("&About"), self._about)

    def _add_action(
        self, menu: QMenu, text: str, slot: Callable[[], object], shortcut: QKeySequence.StandardKey | None = None
    ) -> QAction:
        """Add a menu item.

        Args:
            menu: Menu to add the item to.
            text: Translated item text (``&`` marks the mnemonic).
            slot: Called when the item is triggered.
            shortcut: Optional standard keyboard shortcut.

        Returns:
            The created action.
        """
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda _=False: slot())
        menu.addAction(action)
        return action

    def _add_toggle(self, menu: QMenu, text: str, attribute: str) -> None:
        """Add a checkable menu item bound to a display option of the heat map.

        Args:
            menu: Menu to add the item to.
            text: Translated item text.
            attribute: Key of :attr:`_view_options` and attribute of :class:`HeatmapView`.
        """
        action = QAction(text, self, checkable=True)
        action.setChecked(self._view_options[attribute])

        def toggled(value: bool) -> None:
            self._view_options[attribute] = value
            setattr(self.heatmap, attribute, value)
            self.heatmap.update()

        action.toggled.connect(toggled)
        menu.addAction(action)

    # -- language -------------------------------------------------------------

    def change_language(self, preference: str) -> None:
        """Switch the UI language preference, remember it and rebuild the widgets.

        The open export, the shown exam, the frame position and the playback
        settings are kept.

        Args:
            preference: A language code from :data:`~freestep.gui.i18n.LANGUAGES`,
                or :data:`~freestep.gui.i18n.AUTO_LANGUAGE` to follow the OS.
        """
        if preference == self._language_pref:
            return
        self._language_pref = preference
        self._settings.setValue("language", preference)
        resolved = resolve_language(preference)
        if resolved == language():
            # The displayed language did not change, only the preference; just
            # refresh the menu checkmarks (and keep the export actions' state).
            self._build_actions()
            self._set_exam_controls_enabled(self._current is not None)
            return
        self._stop()
        set_language(resolved)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            install_qt_translator(app)

        state = (
            self.acquisition.currentIndex(),
            self.slider.value(),
            self.mode.currentIndex(),
            self.fps.value(),
            self.trail.value(),
            self.tabs.currentIndex(),
            self.splitter.sizes(),
        )
        self._build_ui()
        self._build_actions()
        acquisition, frame, mode, fps, trail, tab, sizes = state
        self.fps.setValue(fps)
        self.trail.setValue(trail)
        self.tabs.setCurrentIndex(tab)
        self.splitter.setSizes(sizes)
        self._set_exam_controls_enabled(False)

        if self._export is not None:
            self._populate_tree(self._export)
            self._show_export_status()
        elif self._current is not None:
            self._add_fre_item(self._current)
        if self._current is not None:
            self._show_exam(self._current)
            self.acquisition.setCurrentIndex(acquisition)
            self.mode.setCurrentIndex(mode)
            self.slider.setValue(frame)

    # -- misc ------------------------------------------------------------------------

    def _about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(
            self,
            tr("About {app}", app=APP_NAME),
            f"<b>{APP_NAME}</b><br>{html.escape(tr('Viewer for data exported from FreeSTEP pressure plates.'))}"
            f"<br><br><a href='{APP_URL}'>{APP_URL}</a><br>MIT License",
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        """Save the geometry, wait for the worker and close the archives (Qt event handler)."""
        self._stop()
        self._settings.setValue("geometry", self.saveGeometry())
        self._pool.waitForDone(10000)
        if self._current is not None:
            self._current.fre.close()
        self._close_export()
        self.exam_video.set_video(None)
        if self._video_dir is not None:
            self._video_dir.cleanup()
        super().closeEvent(event)


class _FileOpenFilter(QObject):
    """Open files sent by the operating system (macOS Finder: double-click, "Open With")."""

    def __init__(self, window: MainWindow) -> None:
        """Create the filter.

        Args:
            window: Window that opens the files.
        """
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        """Open the file of a ``FileOpen`` event; let other events pass."""
        if isinstance(event, QFileOpenEvent) and event.file():
            self._window.open_path(Path(event.file()))
            return True
        return super().eventFilter(watched, event)


#: Qt warns about every frame of the exam videos (XviD with packed B-frames); harmless.
_VIDEO_WARNING = "QFFmpeg::Demuxer received AVPacket with pts == AV_NOPTS_VALUE"


def _quiet_video_warnings() -> None:
    """Drop the per-frame warning of Qt Multimedia about the exam videos, pass on other messages."""
    previous = None

    def handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
        if message == _VIDEO_WARNING:
            return
        if previous is not None:
            previous(mode, context, message)
        else:
            print(message, file=sys.stderr)

    previous = qInstallMessageHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``freestep-viewer``.

    The language is taken from the saved setting, else from the system locale.

    Args:
        argv: Command line including the program name; ``argv[1]`` may be a file
            to open. ``None`` uses ``sys.argv``.

    Returns:
        Process exit code.
    """
    argv = sys.argv if argv is None else argv
    _quiet_video_warnings()
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("FreeStepViewer")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    settings = QSettings("FreeStepViewer", "FreeStepViewer")
    set_language(resolve_language(str(settings.value("language", AUTO_LANGUAGE))))
    install_qt_translator(app)
    window = MainWindow()
    app.installEventFilter(_FileOpenFilter(window))
    window.show()
    if len(argv) > 1:
        window.open_path(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
