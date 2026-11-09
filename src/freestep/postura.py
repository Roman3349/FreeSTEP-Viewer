"""Parser of ``Postura.dat`` — the pressure-plate recording of a posturography exam.

The file was written by a VB6 application, so: little-endian, ``Integer`` = int16,
``Long`` = int32, ``Single`` = float32, ``True`` = -1, fixed-length strings padded
with spaces, arrays include their upper bound (``Dim a(N)`` has N + 1 items).

One file holds several acquisitions (e.g. eyes open / eyes closed of a Romberg
test). Layout (reverse engineered, see ``docs/FORMAT.md``)::

    File header
    0       2     int16       format version (1 or 3)
    2       50    char[50]    platform name, space padded ("Max 50x60")
    52      2     int16       columns (X)
    54      2     int16       rows (Y)
    56      2     int16       cell width in mm
    58      2     int16       cell height in mm
    60      2     int16       maximum sensor value (255)
    62      ...   int16[k]    version specific settings (v1: 2 values, v3: 6 values)

    Acquisition (repeated)
    0       2     int16       flag, -1 (VB True) for a stored acquisition
    2       4     int32       last frame index N (the acquisition has N + 1 frames)
    6       ...   Frame[N+1]  frames
    ...     2*R*C uint16[R][C] mean of every cell over the frames where it is loaded
    ...     L     char[L]     label, space padded (v3: 100 characters, v1: none)

    Frame (2*R*C + 30 bytes)
    0       2*R*C uint16[R][C] pressure, row-major (row = Y, column = X)
    +0      4     int32       timestamp in ms (system tick count)
    +4      2     int16       unknown (~240..252)
    +6      8     float32[2]  COP of the whole plate (x, y) in cm
    +14     8     float32[2]  COP of the left foot = half with x < columns/2 (x, y) in cm
    +22     8     float32[2]  COP of the right foot = half with x >= columns/2 (x, y) in cm

COP is computed as ``sum(p * index) / sum(p) * cell_size`` (no half-cell offset).
A rotated plate may swap left and right.
The file may be followed by unused, preallocated space.

``Statica.dat`` of a static exam holds a single frame without trailer: the common
header, 7 int16 settings, the descriptor of the VB dynamic array holding the frame
(int16 dimensions = 2, then per dimension int32 count and int32 lower bound; 94
bytes in all), then ``uint16[R][C]`` pressure; the rest of the file (26 zero bytes
in the known sample) is the rest of the record (see :meth:`Postura.parse_static`).
The frame matches the footprint ``Impronta.bpe`` (4 bytes header, ``uint8[R][C]``)
of the same exam.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
from numpy.typing import NDArray

#: Size of the common part of the file header (up to the maximum value) in bytes.
COMMON_HEADER_SIZE = 62
#: Number of int16 settings after the common header of ``Statica.dat``.
STATIC_SETTINGS_COUNT = 7
#: Size of the per-frame trailer (timestamp, unknown int16, 3 COP points) in bytes.
FRAME_TRAILER_SIZE = 30
#: Size of the acquisition header (flag + last frame index) in bytes.
ACQUISITION_HEADER_SIZE = 6

#: Number of version specific int16 settings after the common header, by version.
SETTINGS_COUNT: dict[int, int] = {1: 2, 3: 6}
#: Length of the acquisition label (``String * L``), by version.
LABEL_SIZE: dict[int, int] = {1: 0, 3: 100}

# Common header fields (see the module docstring).
_COMMON_STRUCT = struct.Struct("<h50s5h")
# Descriptor of a two-dimensional VB dynamic array: dimensions, then count and lower bound per dimension
_STATIC_ARRAY_STRUCT = struct.Struct("<h4i")
_ACQUISITION_STRUCT = struct.Struct("<hi")
# Settings counts tried for unknown versions.
_MAX_SETTINGS = 16


@dataclass(frozen=True)
class PosturaHeader:
    """File header of ``Postura.dat``.

    Attributes:
        version: Format version (1 and 3 are known).
        platform: Name of the pressure plate, e.g. ``"Max 50x60"``.
        columns: Number of sensor columns (X axis).
        rows: Number of sensor rows (Y axis).
        cell_width_mm: Width of one sensor cell in mm.
        cell_height_mm: Height of one sensor cell in mm.
        max_value: Maximum raw sensor value (255 for 8-bit plates).
        settings: Version specific int16 values of not yet known meaning
            (v3: ``(-1, 512, 0, 0, 1, 1)``, the 512 differs between exams).
        size: Size of the header in bytes (v1: 66, v3: 74).
        plates: Number of plates in a row joined into the columns (gait recordings, see :mod:`freestep.walkway`).
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
    def frame_size(self) -> int:
        """Size of one frame in bytes (pressure data + trailer)."""
        return self.matrix_size + FRAME_TRAILER_SIZE

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

    @classmethod
    def parse(cls, data: bytes) -> PosturaHeader:
        """Parse the header.

        For unknown versions the number of settings is detected by looking for a
        valid acquisition header behind them.

        Args:
            data: Content of ``Postura.dat``.

        Returns:
            The parsed header.

        Raises:
            ValueError: The data are too short, the plate size is invalid or no
                acquisition follows the header.
        """
        if len(data) < COMMON_HEADER_SIZE + ACQUISITION_HEADER_SIZE:
            raise ValueError("Postura.dat is too short for a header")
        version, name, columns, rows, cell_w, cell_h, max_value = _COMMON_STRUCT.unpack_from(data)
        if columns <= 0 or rows <= 0:
            raise ValueError(f"Invalid plate size {columns}x{rows}")
        frame_size = rows * columns * 2 + FRAME_TRAILER_SIZE
        candidates = [SETTINGS_COUNT[version]] if version in SETTINGS_COUNT else range(_MAX_SETTINGS + 1)
        for count in candidates:
            size = COMMON_HEADER_SIZE + 2 * count
            if _acquisition_at(data, size, frame_size) is not None:
                settings = struct.unpack_from(f"<{count}h", data, COMMON_HEADER_SIZE)
                return cls(
                    version=version,
                    platform=name.decode("cp1252").rstrip(" \x00"),
                    columns=columns,
                    rows=rows,
                    cell_width_mm=cell_w,
                    cell_height_mm=cell_h,
                    max_value=max_value,
                    settings=tuple(settings),
                    size=size,
                )
        raise ValueError(f"No acquisition found after the header (format version {version})")


def _acquisition_at(data: bytes, offset: int, frame_size: int) -> int | None:
    """Return the frame count of an acquisition starting at ``offset``, or ``None`` if there is none.

    Args:
        data: File content.
        offset: Position of the acquisition header.
        frame_size: Size of one frame in bytes.
    """
    if offset + ACQUISITION_HEADER_SIZE > len(data):
        return None
    flag, last_index = _ACQUISITION_STRUCT.unpack_from(data, offset)
    count = int(last_index) + 1
    if flag != -1 or count <= 0 or offset + ACQUISITION_HEADER_SIZE + count * frame_size > len(data):
        return None
    return count


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
    """One acquisition (continuous recording) of a posturography exam.

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
    header: PosturaHeader = field(repr=False)

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
class Postura:
    """Parsed ``Postura.dat``.

    Attributes:
        header: The file header.
        acquisitions: The stored acquisitions in file order.
        raw: Complete file content (the arrays are views into it).
        data_end: Offset where the last acquisition ends; the rest of the file
            is preallocated, unused space.
        gait: A gait recording (dynamic or treadmill exam, one acquisition per
            pass, see :mod:`freestep.walkway`); stabilometry and the load
            distribution of standing feet do not apply.
    """

    header: PosturaHeader
    acquisitions: list[Acquisition]
    raw: bytes = field(repr=False)
    data_end: int = 0
    gait: bool = False

    @classmethod
    def parse(cls, data: bytes) -> Postura:
        """Parse ``Postura.dat``.

        Args:
            data: Complete (decrypted) file content.

        Returns:
            The recording with all acquisitions.

        Raises:
            ValueError: The header is invalid or the file contains no acquisition.
        """
        header = PosturaHeader.parse(data)
        dtype = frame_dtype(header.rows, header.columns)
        label_size = LABEL_SIZE.get(header.version)
        acquisitions: list[Acquisition] = []
        offset = header.size
        while (count := _acquisition_at(data, offset, header.frame_size)) is not None:
            start = offset + ACQUISITION_HEADER_SIZE
            mean_offset = start + count * header.frame_size
            if mean_offset + header.matrix_size > len(data):
                break
            if label_size is None:  # unknown version: detect from the next acquisition
                label_size = next(
                    (
                        size
                        for size in (0, 100, 50, 255)
                        if _acquisition_at(data, mean_offset + header.matrix_size + size, header.frame_size)
                    ),
                    0,
                )
            label_offset = mean_offset + header.matrix_size
            label = data[label_offset : label_offset + label_size].decode("cp1252").rstrip(" \x00")
            acquisitions.append(
                Acquisition(
                    index=len(acquisitions),
                    records=np.frombuffer(data, dtype=dtype, count=count, offset=start),
                    mean_image=np.frombuffer(
                        data, dtype="<u2", count=header.rows * header.columns, offset=mean_offset
                    ).reshape(header.rows, header.columns),
                    label=label,
                    header=header,
                )
            )
            offset = label_offset + label_size
        if not acquisitions:
            raise ValueError("Postura.dat contains no acquisition")
        return cls(header=header, acquisitions=acquisitions, raw=data, data_end=offset)

    @classmethod
    def parse_static(cls, data: bytes) -> Postura:
        """Parse ``Statica.dat`` of a static exam as a recording with one acquisition of one frame.

        The frame has no timestamp; the COPs are computed like FreeSTEP's (halves at ``columns / 2``).

        Args:
            data: Complete (decrypted) file content.

        Returns:
            The recording.

        Raises:
            ValueError: The data are too short or the plate size is invalid.
        """
        descriptor = COMMON_HEADER_SIZE + 2 * STATIC_SETTINGS_COUNT
        size = descriptor + _STATIC_ARRAY_STRUCT.size
        if len(data) < size:
            raise ValueError("Statica.dat is too short for a header")
        version, name, columns, rows, cell_w, cell_h, max_value = _COMMON_STRUCT.unpack_from(data)
        dims, count_1, _, count_2, _ = _STATIC_ARRAY_STRUCT.unpack_from(data, descriptor)
        if dims != 2 or count_1 * count_2 != rows * columns:
            raise ValueError(f"Statica.dat has no {columns}x{rows} array descriptor")
        if columns <= 0 or rows <= 0 or len(data) < size + rows * columns * 2:
            raise ValueError(f"Statica.dat does not hold one {columns}x{rows} frame")
        header = PosturaHeader(
            version=version,
            platform=name.decode("cp1252").rstrip(" \x00"),
            columns=columns,
            rows=rows,
            cell_width_mm=cell_w,
            cell_height_mm=cell_h,
            max_value=max_value,
            settings=struct.unpack_from(f"<{STATIC_SETTINGS_COUNT}h", data, COMMON_HEADER_SIZE),
            size=size,
        )
        pressure = np.frombuffer(data, dtype="<u2", count=rows * columns, offset=header.size).reshape(rows, columns)
        records = np.zeros(1, dtype=frame_dtype(rows, columns))
        records["pressure"][0] = pressure
        half = columns // 2
        for name_, part, first in (
            ("cop_total", pressure, 0),
            ("cop_left", pressure[:, :half], 0),
            ("cop_right", pressure[:, half:], half),
        ):
            load = part.astype(np.float64)
            if load.sum() > 0:
                ys, xs = np.indices(load.shape)
                records[name_][0] = (
                    ((load * (xs + first)).sum() / load.sum()) * cell_w / 10,
                    (load * ys).sum() / load.sum() * cell_h / 10,
                )
        acquisition = Acquisition(index=0, records=records, mean_image=pressure, label="", header=header)
        return cls(header=header, acquisitions=[acquisition], raw=data, data_end=len(data))

    @property
    def unused_size(self) -> int:
        """Number of bytes after the last acquisition (preallocated, unused space)."""
        return len(self.raw) - self.data_end
