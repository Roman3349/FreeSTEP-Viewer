"""PDF report of a posturography exam: the stabilometric report.

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

from ...analysis.load import load_regions
from ...analysis.protocols import ROMBERG_PARAMETERS, SYMMETRY_PARAMETERS, Assessment, Condition, Romberg, Stance
from ...analysis.stabilometry import Stabilometry, speed, sway, unit
from ...models import Exam, Patient
from ...recording import Recording
from ..exam import region_overlay
from ..i18n import tr
from ..meta import APP_NAME
from ..texts import (
    acquisition_style,
    directions_note,
    format_number,
    parameter_groups,
    romberg_order_note,
)
from ..widgets import AP_COLOR, COP_TOTAL_COLOR, ML_COLOR, ORIGIN_MEAN, CopPlot, HeatmapView, StabilogramPlot
from .pages import Pages, exam_when, notes_html, paint_header, section

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
        cells = [format_number(None if r is None else getattr(r, name)) for r in data.results]
        if romberg is not None:
            cells.append(format_number(romberg.quotients[name]) if name in romberg.quotients else "")
        values = "".join(f"<td align='right'>{c}</td>" for c in cells)
        stripe = f" style='background-color:{_STRIPE}'" if number % 2 else ""
        rows.append(
            f"<tr{stripe}><td>{html.escape(label)}</td><td style='color:#666666'>{html.escape(unit(name))}</td>"
            f"{values}</tr>"
        )
    return f"<table {_TABLE}>{''.join(rows)}</table>"


def _test_tables(data: ReportData) -> list[tuple[str, str]]:
    """``(title, table)`` of the Romberg tests on one foot and of the single-leg symmetry."""
    labels = dict(item for _, group in parameter_groups() for item in group)
    tables = []
    one_foot = [r for r in data.assessment.rombergs if r.stance != Stance.BOTH]
    if one_foot:
        columns = [tr("Left foot") if r.stance == Stance.LEFT else tr("Right foot") for r in one_foot]
        rows = [[format_number(r.quotients[name]) for r in one_foot] for name in ROMBERG_PARAMETERS]
        tables.append(
            (tr("Romberg test on one foot"), _value_table(columns, [(n, labels[n]) for n in ROMBERG_PARAMETERS], rows))
        )
    for legs in data.assessment.legs:
        eyes = {False: tr("eyes open"), True: tr("eyes closed"), None: ""}[legs.eyes_closed]
        left, right = data.results[legs.left], data.results[legs.right]
        rows = [
            [
                format_number(None if left is None else getattr(left, name)),
                format_number(None if right is None else getattr(right, name)),
                f"{format_number(legs.symmetry_pct[name])} %",
            ]
            for name in SYMMETRY_PARAMETERS
        ]
        columns = [tr("Left foot"), tr("Right foot"), tr("Symmetry")]
        title = tr("Single-leg stance") + (f", {eyes}" if eyes else "")
        tables.append((title, _value_table(columns, [(n, labels[n]) for n in SYMMETRY_PARAMETERS], rows)))
    return tables


def _value_table(columns: list[str], parameters: list[tuple[str, str]], rows: list[list[str]]) -> str:
    """Table of parameters (rows) with values in the given columns."""
    width = f"{63 / len(columns):.0f}%"
    head = "".join(f"<th align='right' width='{width}'>{html.escape(c)}</th>" for c in columns)
    parameter = html.escape(tr("Parameter"))
    lines = [
        f"<tr style='background-color:#dddddd'><th align='left' width='30%'>{parameter}</th>"
        f"<th width='7%'></th>{head}</tr>"
    ]
    for number, ((name, label), cells) in enumerate(zip(parameters, rows, strict=True)):
        values = "".join(f"<td align='right'>{c}</td>" for c in cells)
        stripe = f" style='background-color:{_STRIPE}'" if number % 2 else ""
        lines.append(
            f"<tr{stripe}><td>{html.escape(label)}</td><td style='color:#666666'>{html.escape(unit(name))}</td>"
            f"{values}</tr>"
        )
    return f"<table {_TABLE}>{''.join(lines)}</table>"


@dataclass(frozen=True)
class ReportData:
    """Everything the report shows.

    Attributes:
        patient: The patient.
        exam: The exam.
        post: Its recording.
        results: Stabilometric parameters of every acquisition (``None`` where not analysed).
        assessment: Conditions of the acquisitions and the tests they form (Romberg, single-leg stance).
        lowpass_hz: Cut-off of the low-pass filter used for ``results`` (``None``: unfiltered).
        detrend: The linear trend was removed for ``results``.
    """

    patient: Patient
    exam: Exam
    post: Recording
    results: list[Stabilometry | None]
    assessment: Assessment
    lowpass_hz: float | None
    detrend: bool = False

    @property
    def romberg(self) -> Romberg | None:
        """The Romberg test on both feet, ``None`` when there is none."""
        return self.assessment.romberg

    @property
    def names(self) -> list[str]:
        """Column names of the acquisitions (their condition when known, else the number)."""
        return [acquisition_style(acq.index, self._condition(acq.index))[0] for acq in self.post.acquisitions]

    def _condition(self, index: int) -> Condition | None:
        """Condition of an acquisition, ``None`` when not known."""
        conditions = self.assessment.conditions
        return conditions[index] if index < len(conditions) else None

    def stance(self, index: int) -> Stance | None:
        """Stance of an acquisition, ``None`` when not known."""
        condition = self._condition(index)
        return condition.stance if condition is not None else None

    @property
    def when(self) -> str:
        """Date and time of the exam."""
        return exam_when(self.exam)


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
    pages = Pages(path, f"{title} — {name}", f"{name} · {tr('Exam {id}', id=data.exam.id)} · {data.when}")
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


def _paint_header(pages: Pages, data: ReportData) -> None:
    """Subject and exam data side by side."""
    durations = ", ".join(f"{a.duration_s:.1f} s" for a in data.post.acquisitions)
    paint_header(
        pages,
        data.patient,
        data.exam,
        [
            (tr("Acquisitions"), f"{len(data.post.acquisitions)} ({durations})"),
            (tr("Platform"), data.post.header.platform),
        ],
    )


def _paint_summary(pages: Pages, data: ReportData) -> None:
    """Main parameters per acquisition and how to read them."""
    labels = {name: label for _, group in parameter_groups() for name, label in group}
    pages.text(section(tr("Summary")), keep_with=120)
    pages.text(_parameter_table([(n, labels[n]) for n in SUMMARY_PARAMETERS], data))
    notes = []
    if data.romberg is not None:
        notes.append(tr("Romberg quotient = eyes closed / eyes open; above 1 means more sway with eyes closed."))
        if not data.romberg.order_from_labels:
            notes.append(romberg_order_note(data.romberg))
    notes.append(_filter_note(data))
    pages.text(notes_html(notes))


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


def _paint_statokinesigrams(pages: Pages, data: ReportData) -> None:
    """COP trajectories with the confidence ellipses."""
    cop_plots = _statokinesigrams(data)
    if cop_plots:
        names = data.names
        pages.text(section(tr("Statokinesigram")), keep_with=380)
        pages.widgets([(names[i], plot) for i, plot in cop_plots], 340, _columns(data))
        pages.text(
            notes_html([tr("COP of the whole plate around its mean position, same scale for all acquisitions.")])
        )


def _paint_load(pages: Pages, data: ReportData) -> None:
    """Mean pressure images and the load distribution parameters."""
    post, names = data.post, data.names
    pages.new_page()
    pages.text(section(tr("Load distribution")))
    heatmaps = [
        (names[a.index], _mean_heatmap(post, a.index, data.results[a.index], data.stance(a.index)))
        for a in post.acquisitions
    ]
    pages.widgets(heatmaps, 300, _columns(data))
    notes = [
        tr(
            "Mean of every sensor over the frames where it is loaded; share of the total load in every "
            "quadrant, COP of the whole plate (black) and of the left and right foot."
        )
    ]
    if any(data.stance(a.index) in (Stance.LEFT, Stance.RIGHT) for a in post.acquisitions):
        notes.append(tr("Single-leg stance: share of the load of the standing foot on its front and rear half."))
    pages.text(notes_html(notes))
    title, parameters = parameter_groups()[-1]  # load distribution
    pages.text(f"<h3>{html.escape(title)}</h3>{_parameter_table(parameters, data)}")
    pages.text(
        notes_html(
            [
                tr(
                    "Load: left and right foot split in the gap between them; "
                    "front and rear half of each foot's length "
                    "(toes toward the top of the heat map)."
                )
            ]
        )
    )


def _paint_time_course(pages: Pages, data: ReportData) -> None:
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
    stabilogram, speed_curve = _time_plots(signals, data.assessment.conditions)
    pages.new_page()
    pages.text(section(tr("Stabilogram")))
    pages.widgets([("", stabilogram)], 420, 1)
    pages.text(section(tr("Speed")), keep_with=260)
    pages.widgets([("", speed_curve)], 240, 1)
    pages.text(
        notes_html(
            [
                tr("Displacement of the COP of the whole plate from its mean (or trend) and its instantaneous speed."),
                directions_note(),
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


def _mean_heatmap(post: Recording, index: int, result: Stabilometry | None, stance: Stance | None) -> HeatmapView:
    """Heat map of the mean pressure image of an acquisition with the load quadrants, scaled to its maximum.

    Single-leg stance shows only the front and rear half of the loaded foot.
    """
    acq, header = post.acquisitions[index], post.header
    view = HeatmapView()
    view.smooth = True
    view.set_frame(
        acq.mean_image,
        max(float(acq.mean_image.max()), 1.0),
        (float(header.cell_width_mm), float(header.cell_height_mm)),
    )
    if result is not None:
        view.set_regions(*region_overlay(load_regions(acq.pressure.sum(axis=0)), result.as_dict(), stance))
    return view


def _time_plots(
    signals: Sequence[tuple[int, np.ndarray, np.ndarray, np.ndarray]], conditions: list[Condition | None]
) -> tuple[StabilogramPlot, StabilogramPlot]:
    """Stabilogram and speed curve; with several acquisitions all are drawn, coloured by acquisition."""
    stabilogram, speed_curve = StabilogramPlot(), StabilogramPlot()
    speed_curve.symmetric = False
    (index, time, cop, curve), others = signals[0], signals[1:]
    if not others:
        stabilogram.set_data(time, [(tr("ML (x)"), ML_COLOR, cop[:, 0]), (tr("AP (y)"), AP_COLOR, cop[:, 1])], "mm")
        speed_curve.set_data(time[1:], [(tr("COP speed"), COP_TOTAL_COLOR, curve)], "mm/s")
    else:
        label, colour = acquisition_style(index, conditions[index] if index < len(conditions) else None)
        styles = [acquisition_style(i, conditions[i] if i < len(conditions) else None) for i, *_ in others]
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


def _paint_parameters(pages: Pages, data: ReportData) -> None:
    """The stabilometric parameters, one table per group, and notes on the methods."""
    pages.new_page()
    pages.text(section(tr("Stabilometric parameters")))
    for title, parameters in parameter_groups()[:-1]:  # the load distribution is on its own page
        pages.text(f"<h3>{html.escape(title)}</h3>{_parameter_table(parameters, data)}")
    for title, table in _test_tables(data):
        pages.text(f"<h3>{html.escape(title)}</h3>{table}")
    notes = [
        _filter_note(data),
        tr(
            "Path length and mean velocity: length of the COP trajectory and its mean speed. "
            "95% confidence ellipse: area containing 95% of the COP positions (principal components). "
            "Frequencies from the power spectrum (Welch) in 0.15–5 Hz."
        ),
        tr("L/S = path length / area of the 95% confidence ellipse."),
        directions_note(),
    ]
    if data.assessment.legs:
        notes.append(tr("Symmetry = (left − right) / mean of both; positive when the left foot sways more."))
    if any(r is None for r in data.results):
        notes.append(tr("Acquisitions with fewer than 32 loaded frames are not analysed."))
    notes.append(tr("Generated by {app} on {date}.", app=APP_NAME, date=datetime.now().strftime("%Y-%m-%d %H:%M")))
    pages.text(section(tr("Methods")) + notes_html(notes))
