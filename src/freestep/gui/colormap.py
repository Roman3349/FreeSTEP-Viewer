"""Colour map for pressure values (white = no load, blue → green → yellow → red)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# Control points (position 0..1, RGB)
_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.00, (255, 255, 255)),
    (0.01, (40, 60, 200)),
    (0.25, (0, 170, 230)),
    (0.45, (40, 200, 80)),
    (0.65, (250, 230, 0)),
    (0.85, (245, 120, 0)),
    (1.00, (200, 0, 0)),
]


def build_lut(size: int = 256) -> NDArray[np.uint8]:
    """Return a ``(size, 4)`` lookup table in Qt ``Format_ARGB32`` byte order (B, G, R, A)."""
    positions = np.array([p for p, _ in _STOPS])
    colours = np.array([c for _, c in _STOPS], dtype=np.float64)
    x = np.linspace(0.0, 1.0, size)
    rgb = np.stack([np.interp(x, positions, colours[:, i]) for i in range(3)], axis=1)
    lut = np.empty((size, 4), dtype=np.uint8)
    lut[:, 0] = rgb[:, 2]
    lut[:, 1] = rgb[:, 1]
    lut[:, 2] = rgb[:, 0]
    lut[:, 3] = 255
    lut[0] = (255, 255, 255, 255)  # exact zero stays white
    return lut


LUT = build_lut()


def colorize(values: NDArray[np.generic], max_value: float) -> NDArray[np.uint8]:
    """Map a 2-D array to a contiguous ``(rows, cols, 4)`` BGRA image."""
    scale = (len(LUT) - 1) / max_value if max_value > 0 else 0.0
    data = np.asarray(values, dtype=np.float64)
    idx = np.clip(data * scale, 0, len(LUT) - 1)
    # Non-zero values never map to the "no load" colour
    idx = np.where((data > 0) & (idx < 1), 1, idx).astype(np.intp)
    return np.ascontiguousarray(LUT[idx])
