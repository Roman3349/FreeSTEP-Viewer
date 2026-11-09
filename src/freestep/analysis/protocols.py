"""Test protocols of posturography: the condition of every acquisition and the comparisons between them.

FreeSTEP records a posturography exam as a series of acquisitions; its protocol
names them *Bipodalica OA / OC* (standing on both feet with eyes open / closed,
the Romberg test) and *Monopodalica DX / SX OA / OC* (standing on the right /
left foot). The labels are usually not stored, so the condition is derived:

- the stance from the load distribution: an acquisition with at least
  :data:`SINGLE_LEG_SHARE_PCT` of the load on one foot is single-leg stance;
- the eyes from the labels, otherwise from the order: of two acquisitions in the
  same stance the first is with eyes open (the classic protocol), unless the
  caller says eyes closed come first.

The evaluations:

- Romberg quotient (eyes closed / eyes open) for standing on both feet and, when
  recorded, for each foot;
- symmetry of single-leg stance: ``(left − right) / mean(left, right)`` in % for the
  sway parameters, per eye condition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ..recording import Recording
from .stabilometry import POSTUROGRAPHY_EXAM_TYPE, Stabilometry

#: Share of the load on one foot from which an acquisition counts as single-leg stance.
SINGLE_LEG_SHARE_PCT = 85.0
#: Maximum relative difference of the durations of two acquisitions whose eye condition is taken from the order.
ROMBERG_DURATION_TOLERANCE = 0.1
#: Parameters compared by the Romberg quotient (eyes closed / eyes open).
ROMBERG_PARAMETERS = (
    "path_length_mm",
    "mean_velocity_mm_s",
    "ellipse_area_mm2",
    "sway_area_mm2_s",
    "sd_ml_mm",
    "sd_ap_mm",
    "range_ml_mm",
    "range_ap_mm",
)
#: Parameters compared between the left and the right foot in single-leg stance.
SYMMETRY_PARAMETERS = (
    "path_length_mm",
    "mean_velocity_mm_s",
    "ellipse_area_mm2",
    "sd_ml_mm",
    "sd_ap_mm",
)

# Words in acquisition labels (English, Italian, Czech); short ones (EO, DX, …) must be whole words.
_OPEN_WORDS = ("open", "aperti", "otevř", "otevr", "eo", "oa")
_CLOSED_WORDS = ("closed", "chiusi", "zavř", "zavr", "ec", "oc")
_LEFT_WORDS = ("left", "sinistr", "lev", "sx")
_RIGHT_WORDS = ("right", "destr", "prav", "dx")
_BOTH_WORDS = ("bipod", "both", "obou", "two")


class Stance(Enum):
    """How the subject stands on the plate."""

    BOTH = "both"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class Condition:
    """Condition of one acquisition.

    Attributes:
        stance: On both feet or on one foot.
        eyes_closed: ``True`` with eyes closed, ``False`` open, ``None`` when not known.
        stance_from_label: The stance was read from the label (else from the load distribution).
        eyes_from_label: The eye condition was read from the label (else assumed from the order).
    """

    stance: Stance
    eyes_closed: bool | None
    stance_from_label: bool = False
    eyes_from_label: bool = False


@dataclass(frozen=True)
class Romberg:
    """Evaluation of a Romberg test (the same stance with eyes open and with eyes closed).

    Attributes:
        eyes_open: Index of the eyes-open acquisition.
        eyes_closed: Index of the eyes-closed acquisition.
        order_from_labels: The eye condition was read from the acquisition labels;
            otherwise the order was used (FreeSTEP stores none).
        quotients: Romberg quotient (eyes closed / eyes open) of every parameter in
            :data:`ROMBERG_PARAMETERS`; ``nan`` when the eyes-open value is zero.
        stance: Both feet (the classic test) or one foot.
    """

    eyes_open: int
    eyes_closed: int
    order_from_labels: bool
    quotients: dict[str, float]
    stance: Stance = Stance.BOTH


@dataclass(frozen=True)
class LegSymmetry:
    """Comparison of single-leg stance on the left and on the right foot.

    Attributes:
        left: Index of the acquisition on the left foot.
        right: Index of the acquisition on the right foot.
        eyes_closed: Eye condition of both acquisitions (``None``: not known).
        symmetry_pct: ``(left − right) / mean(left, right) × 100`` of every parameter in
            :data:`SYMMETRY_PARAMETERS`; positive when the left foot sways more, ``nan``
            when both values are zero.
    """

    left: int
    right: int
    eyes_closed: bool | None
    symmetry_pct: dict[str, float]


@dataclass
class Assessment:
    """The conditions of the acquisitions of an exam and the tests they form.

    Attributes:
        conditions: Condition of every acquisition, ``None`` where it is not known
            (not analysed, or not a posturography exam).
        results: The parameters the tests were evaluated from (``None`` where not analysed).
        rombergs: Romberg tests: on both feet first, then on the left and the right foot.
        legs: Left / right comparisons of single-leg stance, eyes open first.
    """

    conditions: list[Condition | None]
    results: list[Stabilometry | None] = field(default_factory=list)
    rombergs: list[Romberg] = field(default_factory=list)
    legs: list[LegSymmetry] = field(default_factory=list)

    @property
    def romberg(self) -> Romberg | None:
        """The Romberg test on both feet, ``None`` when there is none."""
        return next((r for r in self.rombergs if r.stance == Stance.BOTH), None)


def condition_key(condition: Condition | None) -> str | None:
    """Name of a condition in tables and files.

    ``eyes_open`` / ``eyes_closed`` on both feet (as in a Romberg test), ``left_foot`` /
    ``right_foot`` in single-leg stance, with ``_eyes_open`` / ``_eyes_closed`` when known;
    ``None`` when the condition is not known.
    """
    if condition is None:
        return None
    eyes = {False: "eyes_open", True: "eyes_closed", None: ""}[condition.eyes_closed]
    if condition.stance == Stance.BOTH:
        return eyes or "both_feet"
    foot = f"{condition.stance.value}_foot"
    return f"{foot}_{eyes}" if eyes else foot


def assess(
    post: Recording,
    results: list[Stabilometry | None],
    exam_type: int | None = None,
    eyes_closed_first: bool = False,
) -> Assessment:
    """Find the condition of every acquisition and evaluate the Romberg tests and the single-leg symmetry.

    Args:
        post: The recording.
        results: Parameters of every acquisition (``None`` where the analysis failed).
        exam_type: Type code of the exam; another type than posturography has no protocol.
        eyes_closed_first: Without labels, the first of two acquisitions in the same stance is with eyes closed.

    Returns:
        The assessment.
    """
    if (exam_type is not None and exam_type != POSTUROGRAPHY_EXAM_TYPE) or post.gait:
        return Assessment([None] * len(post.acquisitions), results)
    conditions = classify(post, results, eyes_closed_first)
    assessment = Assessment(conditions, results)
    for stance in Stance:
        romberg = _romberg(conditions, results, stance)
        if romberg is not None:
            assessment.rombergs.append(romberg)
    for eyes in (False, True, None):
        legs = _leg_symmetry(conditions, results, eyes)
        if legs is not None:
            assessment.legs.append(legs)
    return assessment


def evaluate_romberg(
    post: Recording,
    results: list[Stabilometry | None],
    exam_type: int | None = None,
    eyes_closed_first: bool = False,
) -> Romberg | None:
    """Evaluate the Romberg test on both feet (see :func:`assess`), ``None`` when there is none."""
    return assess(post, results, exam_type, eyes_closed_first).romberg


def classify(
    post: Recording, results: list[Stabilometry | None], eyes_closed_first: bool = False
) -> list[Condition | None]:
    """Find the stance and the eye condition of every acquisition (see the module description).

    Args:
        post: The recording.
        results: Parameters of every acquisition; the load distribution tells the stance.
        eyes_closed_first: Without labels, the first of two acquisitions in the same stance is with eyes closed.

    Returns:
        The condition of every acquisition, ``None`` where neither the label nor the analysis tells the stance.
    """
    stances: list[tuple[Stance, bool] | None] = []
    for acq, result in zip(post.acquisitions, results, strict=True):
        labelled = _label_stance(acq.label)
        if labelled is not None:
            stances.append((labelled, True))
        elif result is not None and result.left_load_pct is not None:
            stances.append((_load_stance(result.left_load_pct), False))
        else:
            stances.append(None)
    conditions: list[Condition | None] = []
    for index, stance in enumerate(stances):
        if stance is None:
            conditions.append(None)
            continue
        eyes = _label_eyes(post.acquisitions[index].label)
        if eyes is not None:
            conditions.append(Condition(stance[0], eyes, stance[1], True))
            continue
        same = [i for i, s in enumerate(stances) if s is not None and s[0] == stance[0]]
        conditions.append(Condition(stance[0], _eyes_from_order(post, same, index, eyes_closed_first), stance[1]))
    return conditions


def _eyes_from_order(post: Recording, same: list[int], index: int, eyes_closed_first: bool) -> bool | None:
    """Eye condition of an unlabelled acquisition from its place among the acquisitions in the same stance.

    Only a pair of acquisitions of about the same duration without eye labels is
    taken as eyes open and eyes closed; anything else stays unknown.
    """
    unlabelled = [i for i in same if _label_eyes(post.acquisitions[i].label) is None]
    if len(same) != 2 or len(unlabelled) != 2:
        return None
    first, second = (post.acquisitions[i].duration_s for i in same)
    longer = max(first, second)
    if longer <= 0 or abs(first - second) > ROMBERG_DURATION_TOLERANCE * longer:
        return None
    return (same.index(index) == 0) == eyes_closed_first


def _romberg(conditions: list[Condition | None], results: list[Stabilometry | None], stance: Stance) -> Romberg | None:
    """Romberg test in one stance: the first eyes-open and the first eyes-closed acquisition."""
    eo = next((i for i, c in enumerate(conditions) if c and c.stance == stance and c.eyes_closed is False), None)
    ec = next((i for i, c in enumerate(conditions) if c and c.stance == stance and c.eyes_closed is True), None)
    if eo is None or ec is None:
        return None
    open_result, closed_result = results[eo], results[ec]
    if open_result is None or closed_result is None:
        return None
    quotients = {}
    for name in ROMBERG_PARAMETERS:
        reference = float(getattr(open_result, name))
        quotients[name] = float(getattr(closed_result, name)) / reference if reference else math.nan
    from_labels = all(c is not None and c.eyes_from_label for c in (conditions[eo], conditions[ec]))
    return Romberg(eyes_open=eo, eyes_closed=ec, order_from_labels=from_labels, quotients=quotients, stance=stance)


def _leg_symmetry(
    conditions: list[Condition | None], results: list[Stabilometry | None], eyes: bool | None
) -> LegSymmetry | None:
    """Left / right comparison of single-leg stance with the given eye condition."""

    def find(stance: Stance) -> int | None:
        return next(
            (
                i
                for i, c in enumerate(conditions)
                if c and c.stance == stance and c.eyes_closed is eyes and results[i] is not None
            ),
            None,
        )

    left, right = find(Stance.LEFT), find(Stance.RIGHT)
    if left is None or right is None:
        return None
    left_result, right_result = results[left], results[right]
    assert left_result is not None and right_result is not None
    symmetry = {}
    for name in SYMMETRY_PARAMETERS:
        a, b = float(getattr(left_result, name)), float(getattr(right_result, name))
        symmetry[name] = (a - b) / ((a + b) / 2) * 100 if a + b else math.nan
    return LegSymmetry(left=left, right=right, eyes_closed=eyes, symmetry_pct=symmetry)


def _load_stance(left_load_pct: float) -> Stance:
    """Stance from the share of the load on the left foot."""
    if left_load_pct >= SINGLE_LEG_SHARE_PCT:
        return Stance.LEFT
    if left_load_pct <= 100 - SINGLE_LEG_SHARE_PCT:
        return Stance.RIGHT
    return Stance.BOTH


def _words(label: str) -> list[str]:
    """Lower-case words of a label."""
    return "".join(c if c.isalnum() else " " for c in label.casefold()).split()


def _matches(words: list[str], hints: tuple[str, ...]) -> bool:
    """A word of the label is one of the hints (short hints whole, longer ones as prefixes)."""
    return any(w == h if len(h) <= 2 else w.startswith(h) for w in words for h in hints)


def _label_eyes(label: str) -> bool | None:
    """``True`` for an eyes-closed label, ``False`` for eyes open, ``None`` when the label does not tell."""
    words = _words(label)
    is_open, is_closed = _matches(words, _OPEN_WORDS), _matches(words, _CLOSED_WORDS)
    return None if is_open == is_closed else is_closed


def _label_stance(label: str) -> Stance | None:
    """Stance named in a label, ``None`` when the label does not tell."""
    words = _words(label)
    left, right = _matches(words, _LEFT_WORDS), _matches(words, _RIGHT_WORDS)
    if left != right and not _matches(words, _BOTH_WORDS):
        return Stance.LEFT if left else Stance.RIGHT
    return Stance.BOTH if _matches(words, _BOTH_WORDS) else None
