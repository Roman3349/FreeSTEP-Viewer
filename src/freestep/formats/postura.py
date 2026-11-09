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
"""

from __future__ import annotations

import struct

import numpy as np

from ..recording import Acquisition, Recording, RecordingHeader, frame_dtype
from .common import COMMON_HEADER_SIZE, COMMON_STRUCT, decode_name

#: Size of the per-frame trailer (timestamp, unknown int16, 3 COP points) in bytes.
FRAME_TRAILER_SIZE = 30
#: Size of the acquisition header (flag + last frame index) in bytes.
ACQUISITION_HEADER_SIZE = 6
#: Number of version specific int16 settings after the common header, by version.
SETTINGS_COUNT: dict[int, int] = {1: 2, 3: 6}
#: Length of the acquisition label (``String * L``), by version.
LABEL_SIZE: dict[int, int] = {1: 0, 3: 100}

_ACQUISITION_STRUCT = struct.Struct("<hi")
# Settings counts tried for unknown versions.
_MAX_SETTINGS = 16


def parse_postura(data: bytes) -> Recording:
    """Parse ``Postura.dat``.

    Args:
        data: Complete (decrypted) file content.

    Returns:
        The recording with all acquisitions.

    Raises:
        ValueError: The header is invalid or the file contains no acquisition.
    """
    header = parse_header(data)
    dtype = frame_dtype(header.rows, header.columns)
    frame_size = header.matrix_size + FRAME_TRAILER_SIZE
    label_size = LABEL_SIZE.get(header.version)
    acquisitions: list[Acquisition] = []
    offset = header.size
    while (count := _acquisition_at(data, offset, frame_size)) is not None:
        start = offset + ACQUISITION_HEADER_SIZE
        mean_offset = start + count * frame_size
        if mean_offset + header.matrix_size > len(data):
            break
        if label_size is None:  # unknown version: detect from the next acquisition
            label_size = next(
                (
                    size
                    for size in (0, 100, 50, 255)
                    if _acquisition_at(data, mean_offset + header.matrix_size + size, frame_size)
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
    return Recording(header=header, acquisitions=acquisitions, raw=data, data_end=offset)


def parse_header(data: bytes) -> RecordingHeader:
    """Parse the header of ``Postura.dat``.

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
    version, name, columns, rows, cell_w, cell_h, max_value = COMMON_STRUCT.unpack_from(data)
    if columns <= 0 or rows <= 0:
        raise ValueError(f"Invalid plate size {columns}x{rows}")
    frame_size = rows * columns * 2 + FRAME_TRAILER_SIZE
    candidates = [SETTINGS_COUNT[version]] if version in SETTINGS_COUNT else range(_MAX_SETTINGS + 1)
    for count in candidates:
        size = COMMON_HEADER_SIZE + 2 * count
        if _acquisition_at(data, size, frame_size) is not None:
            settings = struct.unpack_from(f"<{count}h", data, COMMON_HEADER_SIZE)
            return RecordingHeader(
                version=version,
                platform=decode_name(name),
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
