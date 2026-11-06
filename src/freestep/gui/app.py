"""FreeSTEP Viewer — Qt (PySide6) desktop application.

Layout: patient/exam tree on the left, heat map with playback controls in the
middle, COP plot / exam info / preview tabs on the right. Decrypting and
decoding runs in a single worker thread, so the window stays responsive.

Run with ``freestep-viewer [file]`` or ``python -m freestep.gui [file]``.
"""

from __future__ import annotations

import html
import sys
import threading
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QObject,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFileOpenEvent,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
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
from ..batch import (
    FORMATS,
    INDEX_NAME,
    SUMMARY_NAME,
    BatchResult,
    batch_export,
    exam_stem,
    safe_name,
    write_summary_csv,
)
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
from ..models import Exam, FilterSettings, Patient
from ..postura import Acquisition, Postura
from ..stabilometry import (
    DEFAULT_LOWPASS_HZ,
    ROMBERG_PARAMETERS,
    LoadDistribution,
    LoadRegions,
    Romberg,
    Stabilometry,
    analyze_all,
    evaluate_romberg,
    load_distribution,
    load_regions,
    speed,
    sway,
    unit,
)
from ..writers import write_cop_csv, write_mat, write_npz, write_stabilometry_csv
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
from .widgets import (
    AP_COLOR,
    COP_LEFT_COLOR,
    COP_RIGHT_COLOR,
    COP_TOTAL_COLOR,
    ML_COLOR,
    ORIGIN_PLATE,
    CopPlot,
    HeatmapView,
    HistoryPlot,
    StabilogramPlot,
)

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


class _Progress(QObject):
    """Progress reports of a background batch export (emitted in the worker thread, delivered in the GUI)."""

    changed = Signal(int, int, object)  # index, count, exam ID


def _format_labels() -> dict[str, str]:
    """Translated descriptions of the batch export formats (keys of :data:`freestep.batch.FORMATS`)."""
    return {
        "mat": tr("MATLAB: data and stabilometric parameters (.mat)"),
        "mat-raw": tr("MATLAB: recorded data only (_raw.mat)"),
        "npz": tr("NumPy: frames and COP (.npz)"),
        "cop-csv": tr("COP per frame (_cop.csv)"),
        "stats-csv": tr("Stabilometric parameters (_stabilometry.csv)"),
    }


class _BatchDialog(QDialog):
    """Options of the batch export: output directory, formats and the summary table."""

    def __init__(self, parent: QWidget, directory: str, formats: list[str], summary: bool) -> None:
        """Create the dialog.

        Args:
            parent: Parent window.
            directory: Proposed output directory.
            formats: Initially checked formats.
            summary: Initial state of the summary option.
        """
        super().__init__(parent)
        self.setWindowTitle(tr("Batch export"))
        layout = QVBoxLayout(self)
        info = QLabel(
            tr(
                "Exports every exam of the open export. Files are named "
                "<birth date>_<name>_<exam date>_<HH-MM>, e.g. 1990-01-31_Jan_Novak_2025-03-10_09-37.mat; "
                "{index} lists the exported exams.",
                index=INDEX_NAME,
            )
        )
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)

        row = QHBoxLayout()
        self.directory = QLineEdit(directory)
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(QLabel(tr("Directory")))
        row.addWidget(self.directory, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.checks: dict[str, QCheckBox] = {}
        for key, label in _format_labels().items():
            check = QCheckBox(label)
            check.setChecked(key in formats)
            check.toggled.connect(self._update_ok)
            self.checks[key] = check
            layout.addWidget(check)
        self.summary = QCheckBox(tr("Summary table of all acquisitions ({name})", name=SUMMARY_NAME))
        self.summary.setChecked(summary)
        self.summary.toggled.connect(self._update_ok)
        layout.addWidget(self.summary)
        note = QLabel(tr("The parameters use the filter, trend and Romberg settings of the Stabilometry tab."))
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.directory.textChanged.connect(self._update_ok)
        self._update_ok()

    def formats(self) -> list[str]:
        """Return the checked formats in the order of :data:`freestep.batch.FORMATS`."""
        return [key for key in FORMATS if self.checks[key].isChecked()]

    def _browse(self) -> None:
        """Let the user pick the output directory."""
        path = QFileDialog.getExistingDirectory(self, tr("Batch export"), self.directory.text())
        if path:
            self.directory.setText(path)

    def _update_ok(self) -> None:
        """Allow OK only with a directory and at least one format or the summary."""
        ok = bool(self.directory.text().strip()) and bool(self.formats() or self.summary.isChecked())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


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
        loads: Per acquisition: load quadrants and the load distribution.
        preview: JPEG of ``Preview1.jpg``, if present.
    """

    fre: FreFile
    exam: Exam
    patient: Patient
    filter: FilterSettings
    postura: Postura
    pressure_sums: list[NDArray[np.int64]]
    max_images: list[NDArray[np.uint16]]
    loads: list[tuple[LoadRegions, LoadDistribution]]
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
        loads=[(load_regions(a.pressure.sum(axis=0)), load_distribution(a.pressure)) for a in post.acquisitions],
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


def acquisition_style(index: int, romberg: Romberg | None) -> tuple[str, QColor]:
    """Legend label and colour of an acquisition in plots comparing acquisitions (as in the History tab).

    Args:
        index: Zero-based index of the acquisition.
        romberg: Romberg evaluation of the exam; names the eye conditions.
    """
    if romberg is not None and index == romberg.eyes_open:
        return tr("Eyes open"), ML_COLOR
    if romberg is not None and index == romberg.eyes_closed:
        return tr("Eyes closed"), AP_COLOR
    palette = [ML_COLOR, AP_COLOR, COP_LEFT_COLOR, COP_RIGHT_COLOR]
    return tr("Acquisition {number}", number=index + 1), palette[index % len(palette)]


def _acquisition_label(acq: Acquisition) -> str:
    """Name of an acquisition for the selection box, e.g. "Acquisition 1 (51.2 s)"."""
    text = tr("Acquisition {number} ({duration} s)", number=acq.index + 1, duration=f"{acq.duration_s:.1f}")
    return f"{text} — {acq.label}" if acq.label else text


def _show_on_heatmap(
    view: HeatmapView, loaded: LoadedExam, acq: Acquisition, frame: int, mode: int, *, trail: int
) -> None:
    """Show a frame, or the maximum / mean image, of an acquisition on a heat map.

    Args:
        view: The heat map.
        loaded: The exam.
        acq: The acquisition.
        frame: Zero-based frame index (used in the frame mode).
        mode: ``_MODE_FRAME``, ``_MODE_MAX`` or ``_MODE_MEAN``.
        trail: Number of previous frames drawn as the COP trail (0: none; the whole
            trajectory in the maximum / mean mode).
    """
    h = loaded.postura.header
    cell = (float(h.cell_width_mm), float(h.cell_height_mm))
    if mode == _MODE_FRAME:
        view.set_frame(
            acq.pressure[frame],
            h.max_value,
            cell,
            cops=[
                (COP_LEFT_COLOR, acq.cop_left[frame]),
                (COP_RIGHT_COLOR, acq.cop_right[frame]),
                (COP_TOTAL_COLOR, acq.cop_total[frame]),
            ],
            trail=acq.cop_total[max(0, frame - trail) : frame + 1] if trail else None,
        )
        view.set_regions(None)
    else:
        image = loaded.max_images[acq.index] if mode == _MODE_MAX else acq.mean_image
        view.set_frame(image, h.max_value, cell, trail=acq.cop_total if trail else None)
        regions, shares = loaded.loads[acq.index]
        view.set_regions(regions, load_shares(shares))


def load_shares(shares: LoadDistribution) -> tuple[float | None, float | None, float | None, float | None]:
    """Shares of the left front, left rear, right front and right rear quadrant for :meth:`HeatmapView.set_regions`."""
    return (
        shares.get("left_front_pct"),
        shares.get("left_rear_pct"),
        shares.get("right_front_pct"),
        shares.get("right_rear_pct"),
    )


def _png_bytes(image: QImage) -> bytes:
    """Encode an image as PNG."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")  # type: ignore[call-overload]  # the stubs say bytes, PySide6 accepts only str
    buffer.close()
    return bytes(data.data())


def _history_parameters() -> list[tuple[str, str]]:
    """Translated choices of the History tab: ``(parameter, label)``, Romberg quotients last."""
    parameters = [item for _, group in _parameter_groups() for item in group]
    labels = dict(parameters)
    quotients = [
        (f"{ROMBERG_PREFIX}{name}", tr("Romberg quotient: {parameter}", parameter=labels.get(name, name)))
        for name in ROMBERG_PARAMETERS
    ]
    return parameters + quotients


def _setting_float(value: object, default: float) -> float:
    """Convert a stored setting to a float, ``default`` when missing or invalid."""
    try:
        return float(str(value)) if value is not None else default
    except ValueError:
        return default


def _parameter_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """Translated groups of the stabilometric parameters shown in the Stabilometry tab.

    Returns:
        ``(group title, [(attribute of Stabilometry, label), …])`` per group.
    """
    return [
        (
            tr("Position and spread"),
            [
                ("mean_x_mm", tr("Mean position ML")),
                ("mean_y_mm", tr("Mean position AP")),
                ("range_ml_mm", tr("Range ML")),
                ("range_ap_mm", tr("Range AP")),
                ("sd_ml_mm", tr("Standard deviation ML")),
                ("sd_ap_mm", tr("Standard deviation AP")),
                ("mean_distance_mm", tr("Mean distance")),
                ("rms_distance_mm", tr("RMS distance")),
            ],
        ),
        (
            tr("Path and velocity"),
            [
                ("path_length_mm", tr("Path length")),
                ("path_length_ml_mm", tr("Path length ML")),
                ("path_length_ap_mm", tr("Path length AP")),
                ("mean_velocity_mm_s", tr("Mean velocity")),
                ("mean_velocity_ml_mm_s", tr("Mean velocity ML")),
                ("mean_velocity_ap_mm_s", tr("Mean velocity AP")),
            ],
        ),
        (
            tr("Area"),
            [
                ("ellipse_area_mm2", tr("95% confidence ellipse")),
                ("ellipse_major_mm", tr("Ellipse semi-major axis")),
                ("ellipse_minor_mm", tr("Ellipse semi-minor axis")),
                ("ellipse_angle_deg", tr("Ellipse angle")),
                ("convex_hull_area_mm2", tr("Convex hull")),
                ("sway_area_mm2_s", tr("Sway area per second")),
            ],
        ),
        (
            tr("Frequency (0.15–5 Hz)"),
            [
                ("mean_frequency_ml_hz", tr("Mean frequency ML")),
                ("mean_frequency_ap_hz", tr("Mean frequency AP")),
                ("median_frequency_ml_hz", tr("Median frequency ML")),
                ("median_frequency_ap_hz", tr("Median frequency AP")),
                ("f95_ml_hz", tr("95% power frequency ML")),
                ("f95_ap_hz", tr("95% power frequency AP")),
                ("total_power_ml_mm2", tr("Total power ML")),
                ("total_power_ap_mm2", tr("Total power AP")),
            ],
        ),
        (
            tr("Load distribution"),
            [
                ("left_load_pct", tr("Left foot")),
                ("right_load_pct", tr("Right foot")),
                ("load_asymmetry_pct", tr("Asymmetry (right − left)")),
                ("left_load_sd_pct", tr("Left share variability (SD)")),
                ("forefoot_pct", tr("Forefoot")),
                ("rearfoot_pct", tr("Rearfoot")),
                ("left_forefoot_pct", tr("Left forefoot (of left foot)")),
                ("right_forefoot_pct", tr("Right forefoot (of right foot)")),
                ("left_front_pct", tr("Left forefoot (of total)")),
                ("left_rear_pct", tr("Left rearfoot (of total)")),
                ("right_front_pct", tr("Right forefoot (of total)")),
                ("right_rear_pct", tr("Right rearfoot (of total)")),
            ],
        ),
    ]


def _directions_note() -> str:
    """Translated explanation of the ML and AP abbreviations."""
    return tr(
        "ML = medio-lateral direction (left–right, x axis of the plate), "
        "AP = antero-posterior direction (forward–backward, y axis of the plate)."
    )


def _number(value: float | None) -> str:
    """Format a parameter value for the Stabilometry tab, missing values (``None``, NaN) as a dash."""
    return "–" if value is None or not np.isfinite(value) else f"{float(value):.2f}"


def _stabilometry_html(post: Postura, results: list[Stabilometry | None], romberg: Romberg | None) -> str:
    """Build the HTML of the Stabilometry tab: a table with one column per acquisition.

    For a Romberg test the eyes-open and eyes-closed acquisitions are named and the
    Romberg quotient (eyes closed / eyes open) of the compared parameters is added.
    """

    def words(text: str) -> str:
        # One word per line keeps the value columns narrow
        return "<br>".join(html.escape(w) for w in text.split())

    names = [words(tr("Acquisition {number}", number=a.index + 1)) for a in post.acquisitions]
    if romberg is not None:
        for index, condition in ((romberg.eyes_open, tr("Eyes open")), (romberg.eyes_closed, tr("Eyes closed"))):
            names[index] = f"{words(condition)}<br><small>{names[index].replace('<br>', ' ')}</small>"
    columns = [*names, words(tr("Quotient"))] if romberg is not None else names
    header = "".join(f"<th align='right' style='padding-left:12px; white-space:nowrap'>{c}</th>" for c in columns)
    rows = [f"<tr><th align='left'>{html.escape(tr('Parameter'))}</th><th></th>{header}</tr>"]
    for title, parameters in _parameter_groups():
        rows.append(f"<tr><td colspan='{len(columns) + 2}'><b><br>{html.escape(title)}</b></td></tr>")
        for name, label in parameters:
            cells = [_number(None if r is None else getattr(r, name)) for r in results]
            if romberg is not None:
                quotient = romberg.quotients.get(name)
                cells.append(_number(quotient) if quotient is not None else "")
            values = "".join(f"<td align='right' style='padding-left:12px; white-space:nowrap'>{c}</td>" for c in cells)
            rows.append(
                f"<tr><td style='padding-right:12px; white-space:nowrap'>{html.escape(label)}</td>"
                f"<td style='color:gray; white-space:nowrap'>{html.escape(unit(name))}</td>{values}</tr>"
            )
    notes = [_directions_note()]
    if any(r is None for r in results):
        notes.append(tr("Acquisitions with fewer than 32 loaded frames are not analysed."))
    analysed = next((r for r in results if r is not None), None)
    if analysed is not None:
        notes.append(
            tr("Low-pass filter {cutoff} Hz.", cutoff=f"{analysed.lowpass_hz:g}")
            if analysed.lowpass_hz
            else tr("COP not filtered.")
        )
        if analysed.detrended:
            notes.append(tr("Linear trend removed."))
        notes.append(
            tr(
                "Load: left and right foot split in the gap between them; "
                "front and rear half of each foot's length "
                "(toes toward the top of the heat map)."
            )
        )
    if romberg is not None:
        notes.append(tr("Romberg quotient = eyes closed / eyes open; above 1 means more sway with eyes closed."))
        if not romberg.order_from_labels:
            notes.append(tr("The eye condition is not stored in the exam; the order of the acquisitions is assumed."))
    else:
        notes.append(tr("Not a Romberg test (needs a posturography exam with two acquisitions of equal duration)."))
    note_html = "".join(f"<p style='color:gray'>{html.escape(n)}</p>" for n in notes)
    return f"<table cellspacing='0'>{''.join(rows)}</table>{note_html}"


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
        self._view_options = {"smooth": False, "show_grid": False, "show_cop": True, "show_regions": True}
        self._lowpass_hz = _setting_float(self._settings.value("lowpass_hz"), DEFAULT_LOWPASS_HZ)
        self._eyes_closed_first = str(self._settings.value("eyes_closed_first", "false")).lower() == "true"
        self._detrend = str(self._settings.value("detrend", "false")).lower() == "true"
        self._show_ellipse = str(self._settings.value("show_ellipse", "true")).lower() == "true"
        self._compare = str(self._settings.value("compare_acquisitions", "false")).lower() == "true"
        self._cop_origin = int(_setting_float(self._settings.value("cop_origin"), ORIGIN_PLATE))
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
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.preview = QLabel(tr("No preview"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self.preview)
        preview_scroll.setWidgetResizable(True)

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
            tr("Romberg test: FreeSTEP does not store the eye condition; the first acquisition is with eyes closed")
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

        self.tabs = QTabWidget()
        self.stabilogram = StabilogramPlot()
        self.stabilogram.seeked.connect(self._seek)
        stabilogram_note = QLabel(
            tr(
                "COP of the whole plate minus its mean (or trend), filtered as set in the Stabilometry tab. "
                "Click to seek."
            )
            + " "
            + _directions_note()
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

        self.tabs.addTab(cop_tab, tr("COP"))
        self.tabs.addTab(stabilogram_tab, tr("Stabilogram"))
        self.tabs.addTab(speed_tab, tr("Speed"))
        self.history_title = QLabel()
        self.history_parameter = QComboBox()
        for name, label in _history_parameters():
            self.history_parameter.addItem(label, name)
        index = self.history_parameter.findData(self._history_parameter)
        self.history_parameter.setCurrentIndex(max(index, 0))
        self.history_parameter.currentIndexChanged.connect(self._on_history_parameter)
        self.history_export = QPushButton(tr("Export CSV…"))
        self.history_export.setToolTip(tr("All parameters of all exams of the patient"))
        self.history_export.clicked.connect(self._export_history)
        self.history_plot = HistoryPlot()
        self.history_plot.selected.connect(self._on_history_selected)
        history_note = QLabel(
            tr("Exams of the patient, oldest first; click an exam to show it. Settings of the Stabilometry tab apply.")
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

        self.tabs.addTab(analysis, tr("Stabilometry"))
        self.tabs.addTab(self.history_tab, tr("History"))
        self.tabs.addTab(self.info, tr("Info"))
        self.tabs.currentChanged.connect(self._update_history)
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
            self._add_action(file_menu, tr("Export to &MATLAB…"), self._export_mat),
            self._add_action(file_menu, tr("Export &raw data to MATLAB…"), self._export_mat_raw),
            self._add_action(file_menu, tr("Export &stabilometric parameters to CSV…"), self._export_stabilometry),
            self._add_action(file_menu, tr("Create PDF &report…"), self._export_report),
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
        enabled = self._current is not None and self.mode.currentIndex() == _MODE_FRAME
        if not enabled:
            self._stop()
        for widget in (self.play_button, self.slider, self.fps):
            widget.setEnabled(enabled)
        for action in self._frame_actions:
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
            self.batch_action.setEnabled(True)
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
        self.batch_action.setEnabled(False)

    def _populate_tree(self, export: FreeStepExport) -> None:
        """Fill the tree with the patients of ``export`` and their exams.

        Exams whose ``.fre`` file is missing in the export are shown disabled.
        """
        self.tree.clear()
        present = set(export.fre_names)
        for patient in sorted(export.patients, key=lambda p: p.name.casefold()):
            node = QTreeWidgetItem([patient.name, _plain(patient.birth_date), ""])
            node.setToolTip(0, tr("ID {id}", id=patient.id))
            if patient.id is not None:
                node.setData(0, _EXAM_ROLE, ("patient", patient.id))
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
        """Load the exam of a clicked tree item; a patient shows their history (the shown exam is ignored)."""
        key = item.data(0, _EXAM_ROLE)
        if not key or key == self._loading_exam_id or (self._current is not None and key == self._current_key):
            return
        kind, value = key
        if kind == "patient":
            self._history_patient = int(value)
            self.tabs.setCurrentWidget(self.history_tab)
            self._update_history()
        elif kind == "exam" and self._export is not None:
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
        self._update_stabilometry()
        if self._export is not None and loaded.exam.patient_id is not None:
            self._history_patient = loaded.exam.patient_id
        self._update_history()
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
        self.stabilometry_view.setHtml(_stabilometry_html(post, self._stabilometry, self._romberg))
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

    @Slot(int)
    def _seek(self, frame: int) -> None:
        """Show a frame (clicked in the stabilogram); pauses playback."""
        self._stop()
        self.mode.setCurrentIndex(_MODE_FRAME)
        self.slider.setValue(frame)

    # -- history ---------------------------------------------------------------------

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
            self.history_plot.set_message(tr("Open a FreeSTEP export (.zip) to see the history of a patient."))
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
        reporter = _Progress()

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
        labels = [f"{_plain(a.exam.exam_date)}\n{tr('Exam {id}', id=a.exam.id)}" for a in analyses]
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
        frame = min(self.slider.value(), acq.frame_count - 1)
        mode = self.mode.currentIndex()
        _show_on_heatmap(self.heatmap, loaded, acq, frame, mode, trail=self.trail.value())
        if mode == _MODE_FRAME:
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

    # -- playback -----------------------------------------------------------------

    def _toggle_play(self) -> None:
        """Start or pause playback (frame mode only); starting at the end rewinds."""
        if self._current is None or self.mode.currentIndex() != _MODE_FRAME:
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

    # -- exporting -------------------------------------------------------------------

    def _save_name(self, title: str, suffix: str, filter_: str) -> Path | None:
        """Ask for an output file, proposing ``<birth date>_<name>_exam_<ID><suffix>`` like the batch export.

        Args:
            title: Translated dialog title.
            suffix: Appended to the proposed name, including the extension.
            filter_: File type filter of the dialog.

        Returns:
            The chosen path, or ``None`` when cancelled.
        """
        assert self._current is not None
        start = Path(str(self._settings.value("last_dir", str(Path.home()))))
        name = f"{exam_stem(self._current.patient, self._current.exam)}{suffix}"
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

    def _export_mat(self) -> None:
        """Save the frames, COP and stabilometric parameters as a MATLAB file."""
        title = tr("Export to MATLAB")
        if self._current is None or (path := self._save_name(title, ".mat", tr("MATLAB (*.mat)"))) is None:
            return
        write_mat(self._current.postura, path, self._stabilometry, self._romberg)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_mat_raw(self) -> None:
        """Save only the recorded data (no filtering, no parameters) as a MATLAB file."""
        title = tr("Export raw data to MATLAB")
        if self._current is None or (path := self._save_name(title, "_raw.mat", tr("MATLAB (*.mat)"))) is None:
            return
        write_mat(self._current.postura, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_stabilometry(self) -> None:
        """Save the stabilometric parameters (and Romberg quotients) as CSV."""
        title = tr("Export stabilometric parameters")
        if self._current is None or (path := self._save_name(title, "_stabilometry.csv", tr("CSV (*.csv)"))) is None:
            return
        write_stabilometry_csv(self._stabilometry, path, self._romberg)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_report(self) -> None:
        """Save the PDF report of the shown exam with the settings of the Stabilometry tab."""
        from .report import ReportData, write_report  # noqa: PLC0415 — the report module imports this one

        title = tr("Create PDF report")
        if self._current is None or (path := self._save_name(title, "_report.pdf", tr("PDF (*.pdf)"))) is None:
            return
        loaded = self._current
        data = ReportData(
            loaded.patient,
            loaded.exam,
            loaded.postura,
            self._stabilometry,
            self._romberg,
            self._lowpass_hz or None,
            self._detrend,
        )
        write_report(path, data)
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

    def _export_png_zip(self) -> None:
        """Save every frame and the maximum and mean image of all acquisitions as PNGs in a ZIP.

        The images look like the heat map (same size and display options). The
        ZIP holds ``a<n>/frame_<k>.png``, ``a<n>_max.png`` and ``a<n>_mean.png``.
        Rendering runs in the GUI thread (widgets cannot paint elsewhere) behind a
        cancellable progress dialog; a cancelled export leaves no file.
        """
        loaded = self._current
        if loaded is None or (path := self._save_name(tr("Export images"), "_images.zip", tr("ZIP (*.zip)"))) is None:
            return
        self._stop()
        view = HeatmapView()
        view.resize(self.heatmap.size())
        for attribute, value in self._view_options.items():
            setattr(view, attribute, value)
        jobs = [
            (acq, frame, _MODE_FRAME, f"a{acq.index + 1}/frame_{frame + 1:04d}.png")
            for acq in loaded.postura.acquisitions
            for frame in range(acq.frame_count)
        ]
        for acq in loaded.postura.acquisitions:
            jobs += [
                (acq, 0, _MODE_MAX, f"a{acq.index + 1}_max.png"),
                (acq, 0, _MODE_MEAN, f"a{acq.index + 1}_mean.png"),
            ]
        progress = QProgressDialog(tr("Rendering images…"), tr("Cancel"), 0, len(jobs), self)
        progress.setWindowTitle(tr("Export images"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        try:
            # PNG is compressed already, so the members are only stored
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
                for done, (acq, frame, mode, name) in enumerate(jobs):
                    if done % 20 == 0:
                        progress.setValue(done)
                        QApplication.processEvents()
                        if progress.wasCanceled():
                            break
                    _show_on_heatmap(view, loaded, acq, frame, mode, trail=self.trail.value())
                    image = view.image()
                    if image is not None:
                        archive.writestr(name, _png_bytes(image))
            cancelled = progress.wasCanceled()
        except OSError as exc:
            progress.close()
            self._error(tr("Could not save {path}", path=path), exc)
            return
        progress.close()
        if cancelled:
            path.unlink(missing_ok=True)
            self.statusBar().showMessage(tr("Export cancelled"))
        else:
            self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _batch_export(self) -> None:
        """Export every exam of the open export into a directory (see :func:`freestep.batch.batch_export`).

        Decrypting runs in the worker thread behind a cancellable progress dialog;
        an export cancelled midway keeps the files written so far.
        """
        export = self._export
        if export is None:
            return
        saved_formats = str(self._settings.value("batch_formats", "mat")).split(",")
        start = str(self._settings.value("batch_dir", self._settings.value("last_dir", str(Path.home()))))
        summary = str(self._settings.value("batch_summary", "true")).lower() == "true"
        dialog = _BatchDialog(self, start, saved_formats, summary)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        directory = Path(dialog.directory.text().strip())
        formats, summary = dialog.formats(), dialog.summary.isChecked()
        self._settings.setValue("batch_dir", str(directory))
        self._settings.setValue("batch_formats", ",".join(formats))
        self._settings.setValue("batch_summary", "true" if summary else "false")
        self._stop()

        progress = QProgressDialog(tr("Preparing…"), tr("Cancel"), 0, max(len(export.exams), 1), self)
        progress.setWindowTitle(tr("Batch export"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        cancel = threading.Event()
        progress.canceled.connect(cancel.set)
        reporter = _Progress()

        def changed(index: int, count: int, exam_id: object) -> None:
            progress.setMaximum(count)
            progress.setValue(index)
            progress.setLabelText(tr("Exam {id} ({n} of {count})", id=exam_id, n=index + 1, count=count))

        reporter.changed.connect(changed)
        lowpass_hz, detrend, eyes_closed_first = self._lowpass_hz or None, self._detrend, self._eyes_closed_first

        def work() -> BatchResult:
            def report(index: int, count: int, exam: Exam) -> bool:
                reporter.changed.emit(index, count, exam.id)
                return not cancel.is_set()

            return batch_export(
                export,
                directory,
                formats,
                summary=summary,
                lowpass_hz=lowpass_hz,
                detrend=detrend,
                eyes_closed_first=eyes_closed_first,
                progress=report,
            )

        def done(result: BatchResult) -> None:
            progress.close()
            reporter.deleteLater()
            text = ntr(
                "{n} file written to {path}.",
                "{n} files written to {path}.",
                len(result.written),
                path=directory,
            )
            if result.cancelled:
                text += "\n" + tr("The export was cancelled.")
            if result.skipped:
                text += "\n\n" + tr("Skipped exams:") + "\n"
                text += "\n".join(f"{exam.id}: {reason}" for exam, reason in result.skipped)
            self.statusBar().showMessage(text.splitlines()[0])
            icon = QMessageBox.Icon.Warning if result.skipped else QMessageBox.Icon.Information
            QMessageBox(icon, tr("Batch export"), text, parent=self).exec()

        def fail(exc: BaseException) -> None:
            progress.close()
            reporter.deleteLater()
            self._error(tr("Batch export failed"), exc)

        self._run(work, done, fail)

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
