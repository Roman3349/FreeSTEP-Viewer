"""Translated labels, notes and HTML texts of the viewer."""

from __future__ import annotations

import html

import numpy as np
from PySide6.QtGui import QColor

from ..analysis.protocols import (
    ROMBERG_PARAMETERS,
    SINGLE_LEG_SHARE_PCT,
    SYMMETRY_PARAMETERS,
    Assessment,
    Condition,
    Romberg,
    Stance,
)
from ..analysis.stabilometry import Stabilometry, unit
from ..history import ROMBERG_PREFIX
from ..models import Exam, Sex
from ..recording import Acquisition, Recording
from .i18n import tr
from .widgets import AP_COLOR, COP_LEFT_COLOR, COP_RIGHT_COLOR, ML_COLOR


def format_value(value: object) -> str:
    """Format a value for the info HTML: escaped, booleans translated, empty values as a dash."""
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return tr("yes") if value else tr("no")
    return html.escape(str(value))


def plain_value(value: object) -> str:
    """Format a value for a tree cell, ``None`` as an empty string."""
    return "" if value is None else str(value)


def exam_type_label(exam: Exam) -> str:
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


def footwear_label(exam: Exam) -> str | None:
    """Translated footwear during the exam (see :data:`freestep.models.FOOTWEAR`)."""
    if exam.footwear is None:
        return None
    names = {0: tr("Barefoot"), 1: tr("Shoes"), 2: tr("Shoes with orthosis")}
    return names.get(exam.footwear) or tr("Unknown ({code})", code=exam.footwear)


def sex_label(sex: Sex | None) -> str | None:
    """Translated sex of a subject, ``None`` when not known."""
    if sex is None:
        return None
    return {Sex.MALE: tr("Male"), Sex.FEMALE: tr("Female")}[sex]


def condition_label(condition: Condition | None) -> str | None:
    """Translated condition of an acquisition, e.g. "Eyes open", "Left foot, eyes closed"; ``None`` when not known."""
    if condition is None:
        return None
    eyes = {False: tr("eyes open"), True: tr("eyes closed"), None: ""}[condition.eyes_closed]
    if condition.stance == Stance.BOTH:
        return eyes.capitalize() if eyes else tr("Both feet")
    foot = tr("Left foot") if condition.stance == Stance.LEFT else tr("Right foot")
    return f"{foot}, {eyes}" if eyes else foot


def condition_name(key: str) -> str:
    """Translated name of a condition (:func:`~freestep.analysis.protocols.condition_key`) or ``acquisition_<n>``."""
    if key.startswith("acquisition_"):
        return tr("Acquisition {number}", number=key.removeprefix("acquisition_"))
    stance = Stance.LEFT if key.startswith("left_") else Stance.RIGHT if key.startswith("right_") else Stance.BOTH
    eyes = True if key.endswith("eyes_closed") else False if key.endswith("eyes_open") else None
    return condition_label(Condition(stance, eyes)) or key


def acquisition_style(index: int, condition: Condition | None) -> tuple[str, QColor]:
    """Legend label and colour of an acquisition in plots comparing acquisitions (as in the History tab).

    Args:
        index: Zero-based index of the acquisition.
        condition: Condition of the acquisition (see :func:`freestep.analysis.protocols.classify`); names it.
    """
    palette = [ML_COLOR, AP_COLOR, COP_LEFT_COLOR, COP_RIGHT_COLOR]
    label = condition_label(condition)
    if condition is None or label is None:
        return tr("Acquisition {number}", number=index + 1), palette[index % len(palette)]
    if condition.stance == Stance.BOTH:
        return label, AP_COLOR if condition.eyes_closed else ML_COLOR
    return label, COP_LEFT_COLOR if condition.stance == Stance.LEFT else COP_RIGHT_COLOR


def acquisition_label(acq: Acquisition, *, gait: bool = False) -> str:
    """Name of an acquisition for the selection box, e.g. "Acquisition 1 (51.2 s)", "Pass 1 (2.4 s)" for gait."""
    duration = f"{acq.duration_s:.1f}"
    if gait:
        text = tr("Pass {number} ({duration} s)", number=acq.index + 1, duration=duration)
    else:
        text = tr("Acquisition {number} ({duration} s)", number=acq.index + 1, duration=duration)
    return f"{text} — {acq.label}" if acq.label else text


def history_parameters() -> list[tuple[str, str]]:
    """Translated choices of the History tab: ``(parameter, label)``, Romberg quotients last."""
    parameters = [item for _, group in parameter_groups() for item in group]
    labels = dict(parameters)
    quotients = [
        (f"{ROMBERG_PREFIX}{name}", tr("Romberg quotient: {parameter}", parameter=labels.get(name, name)))
        for name in ROMBERG_PARAMETERS
    ]
    return parameters + quotients


def setting_float(value: object, default: float) -> float:
    """Convert a stored setting to a float, ``default`` when missing or invalid."""
    try:
        return float(str(value)) if value is not None else default
    except ValueError:
        return default


def parameter_groups() -> list[tuple[str, list[tuple[str, str]]]]:
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
                ("length_per_area_1_mm", tr("Length per area (L/S)")),
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


def romberg_order_note(romberg: Romberg) -> str:
    """Translated note on the order of the acquisitions of a Romberg test without labels."""
    if romberg.eyes_open < romberg.eyes_closed:
        return tr(
            "FreeSTEP does not store the eye condition; the acquisitions follow the classic Romberg protocol: "
            "eyes open first, then eyes closed."
        )
    return tr("FreeSTEP does not store the eye condition; the first acquisition is taken as eyes closed (as set).")


def directions_note() -> str:
    """Translated explanation of the ML and AP abbreviations."""
    return tr(
        "ML = medio-lateral direction (left–right, x axis of the plate), "
        "AP = antero-posterior direction (forward–backward, y axis of the plate)."
    )


def format_number(value: float | None) -> str:
    """Format a parameter value for the Stabilometry tab, missing values (``None``, NaN) as a dash."""
    return "–" if value is None or not np.isfinite(value) else f"{float(value):.2f}"


def stabilometry_html(post: Recording, results: list[Stabilometry | None], assessment: Assessment) -> str:
    """Build the HTML of the Stabilometry tab: a table with one column per acquisition, then the tests.

    The acquisitions are named by their condition; for a Romberg test on both feet
    the Romberg quotient (eyes closed / eyes open) of the compared parameters is
    added. Romberg tests on one foot and the left / right symmetry of single-leg
    stance follow in their own tables.
    """
    romberg = assessment.romberg
    names = []
    for acq, condition in zip(post.acquisitions, assessment.conditions, strict=True):
        number = tr("Acquisition {number}", number=acq.index + 1)
        label = condition_label(condition)
        names.append(f"{_words(label)}<br><small>{html.escape(number)}</small>" if label else _words(number))
    columns = [*names, _words(tr("Quotient"))] if romberg is not None else names
    rows = [_header_row(tr("Parameter"), columns)]
    for title, parameters in parameter_groups():
        rows.append(f"<tr><td colspan='{len(columns) + 2}'><b><br>{html.escape(title)}</b></td></tr>")
        for name, label in parameters:
            cells = [format_number(None if r is None else getattr(r, name)) for r in results]
            if romberg is not None:
                quotient = romberg.quotients.get(name)
                cells.append(format_number(quotient) if quotient is not None else "")
            rows.append(_parameter_row(name, label, cells))
    tables = [f"<table cellspacing='0'>{''.join(rows)}</table>", *_test_tables(assessment)]
    return "".join(tables) + "".join(f"<p style='color:gray'>{html.escape(n)}</p>" for n in _notes(results, assessment))


def _words(text: str) -> str:
    """One word per line keeps the value columns narrow."""
    return "<br>".join(html.escape(w) for w in text.split())


def _header_row(title: str, columns: list[str]) -> str:
    """Header row of a parameter table."""
    header = "".join(f"<th align='right' style='padding-left:12px; white-space:nowrap'>{c}</th>" for c in columns)
    return f"<tr><th align='left'>{html.escape(title)}</th><th></th>{header}</tr>"


def _parameter_row(name: str, label: str, cells: list[str]) -> str:
    """Row of a parameter table: label, unit and the values."""
    values = "".join(f"<td align='right' style='padding-left:12px; white-space:nowrap'>{c}</td>" for c in cells)
    return (
        f"<tr><td style='padding-right:12px; white-space:nowrap'>{html.escape(label)}</td>"
        f"<td style='color:gray; white-space:nowrap'>{html.escape(unit(name))}</td>{values}</tr>"
    )


def _test_tables(assessment: Assessment) -> list[str]:
    """Tables of the Romberg tests on one foot and of the single-leg symmetry."""
    labels = dict(item for _, group in parameter_groups() for item in group)
    tables = []
    one_foot = [r for r in assessment.rombergs if r.stance != Stance.BOTH]
    if one_foot:
        columns = [_words(tr("Left foot") if r.stance == Stance.LEFT else tr("Right foot")) for r in one_foot]
        rows = [_header_row(tr("Romberg quotient on one foot"), columns)]
        for name in ROMBERG_PARAMETERS:
            rows.append(_parameter_row(name, labels[name], [format_number(r.quotients[name]) for r in one_foot]))
        tables.append(
            f"<h3><br>{html.escape(tr('Romberg test on one foot'))}</h3><table cellspacing='0'>{''.join(rows)}</table>"
        )
    for legs in assessment.legs:
        eyes = {False: tr("eyes open"), True: tr("eyes closed"), None: ""}[legs.eyes_closed]
        title = tr("Single-leg stance") + (f", {eyes}" if eyes else "")
        columns = [_words(tr("Left foot")), _words(tr("Right foot")), _words(tr("Symmetry"))]
        rows = [_header_row(tr("Parameter"), columns)]
        results = assessment.results
        for name in SYMMETRY_PARAMETERS:
            left, right = (
                getattr(results[i], name) if results[i] is not None else None for i in (legs.left, legs.right)
            )
            rows.append(
                _parameter_row(
                    name,
                    labels[name],
                    [format_number(left), format_number(right), f"{format_number(legs.symmetry_pct[name])} %"],
                )
            )
        tables.append(f"<h3><br>{html.escape(title)}</h3><table cellspacing='0'>{''.join(rows)}</table>")
    return tables


def _notes(results: list[Stabilometry | None], assessment: Assessment) -> list[str]:
    """Notes below the Stabilometry tab: directions, filtering, load split and the tests."""
    notes = [directions_note()]
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
        notes.append(tr("L/S = path length / area of the 95% confidence ellipse."))
    if any(c is not None and c.stance != Stance.BOTH and not c.stance_from_label for c in assessment.conditions):
        notes.append(
            tr(
                "Single-leg stance is recognised by at least {share} % of the load on one foot.",
                share=f"{SINGLE_LEG_SHARE_PCT:g}",
            )
        )
    romberg = assessment.romberg
    if assessment.rombergs:
        notes.append(tr("Romberg quotient = eyes closed / eyes open; above 1 means more sway with eyes closed."))
        if romberg is not None and not romberg.order_from_labels:
            notes.append(romberg_order_note(romberg))
    else:
        notes.append(tr("No Romberg test (needs a posturography exam with acquisitions with eyes open and closed)."))
    if assessment.legs:
        notes.append(tr("Symmetry = (left − right) / mean of both; positive when the left foot sways more."))
    return notes
