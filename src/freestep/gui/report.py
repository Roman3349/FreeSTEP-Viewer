"""PDF report of a stabilometric exam.

The report is painted with :class:`~PySide6.QtGui.QPdfWriter`: texts and tables
as rich text (:class:`~PySide6.QtGui.QTextDocument`), plots by rendering the
widgets of the viewer, so they look as on screen but are vector graphics. The
pages are A4 portrait; content flows from page to page, a block that does not
fit on the rest of a page starts a new one.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QMarginsF, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPalette, QPdfWriter, QTextDocument
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ..models import Exam, Patient
from ..postura import Postura
from ..stabilometry import Romberg, Stabilometry, load_regions, speed, sway, unit
from .app import (
    APP_NAME,
    ICON_PATH,
    _directions_note,
    _exam_type_label,
    _footwear_label,
    _number,
    _parameter_groups,
    acquisition_style,
)
from .i18n import tr
from .widgets import AP_COLOR, COP_TOTAL_COLOR, ML_COLOR, ORIGIN_MEAN, CopPlot, HeatmapView, StabilogramPlot

#: Resolution of the PDF in dots per inch; 1 dot = 1 px of the widgets.
_DPI = 96
#: Page margin in dots.
_MARGIN = 48
#: Height reserved for the footer in dots.
_FOOTER = 24
#: Gap between blocks and between plots side by side in dots.
_GAP = 12
#: Font size of the text in points.
_FONT_PT = 9

#: Parameters of the summary on the first page.
SUMMARY_PARAMETERS = (
    "path_length_mm",
    "mean_velocity_mm_s",
    "ellipse_area_mm2",
    "sd_ml_mm",
    "sd_ap_mm",
    "range_ml_mm",
    "range_ap_mm",
    "left_load_pct",
    "right_load_pct",
    "forefoot_pct",
    "rearfoot_pct",
)

_TABLE = "width='100%' cellspacing='0' cellpadding='2' style='border-collapse: collapse'"
_STRIPE = "#f0f0f0"


def _print_palette() -> QPalette:
    """Black on white, independent of the (possibly dark) theme of the screen."""
    palette = QPalette(QColor("white"))
    for role, colour in (
        (QPalette.ColorRole.Window, "white"),
        (QPalette.ColorRole.Base, "white"),
        (QPalette.ColorRole.WindowText, "black"),
        (QPalette.ColorRole.Text, "black"),
    ):
        palette.setColor(role, QColor(colour))
    return palette


class _Pages:
    """Paints blocks one below another and starts a new page when a block does not fit."""

    def __init__(self, path: Path, title: str, footer: str) -> None:
        self.writer = QPdfWriter(str(path))
        self.writer.setResolution(_DPI)
        self.writer.setPageLayout(
            QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF())
        )
        self.writer.setTitle(title)
        self.writer.setCreator(APP_NAME)
        self.painter = QPainter(self.writer)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.font = QFont(self.painter.font())
        self.font.setPointSizeF(_FONT_PT)
        self.width = self.writer.width() - 2 * _MARGIN
        self.bottom = self.writer.height() - _MARGIN - _FOOTER
        self.footer = footer
        self.page = 1
        self.y = float(_MARGIN)

    def ensure(self, height: float) -> None:
        """Start a new page unless ``height`` fits below the current position (an empty page always fits)."""
        if self.y + height > self.bottom and self.y > _MARGIN:
            self.new_page()

    def new_page(self) -> None:
        """Finish the current page and continue at the top of a new one."""
        self._paint_footer()
        self.writer.newPage()
        self.page += 1
        self.y = float(_MARGIN)

    def document(self, text: str, width: float | None = None) -> QTextDocument:
        """Lay out rich text for the PDF."""
        doc = QTextDocument()
        doc.documentLayout().setPaintDevice(self.writer)
        doc.setDefaultFont(self.font)
        doc.setDocumentMargin(0)
        doc.setTextWidth(width or self.width)
        doc.setHtml(text)
        return doc

    def text(self, text: str, *, keep_with: float = 0) -> None:
        """Paint rich text across the page.

        Args:
            text: HTML (the subset of :class:`QTextDocument`).
            keep_with: Height of the following block that must fit on the same page (for headings).
        """
        doc = self.document(text)
        height = doc.size().height()
        self.ensure(height + keep_with)
        self.painter.save()
        self.painter.translate(_MARGIN, self.y)
        doc.drawContents(self.painter)
        self.painter.restore()
        self.y += height + _GAP / 2

    def widgets(self, items: Sequence[tuple[str, QWidget]], height: int, columns: int) -> None:
        """Paint widgets in rows of ``columns``, each with a caption above it.

        Args:
            items: ``(caption, widget)``; the widgets are resized to their cell.
            height: Height of a widget in dots.
            columns: Widgets per row.
        """
        cell = (self.width - _GAP * (columns - 1)) / columns
        for start in range(0, len(items), columns):
            row = items[start : start + columns]
            captions = [self.document(f"<b>{html.escape(c)}</b>", cell) for c, _ in row]
            caption_height = max(d.size().height() for d in captions)
            self.ensure(caption_height + height)
            for column, ((_, widget), caption) in enumerate(zip(row, captions, strict=True)):
                x = _MARGIN + column * (cell + _GAP)
                self.painter.save()
                self.painter.translate(x, self.y)
                caption.drawContents(self.painter)
                self.painter.restore()
                widget.setPalette(_print_palette())
                widget.setFont(self.font)
                widget.resize(int(cell), height)
                widget.render(self.painter, QPoint(int(x), int(self.y + caption_height)))
            self.y += caption_height + height + _GAP

    def logo(self, size: float) -> None:
        """Paint the application logo with its name below in the top right corner of the page."""
        right = _MARGIN + self.width
        QSvgRenderer(str(ICON_PATH)).render(self.painter, QRectF(right - size, _MARGIN, size, size))
        self.painter.save()
        self.painter.setFont(self.font)
        self.painter.setPen(QColor(100, 100, 100))
        # One word per line, centred under the logo
        name = QRectF(right - size * 1.5, _MARGIN + size + 2, size * 2, 2 * _FOOTER)
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        self.painter.drawText(name, align, APP_NAME.replace(" ", "\n"))
        self.painter.restore()

    def finish(self) -> None:
        """Paint the footer of the last page and close the file."""
        self._paint_footer()
        self.painter.end()

    def _paint_footer(self) -> None:
        """Paint the footer line: exam on the left, page number on the right."""
        top = self.writer.height() - _MARGIN - _FOOTER / 2
        rect = QRectF(_MARGIN, top, self.width, _FOOTER)
        self.painter.save()
        self.painter.setFont(self.font)
        self.painter.setPen(QColor(128, 128, 128))
        self.painter.drawLine(rect.topLeft(), rect.topRight())
        text_rect = rect.adjusted(0, 4, 0, 0)
        self.painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, self.footer)
        self.painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight, tr("Page {number}", number=self.page))
        self.painter.restore()


def _age(patient: Patient, exam: Exam) -> int | None:
    """Age at the exam in years: as stored with the exam, else from the birth date."""
    if exam.age is not None:
        return exam.age
    born, day = patient.birth_date, exam.exam_date
    if born is None or day is None:
        return None
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _cell(value: object) -> str:
    """Escape a value for a table cell, empty values as a dash."""
    return "–" if value is None or value == "" else html.escape(str(value))


def _info_table(rows: list[tuple[str, object]]) -> str:
    """Two-column table of labels (grey) and values."""
    body = "".join(
        f"<tr><td style='color:#666666; padding-right:10px'>{html.escape(k)}</td><td>{_cell(v)}</td></tr>"
        for k, v in rows
    )
    return f"<table cellspacing='0' cellpadding='1'>{body}</table>"


def _parameter_table(parameters: list[tuple[str, str]], data: ReportData) -> str:
    """Table of parameters (rows) per acquisition (columns), with the Romberg quotient when evaluated."""
    romberg = data.romberg
    # The quotient column is kept (unnamed) in tables without quotients, so all tables are aligned
    quotients = romberg is not None and any(name in romberg.quotients for name, _ in parameters)
    columns = [*data.names, *([tr("Romberg quotient") if quotients else ""] if romberg is not None else [])]
    width = f"{63 / len(columns):.0f}%"
    head = "".join(f"<th align='right' width='{width}'>{html.escape(c)}</th>" for c in columns)
    parameter = html.escape(tr("Parameter"))
    rows = [
        f"<tr style='background-color:#dddddd'><th align='left' width='30%'>{parameter}</th>"
        f"<th width='7%'></th>{head}</tr>"
    ]
    for number, (name, label) in enumerate(parameters):
        cells = [_number(None if r is None else getattr(r, name)) for r in data.results]
        if romberg is not None:
            cells.append(_number(romberg.quotients[name]) if name in romberg.quotients else "")
        values = "".join(f"<td align='right'>{c}</td>" for c in cells)
        stripe = f" style='background-color:{_STRIPE}'" if number % 2 else ""
        rows.append(
            f"<tr{stripe}><td>{html.escape(label)}</td><td style='color:#666666'>{html.escape(unit(name))}</td>"
            f"{values}</tr>"
        )
    return f"<table {_TABLE}>{''.join(rows)}</table>"


def _section(title: str) -> str:
    """Heading of a report section."""
    return f"<h2 style='margin-top:6px'>{html.escape(title)}</h2>"


def _notes(items: list[str]) -> str:
    """Grey small paragraphs."""
    return "".join(f"<p style='color:#555555; font-size:small'>{html.escape(n)}</p>" for n in items)


@dataclass(frozen=True)
class ReportData:
    """Everything the report shows.

    Attributes:
        patient: The patient.
        exam: The exam.
        post: Its recording.
        results: Stabilometric parameters of every acquisition (``None`` where not analysed).
        romberg: Romberg evaluation, ``None`` when not a Romberg test.
        lowpass_hz: Cut-off of the low-pass filter used for ``results`` (``None``: unfiltered).
        detrend: The linear trend was removed for ``results``.
    """

    patient: Patient
    exam: Exam
    post: Postura
    results: list[Stabilometry | None]
    romberg: Romberg | None
    lowpass_hz: float | None
    detrend: bool = False

    @property
    def names(self) -> list[str]:
        """Column names of the acquisitions (eye condition of a Romberg test, else the number)."""
        return [acquisition_style(acq.index, self.romberg)[0] for acq in self.post.acquisitions]

    @property
    def when(self) -> str:
        """Date and time of the exam."""
        time = self.exam.exam_time.strftime("%H:%M") if self.exam.exam_time else None
        return " ".join(str(v) for v in (self.exam.exam_date, time) if v)


def write_report(path: Path, data: ReportData) -> None:
    """Write the PDF report of an exam.

    Contents: patient and exam data, a summary of the main parameters with the
    Romberg quotients, the statokinesigrams with the confidence ellipses (same
    scale for all acquisitions), the mean pressure images, the stabilogram and
    the speed curve over time, all parameters and notes on the methods.

    Args:
        path: Output file.
        data: The exam and its analysis.
    """
    title = tr("Stabilometric report")
    name = data.patient.name
    pages = _Pages(path, f"{title} — {name}", f"{name} · {tr('Exam {id}', id=data.exam.id)} · {data.when}")
    try:
        pages.logo(52)
        pages.text(f"<h1>{html.escape(title)}</h1>")
        _paint_header(pages, data)
        _paint_summary(pages, data)
        _paint_statokinesigrams(pages, data)
        _paint_time_course(pages, data)
        _paint_load(pages, data)
        _paint_parameters(pages, data)
    finally:
        pages.finish()


def _paint_header(pages: _Pages, data: ReportData) -> None:
    """Patient and exam data side by side."""
    patient, exam, post = data.patient, data.exam, data.post
    height = exam.height_cm or patient.height_cm
    weight = exam.weight_kg or patient.weight_kg
    left = _info_table(
        [
            (tr("Name"), patient.name),
            (tr("Born"), patient.birth_date),
            (tr("Age at exam"), _age(patient, exam)),
            (tr("Sex"), patient.sex),
            (tr("Height"), f"{height:g} cm" if height else None),
            (tr("Weight"), f"{weight:g} kg" if weight else None),
        ]
    )
    durations = ", ".join(f"{a.duration_s:.1f} s" for a in post.acquisitions)
    right = _info_table(
        [
            (tr("Exam"), exam.id),
            (tr("Date"), data.when),
            (tr("Type"), _exam_type_label(exam)),
            (tr("Footwear"), _footwear_label(exam)),
            (tr("Acquisitions"), f"{len(post.acquisitions)} ({durations})"),
            (tr("Platform"), post.header.platform),
        ]
    )
    pages.text(
        f"<table width='100%' cellspacing='0'><tr><td width='50%' valign='top'><b>{html.escape(tr('Patient'))}</b>"
        f"{left}</td><td width='50%' valign='top'><b>{html.escape(tr('Exam'))}</b>{right}</td></tr></table>"
    )
    if exam.note:
        pages.text(f"<p><b>{html.escape(tr('Note'))}:</b> {html.escape(exam.note)}</p>")


def _paint_summary(pages: _Pages, data: ReportData) -> None:
    """Main parameters per acquisition and how to read them."""
    labels = {name: label for _, group in _parameter_groups() for name, label in group}
    pages.text(_section(tr("Summary")), keep_with=120)
    pages.text(_parameter_table([(n, labels[n]) for n in SUMMARY_PARAMETERS], data))
    notes = []
    if data.romberg is not None:
        notes.append(tr("Romberg quotient = eyes closed / eyes open; above 1 means more sway with eyes closed."))
        if not data.romberg.order_from_labels:
            notes.append(tr("The eye condition is not stored in the exam; the order of the acquisitions is assumed."))
    notes.append(_filter_note(data))
    pages.text(_notes(notes))


def _filter_note(data: ReportData) -> str:
    """Translated note on the filtering of the COP the parameters are computed from."""
    text = (
        tr("Low-pass filter {cutoff} Hz.", cutoff=f"{data.lowpass_hz:g}")
        if data.lowpass_hz
        else tr("COP not filtered.")
    )
    return f"{text} {tr('Linear trend removed.')}" if data.detrend else text


def _columns(data: ReportData) -> int:
    """Plots side by side: two when comparing acquisitions."""
    return 2 if len(data.post.acquisitions) > 1 else 1


def _paint_statokinesigrams(pages: _Pages, data: ReportData) -> None:
    """COP trajectories with the confidence ellipses."""
    cop_plots = _statokinesigrams(data)
    if cop_plots:
        names = data.names
        pages.text(_section(tr("Statokinesigram")), keep_with=380)
        pages.widgets([(names[i], plot) for i, plot in cop_plots], 340, _columns(data))
        pages.text(_notes([tr("COP of the whole plate around its mean position, same scale for all acquisitions.")]))


def _paint_load(pages: _Pages, data: ReportData) -> None:
    """Mean pressure images and the load distribution parameters."""
    post, names = data.post, data.names
    pages.new_page()
    pages.text(_section(tr("Load distribution")))
    heatmaps = [(names[a.index], _mean_heatmap(post, a.index, data.results[a.index])) for a in post.acquisitions]
    pages.widgets(heatmaps, 300, _columns(data))
    pages.text(
        _notes(
            [
                tr(
                    "Mean of every sensor over the frames where it is loaded; share of the total load in every "
                    "quadrant, COP of the whole plate (black) and of the left and right foot."
                )
            ]
        )
    )
    title, parameters = _parameter_groups()[-1]  # load distribution
    pages.text(f"<h3>{html.escape(title)}</h3>{_parameter_table(parameters, data)}")
    pages.text(
        _notes(
            [
                tr(
                    "Load: left and right foot split in the gap between them; "
                    "front and rear half of each foot's length "
                    "(toes toward the top of the heat map)."
                )
            ]
        )
    )


def _paint_time_course(pages: _Pages, data: ReportData) -> None:
    """Stabilogram and speed curve of all acquisitions."""
    post = data.post
    signals = []
    for acq in post.acquisitions:
        try:
            cop, first = sway(acq, data.lowpass_hz, detrend=data.detrend)
        except ValueError:
            continue
        time = (first + np.arange(len(cop))) / acq.sampling_rate_hz
        signals.append((acq.index, time, cop, speed(cop, acq.sampling_rate_hz)))
    if not signals:
        return
    stabilogram, speed_curve = _time_plots(signals, data.romberg)
    pages.new_page()
    pages.text(_section(tr("Stabilogram")))
    pages.widgets([("", stabilogram)], 420, 1)
    pages.text(_section(tr("Speed")), keep_with=260)
    pages.widgets([("", speed_curve)], 240, 1)
    pages.text(
        _notes(
            [
                tr("Displacement of the COP of the whole plate from its mean (or trend) and its instantaneous speed."),
                _directions_note(),
            ]
        )
    )


def _statokinesigrams(data: ReportData) -> list[tuple[int, CopPlot]]:
    """COP plots with the confidence ellipse of every analysed acquisition, all with the same scale."""
    plots = []
    span = 0.5
    for acq, result in zip(data.post.acquisitions, data.results, strict=True):
        if result is None:
            continue
        cop = np.asarray(acq.cop_total, dtype=np.float64)
        valid = cop[np.all(np.isfinite(cop), axis=1) & np.any(cop != 0, axis=1)]
        if not len(valid):
            continue
        span = max(span, 2 * float(np.abs(valid - valid.mean(axis=0)).max()), 2 * result.ellipse_major_mm / 10)
        plot = CopPlot()
        plot.origin = ORIGIN_MEAN
        plot.set_series([(tr("total"), COP_TOTAL_COLOR, acq.cop_total)])
        plot.set_ellipse(
            (result.mean_x_mm / 10, result.mean_y_mm / 10),
            result.ellipse_major_mm / 10,
            result.ellipse_minor_mm / 10,
            result.ellipse_angle_deg,
        )
        plot.set_current(-1)  # no current frame in the report
        plots.append((acq.index, plot))
    for _, plot in plots:
        plot.span_cm = span * 1.1
    return plots


def _mean_heatmap(post: Postura, index: int, result: Stabilometry | None) -> HeatmapView:
    """Heat map of the mean pressure image of an acquisition with the load quadrants, scaled to its maximum."""
    acq, header = post.acquisitions[index], post.header
    view = HeatmapView()
    view.smooth = True
    view.set_frame(
        acq.mean_image,
        max(float(acq.mean_image.max()), 1.0),
        (float(header.cell_width_mm), float(header.cell_height_mm)),
    )
    if result is not None:
        shares = (result.left_front_pct, result.left_rear_pct, result.right_front_pct, result.right_rear_pct)
        view.set_regions(load_regions(acq.pressure.sum(axis=0)), shares)
    return view


def _time_plots(
    signals: Sequence[tuple[int, np.ndarray, np.ndarray, np.ndarray]], romberg: Romberg | None
) -> tuple[StabilogramPlot, StabilogramPlot]:
    """Stabilogram and speed curve; with several acquisitions all are drawn, coloured by acquisition."""
    stabilogram, speed_curve = StabilogramPlot(), StabilogramPlot()
    speed_curve.symmetric = False
    (index, time, cop, curve), others = signals[0], signals[1:]
    if not others:
        stabilogram.set_data(time, [(tr("ML (x)"), ML_COLOR, cop[:, 0]), (tr("AP (y)"), AP_COLOR, cop[:, 1])], "mm")
        speed_curve.set_data(time[1:], [(tr("COP speed"), COP_TOTAL_COLOR, curve)], "mm/s")
    else:
        label, colour = acquisition_style(index, romberg)
        styles = [acquisition_style(i, romberg) for i, *_ in others]
        stabilogram.set_data(
            time,
            [(tr("ML (x)"), colour, cop[:, 0]), (tr("AP (y)"), colour, cop[:, 1])],
            "mm",
            label=label,
            others=[(n, c, t, [o[:, 0], o[:, 1]]) for (n, c), (_, t, o, _) in zip(styles, others, strict=True)],
        )
        speed_curve.set_data(
            time[1:],
            [(tr("COP speed"), colour, curve)],
            "mm/s",
            label=label,
            others=[(n, c, t[1:], [v]) for (n, c), (_, t, _, v) in zip(styles, others, strict=True)],
        )
    for plot in (stabilogram, speed_curve):
        plot.set_current(-1)  # no current frame in the report
    return stabilogram, speed_curve


def _paint_parameters(pages: _Pages, data: ReportData) -> None:
    """The stabilometric parameters, one table per group, and notes on the methods."""
    pages.new_page()
    pages.text(_section(tr("Stabilometric parameters")))
    for title, parameters in _parameter_groups()[:-1]:  # the load distribution is on its own page
        pages.text(f"<h3>{html.escape(title)}</h3>{_parameter_table(parameters, data)}")
    notes = [
        _filter_note(data),
        tr(
            "Path length and mean velocity: length of the COP trajectory and its mean speed. "
            "95% confidence ellipse: area containing 95% of the COP positions (principal components). "
            "Frequencies from the power spectrum (Welch) in 0.15–5 Hz."
        ),
        _directions_note(),
    ]
    if any(r is None for r in data.results):
        notes.append(tr("Acquisitions with fewer than 32 loaded frames are not analysed."))
    notes.append(tr("Generated by {app} on {date}.", app=APP_NAME, date=datetime.now().strftime("%Y-%m-%d %H:%M")))
    pages.text(_section(tr("Methods")) + _notes(notes))
