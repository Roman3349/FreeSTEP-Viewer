"""FreeSTEP Viewer — Qt (PySide6) desktop application.

Layout: patient/exam tree on the left, heat map with playback controls in the
middle, COP plot / exam info / preview tabs on the right. Decrypting and
decoding runs in a single worker thread, so the window stays responsive.

Run with ``freestep-viewer [file]`` or ``python -m freestep.gui [file]``.
"""

from __future__ import annotations

import html
import sys
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QEvent, QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QFileOpenEvent,
    QIcon,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..archive import DEFAULT_PASSWORD, FreeStepExport, FreFile
from ..models import Exam, FilterSettings, Patient
from ..postura import Acquisition, Postura
from ..writers import write_cop_csv, write_npz
from .i18n import (
    AUTO_LANGUAGE,
    LANGUAGES,
    install_qt_translator,
    language,
    ntr,
    resolve_language,
    set_language,
    system_language,
    tr,
)
from .widgets import COP_LEFT_COLOR, COP_RIGHT_COLOR, COP_TOTAL_COLOR, CopPlot, HeatmapView

#: Application name (window title, About dialog, settings).
APP_NAME = "FreeSTEP Viewer"
#: Project homepage (About dialog).
APP_URL = "https://github.com/Roman3349/FreeSTEP-Viewer"
#: Reverse-DNS application ID (desktop file, Flatpak, AppStream).
APP_ID = "cz.romanondracek.FreeStepViewer"
#: Application icon (also used by the packaging scripts).
ICON_PATH = Path(__file__).with_name("icon.svg")

# Tree item data role holding the exam key: ("exam", exam_id) or ("file", path).
_EXAM_ROLE = Qt.ItemDataRole.UserRole

# Indexes of the display mode combo box; also used in PNG file names.
_MODE_FRAME, _MODE_MAX, _MODE_MEAN = 0, 1, 2
_MODE_FILE_SUFFIX = {_MODE_MAX: "max", _MODE_MEAN: "mean"}


# -- background work ---------------------------------------------------------


class _Signals(QObject):
    """Signals of a :class:`_Task` (a ``QRunnable`` cannot have signals itself).

    Signals are delivered in the GUI thread because this object lives there.
    """

    finished = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    """Run a function in a thread pool and report the result via :class:`_Signals`."""

    def __init__(self, func: Callable[[], object]) -> None:
        """Create the task.

        Args:
            func: Function to run in the worker thread; must not touch widgets.
        """
        super().__init__()
        self.func = func
        self.signals = _Signals()

    def run(self) -> None:
        """Call the function and emit ``finished(result)`` or ``failed(exception)``."""
        try:
            result = self.func()
        except BaseException as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


@dataclass
class LoadedExam:
    """An exam with its decoded recording and values precomputed for display.

    Attributes:
        fre: The open exam archive (closed when another exam is shown).
        exam: Exam metadata from the archive.
        patient: Patient from the archive.
        filter: Filter settings of the recording.
        postura: The decoded recording.
        pressure_sums: Per acquisition: sum of all sensor values per frame.
        max_images: Per acquisition: per-sensor maximum over all frames.
        preview: JPEG of ``Preview1.jpg``, if present.
    """

    fre: FreFile
    exam: Exam
    patient: Patient
    filter: FilterSettings
    postura: Postura
    pressure_sums: list[NDArray[np.int64]]
    max_images: list[NDArray[np.uint16]]
    preview: bytes | None


def _load_exam(fre: FreFile) -> LoadedExam:
    """Decode an exam and precompute the display values (runs in the worker thread).

    Args:
        fre: The open exam archive.

    Returns:
        The loaded exam.
    """
    post = fre.postura  # the slow part (ZipCrypto decryption)
    return LoadedExam(
        fre=fre,
        exam=fre.exam,
        patient=fre.patient,
        filter=fre.filter,
        postura=post,
        pressure_sums=[a.pressure.reshape(a.frame_count, -1).sum(axis=1, dtype=np.int64) for a in post.acquisitions],
        max_images=[a.pressure.max(axis=0) for a in post.acquisitions],
        preview=fre.preview_jpeg(),
    )


def _is_bad_password(exc: BaseException) -> bool:
    """Tell whether ``exc`` is the error :mod:`zipfile` raises for a wrong password."""
    return isinstance(exc, RuntimeError) and "password" in str(exc).lower()


def _fmt(value: object) -> str:
    """Format a value for the info HTML: escaped, booleans translated, empty values as a dash."""
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return tr("yes") if value else tr("no")
    return html.escape(str(value))


def _plain(value: object) -> str:
    """Format a value for a tree cell, ``None`` as an empty string."""
    return "" if value is None else str(value)


def _exam_type_label(exam: Exam) -> str:
    """Translated name of the exam type (see :data:`freestep.models.EXAM_TYPES`)."""
    names = {
        0: tr("Static"),
        1: tr("Static with photo"),
        2: tr("Dynamic"),
        3: tr("Dynamic with video"),
        4: tr("Posturography"),
        6: tr("Videography"),
        8: tr("Podoscanner"),
        11: tr("Treadmill with video"),
    }
    name = names.get(exam.exam_type) if exam.exam_type is not None else None
    return name or tr("Unknown ({code})", code=exam.exam_type)


def _footwear_label(exam: Exam) -> str | None:
    """Translated footwear during the exam (see :data:`freestep.models.FOOTWEAR`)."""
    if exam.footwear is None:
        return None
    names = {0: tr("Barefoot"), 1: tr("Shoes"), 2: tr("Shoes with orthosis")}
    return names.get(exam.footwear) or tr("Unknown ({code})", code=exam.footwear)


def _acquisition_label(acq: Acquisition) -> str:
    """Name of an acquisition for the selection box, e.g. "Acquisition 1 (51.2 s)"."""
    text = tr("Acquisition {number} ({duration} s)", number=acq.index + 1, duration=f"{acq.duration_s:.1f}")
    return f"{text} — {acq.label}" if acq.label else text


# -- main window ---------------------------------------------------------------


class MainWindow(QMainWindow):
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
        self._tasks: set[_Task] = set()
        self._export: FreeStepExport | None = None
        self._export_name = ""
        self._current: LoadedExam | None = None
        self._loading_exam_id: object = None
        self._current_key: object = None
        self._password = DEFAULT_PASSWORD
        self._settings = QSettings("FreeStepViewer", "FreeStepViewer")
        self._language_pref = str(self._settings.value("language", AUTO_LANGUAGE))
        self._view_options = {"smooth": False, "show_grid": False, "show_cop": True}
        self._window_actions: list[QAction] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_frame)
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
        self.tree.setHeaderLabels([tr("Patient / exam"), tr("Date"), tr("Type")])
        self.tree.setColumnWidth(0, 200)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.itemClicked.connect(self._on_item_activated)

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

        self.cop_plot = CopPlot()
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.preview = QLabel(tr("No preview"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self.preview)
        preview_scroll.setWidgetResizable(True)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.cop_plot, tr("COP"))
        self.tabs.addTab(self.info, tr("Info"))
        self.tabs.addTab(preview_scroll, tr("Preview"))

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([280, 620, 380])
        self.setCentralWidget(self.splitter)
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
            self._add_action(file_menu, tr("Save heat map as &PNG…"), self._export_png),
        ]
        file_menu.addSeparator()
        self._add_action(file_menu, tr("&Quit"), self.close, QKeySequence.StandardKey.Quit)

        view_menu = self.menuBar().addMenu(tr("&View"))
        self._add_toggle(view_menu, tr("Smooth interpolation"), "smooth")
        self._add_toggle(view_menu, tr("Sensor grid"), "show_grid")
        self._add_toggle(view_menu, tr("COP markers"), "show_cop")
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

    def _set_exam_controls_enabled(self, enabled: bool) -> None:
        """Enable the playback controls and export actions (only useful with a loaded exam)."""
        for widget in (self.acquisition, self.mode, self.play_button, self.slider, self.fps, self.trail):
            widget.setEnabled(enabled)
        for action in self.export_actions:
            action.setEnabled(enabled)

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

    # -- background helper ------------------------------------------------------

    def _run(
        self, func: Callable[[], object], done: Callable[[Any], None], fail: Callable[[BaseException], None]
    ) -> None:
        """Run ``func`` in the worker thread.

        Args:
            func: Work to do; must not touch widgets.
            done: Called in the GUI thread with the result.
            fail: Called in the GUI thread with the raised exception.
        """
        task = _Task(func)
        self._tasks.add(task)  # keep a reference until finished

        def finished(result: object) -> None:
            self._tasks.discard(task)
            done(result)

        def failed(exc: BaseException) -> None:
            self._tasks.discard(task)
            fail(exc)

        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
        self._pool.start(task)

    def _error(self, title: str, exc: BaseException) -> None:
        """Show an error dialog with the traceback in its details.

        Args:
            title: Translated dialog title.
            exc: The error.
        """
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"{title}: {exc}")
        box = QMessageBox(QMessageBox.Icon.Critical, title, str(exc) or exc.__class__.__name__, parent=self)
        box.setDetailedText("".join(traceback.format_exception(exc)))
        box.exec()

    def _ask_password(self) -> bool:
        """Ask for the archive password and remember it.

        Returns:
            ``False`` when the user cancelled the dialog.
        """
        password, ok = QInputDialog.getText(
            self, tr("Password"), tr("The archive is encrypted with a different password:"), QLineEdit.EchoMode.Password
        )
        if ok:
            self._password = password
        return ok

    # -- opening files --------------------------------------------------------------

    def open_dialog(self) -> None:
        """Let the user pick an export or exam file and open it."""
        start = str(self._settings.value("last_dir", str(Path.home())))
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Open FreeSTEP data"),
            start,
            tr("FreeSTEP data (*.zip *.fre);;Export (*.zip);;Exam (*.fre);;All (*)"),
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        """Open an export (``.zip``) or a single exam (``.fre``).

        Args:
            path: File to open; the type is decided by the suffix.
        """
        self._settings.setValue("last_dir", str(path.parent))
        self._stop()
        if path.suffix.lower() == ".fre":
            self._open_fre_file(path)
        else:
            self._open_export(path)

    def _open_export(self, path: Path) -> None:
        """Open an export in the worker thread and fill the tree; asks for the password if needed."""
        self.statusBar().showMessage(tr("Reading {name}…", name=path.name))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        password = self._password

        def work() -> FreeStepExport:
            export = FreeStepExport(path, password)
            try:
                _ = export.index, export.patients, export.exams  # decrypts Indice.ini and Export.mdb
            except BaseException:
                export.close()
                raise
            return export

        def done(export: FreeStepExport) -> None:
            QApplication.restoreOverrideCursor()
            self._close_export()
            self._export = export
            self._export_name = path.name
            self._populate_tree(export)
            self.setWindowTitle(f"{path.name} — {APP_NAME}")
            self._show_export_status()

        def fail(exc: BaseException) -> None:
            QApplication.restoreOverrideCursor()
            if _is_bad_password(exc) and self._ask_password():
                self._open_export(path)
            elif isinstance(exc, zipfile.BadZipFile):
                self._error(tr("Not a FreeSTEP export"), exc)
            else:
                self._error(tr("Cannot open export"), exc)

        self._run(work, done, fail)

    def _show_export_status(self) -> None:
        """Show the summary of the open export in the status bar."""
        assert self._export is not None
        self.statusBar().showMessage(
            tr(
                "{name}: centre {centre}, {patients} patients, {exams} exams",
                name=self._export_name,
                centre=self._export.index.centre_code,
                patients=len(self._export.patients),
                exams=len(self._export.exams),
            )
        )

    def _open_fre_file(self, path: Path) -> None:
        """Open a single exam file; asks for the password if needed."""
        self._close_export()
        self.tree.clear()
        try:
            fre = FreFile(path, self._password)
            _ = fre.exam  # reads the INI files, i.e. checks the password
        except BaseException as exc:
            if _is_bad_password(exc) and self._ask_password():
                self._open_fre_file(path)
            else:
                self._error(tr("Cannot open exam"), exc)
            return
        item = QTreeWidgetItem([fre.patient.name or path.name, _plain(fre.exam.exam_date), _exam_type_label(fre.exam)])
        item.setData(0, _EXAM_ROLE, ("file", str(path)))
        self.tree.addTopLevelItem(item)
        self.setWindowTitle(f"{path.name} — {APP_NAME}")
        self._load(lambda: fre, ("file", str(path)))

    def _add_fre_item(self, loaded: LoadedExam) -> None:
        """Re-add the tree item of a single opened ``.fre`` file (after rebuilding the UI)."""
        item = QTreeWidgetItem(
            [loaded.patient.name or loaded.exam.file_name, _plain(loaded.exam.exam_date), _exam_type_label(loaded.exam)]
        )
        item.setData(0, _EXAM_ROLE, self._current_key)
        self.tree.addTopLevelItem(item)

    def _close_export(self) -> None:
        """Close the open export, if any."""
        if self._export is not None:
            self._export.close()
            self._export = None

    def _populate_tree(self, export: FreeStepExport) -> None:
        """Fill the tree with the patients of ``export`` and their exams.

        Exams whose ``.fre`` file is missing in the export are shown disabled.
        """
        self.tree.clear()
        present = set(export.fre_names)
        for patient in sorted(export.patients, key=lambda p: p.name.casefold()):
            node = QTreeWidgetItem([patient.name, _plain(patient.birth_date), ""])
            node.setToolTip(0, tr("ID {id}", id=patient.id))
            self.tree.addTopLevelItem(node)
            exams = export.exams_of(patient.id) if patient.id is not None else []
            for exam in exams:
                if exam.file_name in present:
                    label = tr("Exam {id}", id=exam.id)
                else:
                    label = tr("Exam {id} (missing)", id=exam.id)
                child = QTreeWidgetItem(
                    [label, f"{_plain(exam.exam_date)} {_plain(exam.exam_time)}", _exam_type_label(exam)]
                )
                child.setData(0, _EXAM_ROLE, ("exam", exam.id))
                child.setDisabled(exam.file_name not in present)
                node.addChild(child)
            node.setExpanded(True)
        self.tree.resizeColumnToContents(1)

    @Slot(QTreeWidgetItem, int)
    def _on_item_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """Load the exam of a clicked tree item (patients and the shown exam are ignored)."""
        key = item.data(0, _EXAM_ROLE)
        if not key or key == self._loading_exam_id or (self._current is not None and key == self._current_key):
            return
        kind, value = key
        if kind == "exam" and self._export is not None:
            export = self._export
            self._load(lambda: export.open_fre(int(value)), key)

    def _load(self, open_fre: Callable[[], FreFile], key: object) -> None:
        """Open (decrypt) the exam and decode it in the worker thread, then show it.

        Args:
            open_fre: Returns the open exam archive; called in the worker thread.
            key: Tree key of the exam; a result for an outdated key is discarded.
        """
        self._stop()
        self._loading_exam_id = key
        self.statusBar().showMessage(tr("Decrypting and decoding Postura.dat…"))
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        def done(loaded: LoadedExam) -> None:
            QApplication.restoreOverrideCursor()
            if self._loading_exam_id != key:
                loaded.fre.close()
                return
            self._loading_exam_id = None
            if self._current is not None:
                self._current.fre.close()
            self._current = loaded
            self._current_key = key
            self._show_exam(loaded)

        def fail(exc: BaseException) -> None:
            self._loading_exam_id = None
            self._error(tr("Cannot decode exam"), exc)

        def work() -> LoadedExam:
            fre = open_fre()
            try:
                return _load_exam(fre)
            except BaseException:
                fre.close()
                raise

        self._run(work, done, fail)

    # -- showing an exam --------------------------------------------------------------

    def _show_exam(self, loaded: LoadedExam) -> None:
        """Show a loaded exam in all views, starting with the first frame of the first acquisition."""
        post = loaded.postura
        self.acquisition.blockSignals(True)
        self.acquisition.clear()
        self.acquisition.addItems([_acquisition_label(a) for a in post.acquisitions])
        self.acquisition.setCurrentIndex(0)
        self.acquisition.blockSignals(False)
        self._set_exam_controls_enabled(True)
        self.acquisition.setEnabled(len(post.acquisitions) > 1)
        self.info.setHtml(self._info_html(loaded))
        if loaded.preview:
            pixmap = QPixmap()
            pixmap.loadFromData(loaded.preview)
            self.preview.setPixmap(pixmap)
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(tr("No preview in this exam"))
        self._on_acquisition_changed()
        self.statusBar().showMessage(
            ntr(
                "Exam {id} — {name}: {n} acquisition, {platform}",
                "Exam {id} — {name}: {n} acquisitions, {platform}",
                len(post.acquisitions),
                id=loaded.exam.id,
                name=loaded.patient.name,
                platform=post.header.platform,
            )
        )

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
        self.cop_plot.set_series(
            [
                (tr("total"), COP_TOTAL_COLOR, acq.cop_total),
                (tr("left"), COP_LEFT_COLOR, acq.cop_left),
                (tr("right"), COP_RIGHT_COLOR, acq.cop_right),
            ]
        )
        self.fps.setValue(max(1, round(acq.sampling_rate_hz)) if acq.sampling_rate_hz else self.fps.value())
        self._refresh_frame()

    def _info_html(self, loaded: LoadedExam) -> str:
        """Build the HTML of the Info tab (patient, exam, recording and acquisition tables)."""
        p, e, h, f = loaded.patient, loaded.exam, loaded.postura.header, loaded.filter

        def table(title: str, rows: list[tuple[str, object]]) -> str:
            body = "".join(
                f"<tr><td style='color:gray;padding-right:12px'>{html.escape(k)}</td><td>{_fmt(v)}</td></tr>"
                for k, v in rows
            )
            return f"<h3>{html.escape(title)}</h3><table>{body}</table>"

        acquisitions = [
            table(
                _acquisition_label(acq),
                [
                    (tr("Frames"), acq.frame_count),
                    (tr("Duration"), f"{acq.duration_s:.2f} s"),
                    (tr("Sampling rate"), f"{acq.sampling_rate_hz:.2f} Hz"),
                    (tr("COP path length"), f"{acq.path_length_mm():.1f} mm"),
                    (
                        tr("Sum per frame"),
                        tr("{min} … {max} (mean {mean})", min=sums.min(), max=sums.max(), mean=f"{sums.mean():.0f}"),
                    ),
                ],
            )
            for acq, sums in zip(loaded.postura.acquisitions, loaded.pressure_sums, strict=True)
        ]
        return "".join(
            [
                table(
                    tr("Patient"),
                    [
                        (tr("Name"), p.name),
                        (tr("ID"), p.id),
                        (tr("Born"), p.birth_date),
                        (tr("Sex"), p.sex),
                        (tr("Height"), f"{p.height_cm:g} cm" if p.height_cm else None),
                        (tr("Weight"), f"{p.weight_kg:g} kg" if p.weight_kg else None),
                        (tr("Shoe size"), f"{p.shoe_size_eu:g} EU" if p.shoe_size_eu else None),
                        (tr("SSID"), p.ssid),
                    ],
                ),
                table(
                    tr("Exam"),
                    [
                        (tr("ID"), e.id),
                        (tr("Date"), e.exam_date),
                        (tr("Time"), e.exam_time),
                        (tr("Type"), _exam_type_label(e)),
                        (tr("Footwear"), _footwear_label(e)),
                        (tr("Height at exam"), f"{e.height_cm:g} cm" if e.height_cm else None),
                        (tr("Weight at exam"), f"{e.weight_kg:g} kg" if e.weight_kg else None),
                        (tr("Filter"), tr("raw data") if f.is_raw else f"{f.filter} ({f.frequency} Hz)"),
                        (tr("Note"), e.note),
                        (tr("File"), e.file_name),
                        (tr("SSID"), e.ssid),
                    ],
                ),
                table(
                    tr("Recording"),
                    [
                        (tr("Platform"), h.platform),
                        (tr("Format version"), h.version),
                        (tr("Sensors"), f"{h.columns} × {h.rows}"),
                        (tr("Cell"), f"{h.cell_width_mm} × {h.cell_height_mm} mm"),
                        (tr("Plate"), f"{h.width_cm:g} × {h.height_cm:g} cm"),
                        (tr("Max value"), h.max_value),
                        (tr("Acquisitions"), len(loaded.postura.acquisitions)),
                        (tr("Unknown settings"), h.settings),
                    ],
                ),
                *acquisitions,
            ]
        )

    @Slot()
    def _refresh_frame(self) -> None:
        """Redraw the heat map for the current frame / display mode and move the COP marker."""
        loaded, acq = self._current, self._current_acquisition()
        if loaded is None or acq is None:
            return
        h = loaded.postura.header
        frame = min(self.slider.value(), acq.frame_count - 1)
        mode = self.mode.currentIndex()
        cell = (float(h.cell_width_mm), float(h.cell_height_mm))
        if mode == _MODE_FRAME:
            start = max(0, frame - self.trail.value())
            self.heatmap.set_frame(
                acq.pressure[frame],
                h.max_value,
                cell,
                cops=[
                    (COP_LEFT_COLOR, acq.cop_left[frame]),
                    (COP_RIGHT_COLOR, acq.cop_right[frame]),
                    (COP_TOTAL_COLOR, acq.cop_total[frame]),
                ],
                trail=acq.cop_total[start : frame + 1] if self.trail.value() else None,
            )
            sums = loaded.pressure_sums[acq.index]
            self.frame_label.setText(f"{frame + 1} / {acq.frame_count}  {acq.time_s[frame]:.2f} s  Σ {sums[frame]}")
        else:
            image = loaded.max_images[acq.index] if mode == _MODE_MAX else acq.mean_image
            self.heatmap.set_frame(image, h.max_value, cell, trail=acq.cop_total if self.trail.value() else None)
            self.frame_label.setText(tr("all frames"))
        self.cop_plot.set_current(frame)

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

    # -- playback -----------------------------------------------------------------

    def _toggle_play(self) -> None:
        """Start or pause playback; starting switches to the frame mode and rewinds at the end."""
        if self._current is None:
            return
        if self._timer.isActive():
            self._stop()
        else:
            if self.slider.value() >= self.slider.maximum():
                self.slider.setValue(0)
            self.mode.setCurrentIndex(_MODE_FRAME)
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

    # -- exporting -------------------------------------------------------------------

    def _save_name(self, title: str, suffix: str, filter_: str) -> Path | None:
        """Ask for an output file, proposing ``<exam file stem><suffix>``.

        Args:
            title: Translated dialog title.
            suffix: Appended to the proposed name, including the extension.
            filter_: File type filter of the dialog.

        Returns:
            The chosen path, or ``None`` when cancelled.
        """
        assert self._current is not None
        start = Path(str(self._settings.value("last_dir", str(Path.home()))))
        name = f"{Path(self._current.exam.file_name or 'exam').stem}{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, title, str(start / name), filter_)
        return Path(path) if path else None

    def _export_csv(self) -> None:
        """Save the per-frame time, pressure sum and COP of all acquisitions as CSV."""
        if self._current is None or (path := self._save_name(tr("Export COP"), "_cop.csv", tr("CSV (*.csv)"))) is None:
            return
        write_cop_csv(self._current.postura, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_npz(self) -> None:
        """Save the frames and COP of all acquisitions as a compressed NumPy archive."""
        if self._current is None or (path := self._save_name(tr("Export frames"), ".npz", tr("NumPy (*.npz)"))) is None:
            return
        write_npz(self._current.postura, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_png(self) -> None:
        """Save the heat map as shown (frame, maximum or mean) as PNG."""
        if self._current is None:
            return
        mode = self.mode.currentIndex()
        acq = f"_a{self.acquisition.currentIndex() + 1}"
        suffix = (
            f"{acq}_frame{self.slider.value() + 1}.png"
            if mode == _MODE_FRAME
            else f"{acq}_{_MODE_FILE_SUFFIX[mode]}.png"
        )
        if (path := self._save_name(tr("Save image"), suffix, tr("PNG (*.png)"))) is None:
            return
        image = self.heatmap.image()
        if image is not None and image.save(str(path)):
            self.statusBar().showMessage(tr("Saved {path}", path=path))
        else:
            self.statusBar().showMessage(tr("Could not save {path}", path=path))

    # -- misc ------------------------------------------------------------------------

    def _about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(
            self,
            tr("About {app}", app=APP_NAME),
            f"<b>{APP_NAME}</b><br>{html.escape(tr('Viewer for data exported from FreeSTEP pressure plates.'))}"
            f"<br><br><a href='{APP_URL}'>{APP_URL}</a><br>MIT License",
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt API)
        """Accept dragged ``.zip`` and ``.fre`` files (Qt event handler)."""
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in (".zip", ".fre"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt API)
        """Open the first dropped file (Qt event handler)."""
        urls = event.mimeData().urls()
        if urls:
            self.open_path(Path(urls[0].toLocalFile()))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        """Save the geometry, wait for the worker and close the archives (Qt event handler)."""
        self._stop()
        self._settings.setValue("geometry", self.saveGeometry())
        self._pool.waitForDone(10000)
        if self._current is not None:
            self._current.fre.close()
        self._close_export()
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
