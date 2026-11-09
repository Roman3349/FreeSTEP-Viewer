"""Load distribution between the feet and between their front and rear halves (quadrants).

The feet are found in the load summed over the frames of a recording; the
shares are computed per frame and averaged (see :func:`load_distribution`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

#: Rows of a foot whose summed load is below this fraction of the most loaded row are not part of the foot.
FOOT_ROW_THRESHOLD = 0.02


class LoadDistribution(TypedDict, total=False):
    """The load distribution fields of :class:`Stabilometry` (empty when nothing is loaded)."""

    left_load_pct: float
    right_load_pct: float
    load_asymmetry_pct: float
    left_load_sd_pct: float
    forefoot_pct: float | None
    rearfoot_pct: float | None
    left_forefoot_pct: float | None
    right_forefoot_pct: float | None
    left_front_pct: float | None
    left_rear_pct: float | None
    right_front_pct: float | None
    right_rear_pct: float | None


@dataclass(frozen=True)
class Foot:
    """Extent and centre of pressure of one foot in sensor cells (rows toward 0 are the front).

    Attributes:
        first_row: First row of the foot's length.
        last_row: Last row of the foot's length.
        middle_row: Boundary between the front and the rear half (a row cut by it is split between both).
        first_column: First loaded column of the foot.
        last_column: Last loaded column of the foot.
        cop: Load-weighted centre (column, row), cell index convention of FreeSTEP's COP.
    """

    first_row: int
    last_row: int
    middle_row: float
    first_column: int
    last_column: int
    cop: tuple[float, float]

    def front_weights(self, rows: int) -> NDArray[np.float64]:
        """Fraction of every row that belongs to the front half."""
        return np.clip(self.middle_row - np.arange(rows), 0, 1)


@dataclass(frozen=True)
class LoadRegions:
    """Division of the plate into the feet and their front and rear halves (quadrants).

    Attributes:
        split_column: First column of the right foot; it lies in the gap between the feet.
        left: The left foot, ``None`` when unloaded.
        right: The right foot, ``None`` when unloaded.
        cop: Load-weighted centre (column, row) of the whole plate, ``None`` without load.
    """

    split_column: int
    left: Foot | None
    right: Foot | None
    cop: tuple[float, float] | None


def load_regions(load: NDArray[np.floating] | NDArray[np.integer]) -> LoadRegions:
    """Find the feet and their front and rear halves in the load summed over the frames.

    The feet are split in the gap between them: the middle of the longest run of
    unloaded columns between the first and last loaded column (FreeSTEP splits at
    the middle of the plate, which cuts a foot when the patient does not stand in
    the centre). When the feet touch, the least loaded column in the middle half
    of the loaded width is used. A foot's front and rear half are split in the
    middle of its length; the toes are assumed to point to row 0.

    Args:
        load: Load summed over the frames, ``(rows, columns)``.

    Returns:
        The regions.
    """
    summed = np.asarray(load, dtype=np.float64)
    columns = summed.shape[1]
    profile = summed.sum(axis=0)
    loaded = np.flatnonzero(profile > FOOT_ROW_THRESHOLD * profile.max()) if profile.max() > 0 else np.array([])
    split = columns // 2
    if len(loaded) >= 2:
        first, last = int(loaded[0]), int(loaded[-1])
        inside = profile[first : last + 1] > FOOT_ROW_THRESHOLD * profile.max()
        gaps = _runs(~inside)
        if gaps:
            start, stop = max(gaps, key=lambda run: (run[1] - run[0], -abs((run[0] + run[1]) / 2 + first - split)))
            split = first + (start + stop + 1) // 2
        else:  # touching feet
            quarter = (last - first) // 4
            middle = profile[first + quarter : last - quarter + 1]
            split = first + quarter + int(np.argmin(middle)) + 1
    total = summed.sum()
    rows, cols = np.indices(summed.shape)
    cop = (float((summed * cols).sum() / total), float((summed * rows).sum() / total)) if total > 0 else None
    return LoadRegions(split, _foot(summed, 0, split), _foot(summed, split, columns), cop)


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Return ``(start, stop)`` of every run of ``True`` (stop exclusive)."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True))


def _foot(load: NDArray[np.float64], start: int, stop: int) -> Foot | None:
    """Extent and COP of the foot in columns ``start:stop``, ``None`` when unloaded."""
    part = load[:, start:stop]
    profile = part.sum(axis=1)
    if profile.max() <= 0:
        return None
    rows = np.flatnonzero(profile >= FOOT_ROW_THRESHOLD * profile.max())
    first, last = int(rows[0]), int(rows[-1])
    widths = part.sum(axis=0)
    columns = np.flatnonzero(widths >= FOOT_ROW_THRESHOLD * widths.max())
    ys, xs = np.indices(part.shape)
    total = part.sum()
    cop = (float((part * xs).sum() / total) + start, float((part * ys).sum() / total))
    return Foot(first, last, (first + last + 1) / 2, start + int(columns[0]), start + int(columns[-1]), cop)


def load_distribution(pressure: NDArray[np.integer]) -> LoadDistribution:
    """Compute the load distribution between the feet and between their front and rear halves.

    The regions are found by :func:`load_regions` in the summed load. All shares
    are means over the loaded frames of the share in every frame, so the
    quadrants of a foot add up to its share.

    Args:
        pressure: Frames ``(frames, rows, columns)``; frames without load are ignored.

    Returns:
        The ``*_pct`` fields of :class:`Stabilometry` (see there for the definitions).
    """
    frames = pressure.astype(np.float64)
    total = frames.sum(axis=(1, 2))
    loaded = total > 0
    if not loaded.any():
        return LoadDistribution()
    frames, total = frames[loaded], total[loaded]
    regions = load_regions(frames.sum(axis=0))
    half = regions.split_column
    rows = frames.shape[1]
    # Load of every frame on the front and rear half of each foot, as shares of the frame's total load
    quadrants = []
    for foot, part in ((regions.left, frames[:, :, :half]), (regions.right, frames[:, :, half:])):
        by_row = part.sum(axis=2)
        front = by_row @ foot.front_weights(rows) if foot is not None else np.zeros(len(frames))
        quadrants += [front / total * 100, (by_row.sum(axis=1) - front) / total * 100]
    left_share = quadrants[0] + quadrants[1]
    left_front, left_rear, right_front, right_rear = (float(q.mean()) for q in quadrants)

    def share(part: float, whole: float) -> float | None:
        return part / whole * 100 if whole > 0 else None

    left_pct = float(left_share.mean())
    has_feet = regions.left is not None or regions.right is not None
    return LoadDistribution(
        left_load_pct=left_pct,
        right_load_pct=100 - left_pct,
        load_asymmetry_pct=100 - 2 * left_pct,
        left_load_sd_pct=float(left_share.std(ddof=1)) if len(left_share) > 1 else 0.0,
        forefoot_pct=left_front + right_front if has_feet else None,
        rearfoot_pct=left_rear + right_rear if has_feet else None,
        left_forefoot_pct=share(left_front, left_front + left_rear),
        right_forefoot_pct=share(right_front, right_front + right_rear),
        left_front_pct=left_front if regions.left is not None else None,
        left_rear_pct=left_rear if regions.left is not None else None,
        right_front_pct=right_front if regions.right is not None else None,
        right_rear_pct=right_rear if regions.right is not None else None,
    )
