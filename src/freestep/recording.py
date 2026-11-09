"""A pressure recording: plate geometry, acquisitions of frames with their COP.

The recordings of all exam types are read into this model (see :mod:`freestep.formats`):
posturography (``Postura.dat``), static exams (one frame of ``Statica.dat``) and gait
over several plates (``Dinamica.dat``, ``TreadMILL.dat``). Positions are in cm with the
cell-index convention of FreeSTEP: ``cell index × cell size`` without a half-cell offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class RecordingHeader:
    """Plate geometry and file header of a pressure recording.

    Attributes:
        version: Format version of the file (``Postura.dat``: 1, 3; ``Statica.dat``: 3; ``Dinamica.dat``: 2, 4).
        platform: Name of the pressure plate, e.g. ``"Max 50x60"``.
        columns: Number of sensor columns (X axis).
        rows: Number of sensor rows (Y axis).
        cell_width_mm: Width of one sensor cell in mm.
        cell_height_mm: Height of one sensor cell in mm.
        max_value: Maximum raw sensor value (255 for 8-bit plates).
        settings: Version specific int16 values of not yet known meaning
            (v3: ``(-1, 512, 0, 0, 1, 1)``, the 512 differs between exams).
        size: Size of the file header in bytes (``Postura.dat`` v1: 66, v3: 74).
        plates: Number of plates in a row joined into the columns (gait, see :mod:`freestep.formats.walkway`).
    """

    version: int
    platform: str
    columns: int
    rows: int
    cell_width_mm: int
    cell_height_mm: int
    max_value: int
    settings: tuple[int, ...]
    size: int
    plates: int = 1

    @property
    def matrix_size(self) -> int:
        """Size of one pressure matrix in bytes."""
        return self.rows * self.columns * 2

    @property
    def width_cm(self) -> float:
        """Width of the sensing area in cm."""
        return self.columns * self.cell_width_mm / 10

    @property
    def height_cm(self) -> float:
        """Height of the sensing area in cm."""
        return self.rows * self.cell_height_mm / 10


def frame_dtype(rows: int, columns: int) -> np.dtype:
    """Build the NumPy structured dtype of one frame.

    Args:
        rows: Number of sensor rows.
        columns: Number of sensor columns.

    Returns:
        Dtype with the fields ``pressure``, ``timestamp_ms``, ``unknown``,
        ``cop_total``, ``cop_left`` and ``cop_right`` (see the module docstring).
    """
    return np.dtype(
        [
            ("pressure", "<u2", (rows, columns)),
            ("timestamp_ms", "<i4"),
            ("unknown", "<i2"),
            ("cop_total", "<f4", (2,)),
            ("cop_left", "<f4", (2,)),
            ("cop_right", "<f4", (2,)),
        ]
    )


@dataclass
class Acquisition:
    """One acquisition (continuous recording; a pass of a gait recording).

    Arrays are zero-copy, read-only views into the file content.

    Attributes:
        index: Zero-based position in the file.
        records: Structured array of the frames (fields see :func:`frame_dtype`).
        mean_image: Mean of every cell over the frames where it is loaded, as
            stored by FreeSTEP, shape ``(rows, columns)``.
        label: Label stored with the acquisition (often empty; v1 files have none).
        header: Header of the file (plate geometry).
    """

    index: int
    records: NDArray[np.void] = field(repr=False)
    mean_image: NDArray[np.uint16] = field(repr=False)
    label: str
    header: RecordingHeader = field(repr=False)

    @property
    def frame_count(self) -> int:
        """Number of frames."""
        return len(self.records)

    @property
    def pressure(self) -> NDArray[np.uint16]:
        """Pressure values, shape ``(frames, rows, columns)``, raw sensor units."""
        return self.records["pressure"]

    @property
    def cop_total(self) -> NDArray[np.float32]:
        """Centre of pressure of the whole plate, shape ``(frames, 2)`` as (x, y) cm."""
        return self.records["cop_total"]

    @property
    def cop_left(self) -> NDArray[np.float32]:
        """COP of the left foot (half with x < columns/2), shape ``(frames, 2)`` as (x, y) cm."""
        return self.records["cop_left"]

    @property
    def cop_right(self) -> NDArray[np.float32]:
        """COP of the right foot (half with x >= columns/2), shape ``(frames, 2)`` as (x, y) cm."""
        return self.records["cop_right"]

    @property
    def timestamps_ms(self) -> NDArray[np.int32]:
        """Absolute frame timestamps in ms (system tick count, arbitrary origin)."""
        return self.records["timestamp_ms"]

    @cached_property
    def time_s(self) -> NDArray[np.float64]:
        """Frame times in seconds relative to the first frame."""
        return (self.timestamps_ms.astype(np.int64) - int(self.timestamps_ms[0])) / 1000.0

    @property
    def duration_s(self) -> float:
        """Time between the first and the last frame in seconds."""
        return float(self.time_s[-1]) if self.frame_count else 0.0

    @property
    def sampling_rate_hz(self) -> float:
        """Mean sampling rate in Hz (25 Hz for current FreeSTEP versions)."""
        return (self.frame_count - 1) / self.duration_s if self.duration_s > 0 else 0.0

    def compute_cop(self, frame: int) -> tuple[float, float] | None:
        """Recompute the centre of pressure of a frame from its pressure data.

        Uses the same convention as FreeSTEP (cell index times cell size, without
        a half-cell offset), so the result matches :attr:`cop_total`.

        Args:
            frame: Zero-based frame index.

        Returns:
            ``(x, y)`` in cm, or ``None`` for a frame without any load.
        """
        p = self.pressure[frame].astype(np.float64)
        total = p.sum()
        if total == 0:
            return None
        ys, xs = np.indices(p.shape)
        return (
            float((p * xs).sum() / total * self.header.cell_width_mm / 10),
            float((p * ys).sum() / total * self.header.cell_height_mm / 10),
        )

    def path_length_mm(self) -> float:
        """Length of the COP trajectory in mm."""
        cop = self.cop_total.astype(np.float64) * 10
        return float(np.linalg.norm(np.diff(cop, axis=0), axis=1).sum())


@dataclass
class Recording:
    """A pressure recording of an exam.

    Attributes:
        header: The file header.
        acquisitions: The stored acquisitions in file order.
        raw: Complete file content (the arrays are views into it).
        data_end: Offset where the last acquisition ends; the rest of the file
            is preallocated, unused space.
        gait: A gait recording (dynamic or treadmill exam, one acquisition per
            pass, see :mod:`freestep.formats.walkway`); stabilometry and the load
            distribution of standing feet do not apply.
    """

    header: RecordingHeader
    acquisitions: list[Acquisition]
    raw: bytes = field(repr=False)
    data_end: int = 0
    gait: bool = False

    @property
    def unused_size(self) -> int:
        """Number of bytes after the last acquisition (preallocated, unused space)."""
        return len(self.raw) - self.data_end
