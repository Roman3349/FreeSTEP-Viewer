"""Tests of the test protocols: conditions of the acquisitions, Romberg tests and single-leg symmetry."""

import math

import numpy as np
import pytest

from freestep.analysis.protocols import Stance, assess, classify, evaluate_romberg
from freestep.analysis.stabilometry import POSTUROGRAPHY_EXAM_TYPE, analyze_all
from freestep.recording import Acquisition, Recording, RecordingHeader, frame_dtype

ROWS, COLUMNS, FS = 8, 10, 25.0
HEADER = RecordingHeader(3, "Test", COLUMNS, ROWS, 5, 5, 255, (), 74)


Spec = tuple[int, int, float, str] | tuple[int, int, float, str, float]


def _acquisition(index: int, spec: Spec) -> Acquisition:
    """Acquisition ``(left load, right load, sway radius in cm, label[, seconds])``: constant loads, COP on a circle."""
    left, right, radius_cm, label = spec[:4]
    seconds = spec[4] if len(spec) == 5 else 20.0
    count = round(seconds * FS)
    records = np.zeros(count, frame_dtype(ROWS, COLUMNS))
    records["pressure"][:, 2:6, 2] = left
    records["pressure"][:, 2:6, 7] = right
    records["timestamp_ms"] = 1000 + np.arange(count) * 40
    phase = 2 * np.pi * 0.5 * np.arange(count) / FS
    records["cop_total"] = np.column_stack([2.5 + radius_cm * np.cos(phase), 2 + radius_cm * np.sin(phase)])
    return Acquisition(index, records, records["pressure"][0], label, HEADER)


def _recording(*specs: Spec) -> Recording:
    """Recording of acquisitions given as ``(left load, right load, sway radius in cm, label[, seconds])``."""
    acquisitions = [_acquisition(i, spec) for i, spec in enumerate(specs)]
    return Recording(HEADER, acquisitions, b"")


@pytest.mark.parametrize(
    ("labels", "closed_first", "expected"),
    [
        (("", ""), False, (0, 1, False)),
        (("", ""), True, (1, 0, False)),
        (("Occhi chiusi", "Occhi aperti"), False, (1, 0, True)),
        (("EO", "EC"), True, (0, 1, True)),
        (("Bipodalica OC", "Bipodalica OA"), False, (1, 0, True)),
    ],
)
def test_romberg_order(labels: tuple[str, str], closed_first: bool, expected: tuple[int, int, bool]) -> None:
    """The eye condition comes from the labels, otherwise from the order."""
    post = _recording((10, 10, 0.2, labels[0]), (10, 10, 0.4, labels[1]))
    romberg = evaluate_romberg(post, analyze_all(post), POSTUROGRAPHY_EXAM_TYPE, closed_first)
    assert romberg is not None
    assert (romberg.eyes_open, romberg.eyes_closed, romberg.order_from_labels) == expected
    assert evaluate_romberg(post, analyze_all(post), 0) is None  # not a posturography exam


def test_romberg_quotients() -> None:
    """The quotients compare eyes closed with eyes open; without a result there is no test."""
    post = _recording((10, 10, 0.2, ""), (10, 10, 0.4, ""))
    results = analyze_all(post)
    romberg = evaluate_romberg(post, results)
    assert romberg is not None and romberg.stance is Stance.BOTH
    assert romberg.quotients["path_length_mm"] == pytest.approx(2, rel=1e-2)
    assert romberg.quotients["ellipse_area_mm2"] == pytest.approx(4, rel=1e-2)
    assert evaluate_romberg(post, [results[0], None]) is None


def test_freestep_protocol_without_labels() -> None:
    """Romberg on both feet, then single-leg stance on the right and on the left foot, detected from the load."""
    post = _recording(
        (10, 10, 0.2, "", 50),
        (10, 10, 0.4, "", 50),
        (1, 20, 0.6, "", 10),
        (20, 1, 1.2, "", 10),
    )
    results = analyze_all(post)
    conditions = classify(post, results)
    assert [c.stance if c else None for c in conditions] == [Stance.BOTH, Stance.BOTH, Stance.RIGHT, Stance.LEFT]
    assert [c.eyes_closed if c else None for c in conditions] == [False, True, None, None]
    assessment = assess(post, results, POSTUROGRAPHY_EXAM_TYPE)
    assert assessment.romberg is not None and len(assessment.rombergs) == 1
    (legs,) = assessment.legs
    assert (legs.left, legs.right, legs.eyes_closed) == (3, 2, None)
    # The left foot sways twice as much: (2 − 1) / 1.5
    assert legs.symmetry_pct["path_length_mm"] == pytest.approx(100 / 1.5, rel=1e-2)


def test_single_leg_from_labels() -> None:
    """FreeSTEP's labels tell the foot and the eyes, also of a Romberg test on one foot."""
    post = _recording(
        (1, 20, 0.3, "Monopodalica DX OA"),
        (1, 20, 0.6, "Monopodalica DX OC"),
        (20, 1, 0.3, "Monopodalica SX OA"),
        (20, 1, 0.3, "Monopodalica SX OC"),
    )
    assessment = assess(post, analyze_all(post))
    assert [(c.stance, c.eyes_closed) for c in assessment.conditions if c] == [
        (Stance.RIGHT, False),
        (Stance.RIGHT, True),
        (Stance.LEFT, False),
        (Stance.LEFT, True),
    ]
    assert assessment.romberg is None
    assert [(r.stance, r.eyes_open, r.eyes_closed, r.order_from_labels) for r in assessment.rombergs] == [
        (Stance.LEFT, 2, 3, True),
        (Stance.RIGHT, 0, 1, True),
    ]
    assert [(s.left, s.right, s.eyes_closed) for s in assessment.legs] == [(2, 0, False), (3, 1, True)]
    assert assessment.legs[0].symmetry_pct["ellipse_area_mm2"] == pytest.approx(0, abs=1)


def test_unknown_conditions() -> None:
    """Without an analysis the stance is not known; gait and other exam types have no protocol."""
    post = _recording((10, 10, 0.2, ""), (10, 10, 0.4, ""))
    assessment = assess(post, [None, None])
    assert assessment.conditions == [None, None] and assessment.romberg is None
    assert assess(post, analyze_all(post), exam_type=1).conditions == [None, None]


def test_length_per_area() -> None:
    """L/S is the path length over the ellipse area."""
    post = _recording((10, 10, 0.2, ""))
    (result,) = analyze_all(post)
    assert result is not None
    assert result.length_per_area_1_mm == pytest.approx(result.path_length_mm / result.ellipse_area_mm2)
    assert not math.isnan(result.length_per_area_1_mm)
