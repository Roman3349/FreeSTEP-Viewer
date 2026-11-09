"""Analysis of a static exam: the footprint of one pressure frame.

The feet are split in the gap between them and every foot in the front and rear
half of its length like in :mod:`freestep.analysis.load`. Values are raw sensor units
(FreeSTEP does not store a calibration), lengths in cm, areas in cm².
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..recording import RecordingHeader
from .load import Foot, LoadRegions, load_regions


@dataclass(frozen=True)
class FootPrint:
    """Footprint of one foot, or of both feet together.

    Attributes:
        load_pct: Share of the total load.
        forefoot_pct: Share of this part's load on the front half of the foot(s).
        rearfoot_pct: Share on the rear half.
        contact_area_cm2: Area of the loaded cells.
        length_cm: Length of the footprint (rows from the first to the last loaded one); ``None`` for both feet.
        width_cm: Width of the footprint (columns); ``None`` for both feet.
        peak_value: Highest sensor value.
        mean_value: Mean value of the loaded cells.
        cop_cm: Centre of pressure ``(x, y)`` in cm (cell index convention), ``None`` without load.
    """

    load_pct: float
    forefoot_pct: float | None
    rearfoot_pct: float | None
    contact_area_cm2: float
    length_cm: float | None
    width_cm: float | None
    peak_value: int
    mean_value: float | None
    cop_cm: tuple[float, float] | None


@dataclass(frozen=True)
class StaticAnalysis:
    """Footprints of a static exam.

    Attributes:
        left: The left foot (``None`` when unloaded).
        right: The right foot (``None`` when unloaded).
        total: Both feet together.
        regions: Split between the feet and their halves (for drawing).
    """

    left: FootPrint | None
    right: FootPrint | None
    total: FootPrint
    regions: LoadRegions

    @property
    def quadrants(self) -> tuple[float | None, float | None, float | None, float | None]:
        """Shares of the total load on the left front, left rear, right front and right rear quadrant in %."""
        values: list[float | None] = []
        for foot in (self.left, self.right):
            if foot is None or foot.forefoot_pct is None:
                values += [None, None]
            else:
                values += [foot.load_pct * foot.forefoot_pct / 100, foot.load_pct * (100 - foot.forefoot_pct) / 100]
        return values[0], values[1], values[2], values[3]


def analyze_static(frame: NDArray[np.integer], header: RecordingHeader) -> StaticAnalysis:
    """Analyse the footprint of one pressure frame.

    Args:
        frame: Pressure values ``(rows, columns)``.
        header: Plate geometry (cell size).

    Returns:
        The analysis.

    Raises:
        ValueError: The frame has no load.
    """
    load = frame.astype(np.float64)
    total = float(load.sum())
    if total <= 0:
        raise ValueError("The frame has no load")
    regions = load_regions(load)
    half = regions.split_column
    cell_cm2 = header.cell_width_mm * header.cell_height_mm / 100
    rows = load.shape[0]

    def part(foot: Foot | None, cells: NDArray[np.float64], offset: int) -> FootPrint | None:
        if foot is None or cells.sum() <= 0:
            return None
        weight = float(cells.sum())
        front = float(cells.sum(axis=1) @ foot.front_weights(rows))
        loaded = cells[cells > 0]
        ys, xs = np.indices(cells.shape)
        return FootPrint(
            load_pct=weight / total * 100,
            forefoot_pct=front / weight * 100,
            rearfoot_pct=100 - front / weight * 100,
            contact_area_cm2=len(loaded) * cell_cm2,
            length_cm=(foot.last_row - foot.first_row + 1) * header.cell_height_mm / 10,
            width_cm=(foot.last_column - foot.first_column + 1) * header.cell_width_mm / 10,
            peak_value=int(loaded.max()),
            mean_value=float(loaded.mean()),
            cop_cm=(
                float((cells * (xs + offset)).sum() / weight * header.cell_width_mm / 10),
                float((cells * ys).sum() / weight * header.cell_height_mm / 10),
            ),
        )

    left = part(regions.left, load[:, :half], 0)
    right = part(regions.right, load[:, half:], half)
    feet = [f for f in (left, right) if f is not None]
    front = sum(f.load_pct * (f.forefoot_pct or 0) / 100 for f in feet)
    loaded = load[load > 0]
    ys, xs = np.indices(load.shape)
    both = FootPrint(
        load_pct=100.0,
        forefoot_pct=front,
        rearfoot_pct=100 - front,
        contact_area_cm2=len(loaded) * cell_cm2,
        length_cm=None,
        width_cm=None,
        peak_value=int(loaded.max()),
        mean_value=float(loaded.mean()),
        cop_cm=(
            float((load * xs).sum() / total * header.cell_width_mm / 10),
            float((load * ys).sum() / total * header.cell_height_mm / 10),
        ),
    )
    return StaticAnalysis(left=left, right=right, total=both, regions=regions)
