"""Development of the stabilometric parameters of a patient over their exams."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .analysis.stabilometry import (
    DEFAULT_LOWPASS_HZ,
    ROMBERG_PARAMETERS,
    Romberg,
    Stabilometry,
    analyze_all,
    evaluate_romberg,
)
from .archive import FreeStepExport
from .models import Exam
from .recording import Recording

#: Prefix of the Romberg quotient "parameters" accepted by :func:`series` (e.g. ``romberg_path_length_mm``).
ROMBERG_PREFIX = "romberg_"
#: Condition of the Romberg quotient series returned by :func:`series`.
ROMBERG_CONDITION = "romberg"


@dataclass(frozen=True)
class ExamAnalysis:
    """Stabilometric analysis of one exam.

    Attributes:
        exam: The exam.
        results: Parameters of every acquisition (``None`` where the analysis failed).
        romberg: Romberg evaluation, if the exam is a Romberg test.
        conditions: Per acquisition: ``"eyes_open"`` / ``"eyes_closed"`` for a
            Romberg test, otherwise ``"acquisition_<n>"`` (1-based).
    """

    exam: Exam
    results: list[Stabilometry | None]
    romberg: Romberg | None
    conditions: list[str] = field(default_factory=list)


def analyze_exam(
    post: Recording,
    exam: Exam,
    *,
    lowpass_hz: float | None = DEFAULT_LOWPASS_HZ,
    detrend: bool = False,
    eyes_closed_first: bool = False,
) -> ExamAnalysis:
    """Analyse every acquisition of an exam and evaluate the Romberg test.

    Args:
        post: The recording of the exam.
        exam: The exam (its type decides about the Romberg test).
        lowpass_hz: Low-pass filter (see :func:`freestep.analysis.stabilometry.analyze`).
        detrend: Remove the linear trend (see :func:`freestep.analysis.stabilometry.analyze`).
        eyes_closed_first: Romberg order without labels (see :func:`freestep.analysis.stabilometry.detect_romberg`).
    """
    results = analyze_all(post, lowpass_hz, detrend=detrend)
    romberg = evaluate_romberg(post, results, exam.exam_type, eyes_closed_first)
    conditions = [f"acquisition_{acq.index + 1}" for acq in post.acquisitions]
    if romberg is not None:
        conditions[romberg.eyes_open], conditions[romberg.eyes_closed] = "eyes_open", "eyes_closed"
    return ExamAnalysis(exam=exam, results=results, romberg=romberg, conditions=conditions)


def patient_exams(export: FreeStepExport, patient_id: int) -> list[Exam]:
    """Return the exams of a patient whose ``.fre`` file is in the export, oldest first."""
    present = {name.lower() for name in export.fre_names}
    return [e for e in export.exams_of(patient_id) if e.file_name and e.file_name.lower() in present]


def analyze_exams(
    export: FreeStepExport,
    exams: Sequence[Exam],
    *,
    lowpass_hz: float | None = DEFAULT_LOWPASS_HZ,
    detrend: bool = False,
    eyes_closed_first: bool = False,
    progress: Callable[[int, int, Exam], bool] | None = None,
) -> tuple[list[ExamAnalysis], list[tuple[Exam, str]]]:
    """Decrypt and analyse exams of an export (a few seconds each).

    Args:
        export: The open export.
        exams: Exams to analyse.
        lowpass_hz: Low-pass filter (see :func:`analyze_exam`).
        detrend: Remove the linear trend (see :func:`analyze_exam`).
        eyes_closed_first: Romberg order without labels (see :func:`analyze_exam`).
        progress: Called before each exam with ``(index, count, exam)``; returning
            ``False`` stops (the exams analysed so far are returned).

    Returns:
        The analyses in the order of ``exams`` and ``(exam, reason)`` of the exams
        that could not be analysed.
    """
    analyses: list[ExamAnalysis] = []
    failed: list[tuple[Exam, str]] = []
    for index, exam in enumerate(exams):
        if progress is not None and not progress(index, len(exams), exam):
            break
        try:
            with export.open_fre(exam) as fre:
                analyses.append(
                    analyze_exam(
                        fre.recording, exam, lowpass_hz=lowpass_hz, detrend=detrend, eyes_closed_first=eyes_closed_first
                    )
                )
        except (OSError, ValueError, RuntimeError) as exc:
            failed.append((exam, str(exc)))
    return analyses, failed


def series(analyses: Sequence[ExamAnalysis], parameter: str) -> dict[str, list[float | None]]:
    """Return the values of a parameter over the exams, one list per condition.

    Args:
        analyses: The exams in the order to plot.
        parameter: A :class:`~freestep.analysis.stabilometry.Stabilometry` attribute, or
            :data:`ROMBERG_PREFIX` + one of :data:`~freestep.analysis.stabilometry.ROMBERG_PARAMETERS`
            for the Romberg quotient.

    Returns:
        ``{condition: [value per exam]}`` with ``None`` where an exam has no value;
        conditions in the order of their first appearance (the Romberg quotient
        has the single condition :data:`ROMBERG_CONDITION`).
    """
    if parameter.startswith(ROMBERG_PREFIX):
        name = parameter.removeprefix(ROMBERG_PREFIX)
        if name not in ROMBERG_PARAMETERS:
            raise ValueError(f"Unknown Romberg parameter {name!r}")
        return {ROMBERG_CONDITION: [_finite(a.romberg.quotients[name]) if a.romberg else None for a in analyses]}
    if parameter not in Stabilometry.__dataclass_fields__:
        raise ValueError(f"Unknown parameter {parameter!r}")
    values: dict[str, list[float | None]] = {}
    for column, analysis in enumerate(analyses):
        for condition, result in zip(analysis.conditions, analysis.results, strict=True):
            row = values.setdefault(condition, [None] * len(analyses))
            value = None if result is None else getattr(result, parameter)
            row[column] = None if value is None else _finite(float(value))
    return values


def _finite(value: float) -> float | None:
    """Return the value, ``None`` for NaN or infinity."""
    return value if math.isfinite(value) else None


def history_rows(analyses: Sequence[ExamAnalysis]) -> list[dict[str, object]]:
    """Return a table of the analyses: one row per exam and acquisition.

    Columns: ``exam_id``, ``exam_date``, ``exam_time``, ``acquisition`` (1-based),
    ``condition`` (see :attr:`ExamAnalysis.conditions`), all stabilometric
    parameters and the Romberg quotients (``romberg_<parameter>``).
    """
    rows: list[dict[str, object]] = []
    for analysis in analyses:
        exam = analysis.exam
        for index, (condition, result) in enumerate(zip(analysis.conditions, analysis.results, strict=True)):
            row: dict[str, object] = {
                "exam_id": exam.id,
                "exam_date": exam.exam_date or "",
                "exam_time": exam.exam_time or "",
                "acquisition": index + 1,
                "condition": condition,
            }
            if result is not None:
                row.update(result.as_dict())
            if analysis.romberg is not None:
                row.update({f"{ROMBERG_PREFIX}{n}": analysis.romberg.quotients[n] for n in ROMBERG_PARAMETERS})
            rows.append(row)
    return rows
