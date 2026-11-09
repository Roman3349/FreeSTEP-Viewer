"""Translated labels, notes and HTML texts of the viewer."""

from __future__ import annotations

import html

import numpy as np
from PySide6.QtGui import QColor

from ..history import ROMBERG_PREFIX
from ..models import Exam, Sex
from ..postura import Acquisition, Postura
from ..stabilometry import ROMBERG_PARAMETERS, Romberg, Stabilometry, unit
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


def stabilometry_html(post: Postura, results: list[Stabilometry | None], romberg: Romberg | None) -> str:
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
    for title, parameters in parameter_groups():
        rows.append(f"<tr><td colspan='{len(columns) + 2}'><b><br>{html.escape(title)}</b></td></tr>")
        for name, label in parameters:
            cells = [format_number(None if r is None else getattr(r, name)) for r in results]
            if romberg is not None:
                quotient = romberg.quotients.get(name)
                cells.append(format_number(quotient) if quotient is not None else "")
            values = "".join(f"<td align='right' style='padding-left:12px; white-space:nowrap'>{c}</td>" for c in cells)
            rows.append(
                f"<tr><td style='padding-right:12px; white-space:nowrap'>{html.escape(label)}</td>"
                f"<td style='color:gray; white-space:nowrap'>{html.escape(unit(name))}</td>{values}</tr>"
            )
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
    if romberg is not None:
        notes.append(tr("Romberg quotient = eyes closed / eyes open; above 1 means more sway with eyes closed."))
        if not romberg.order_from_labels:
            notes.append(romberg_order_note(romberg))
    else:
        notes.append(tr("Not a Romberg test (needs a posturography exam with two acquisitions of equal duration)."))
    note_html = "".join(f"<p style='color:gray'>{html.escape(n)}</p>" for n in notes)
    return f"<table cellspacing='0'>{''.join(rows)}</table>{note_html}"
