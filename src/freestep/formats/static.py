"""Parser of ``Statica.dat`` — the single pressure frame of a static exam.

``Statica.dat`` of a static exam holds a single frame without trailer: the common
header, 7 int16 settings, the descriptor of the VB dynamic array holding the frame
(int16 dimensions = 2, then per dimension int32 count and int32 lower bound; 94
bytes in all), then ``uint16[R][C]`` pressure; the rest of the file (26 zero bytes
in the known sample) is the rest of the record.
The frame matches the footprint ``Impronta.bpe`` (4 bytes header, ``uint8[R][C]``)
of the same exam.
"""

from __future__ import annotations

import struct

import numpy as np

from ..recording import Acquisition, Recording, RecordingHeader, frame_dtype
from .common import COMMON_HEADER_SIZE, COMMON_STRUCT, decode_name

#: Number of int16 settings after the common header of ``Statica.dat``.
STATIC_SETTINGS_COUNT = 7

# Descriptor of a two-dimensional VB dynamic array: dimensions, then count and lower bound per dimension
_ARRAY_STRUCT = struct.Struct("<h4i")


def parse_static(data: bytes) -> Recording:
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
    size = descriptor + _ARRAY_STRUCT.size
    if len(data) < size:
        raise ValueError("Statica.dat is too short for a header")
    version, name, columns, rows, cell_w, cell_h, max_value = COMMON_STRUCT.unpack_from(data)
    dims, count_1, _, count_2, _ = _ARRAY_STRUCT.unpack_from(data, descriptor)
    if dims != 2 or count_1 * count_2 != rows * columns:
        raise ValueError(f"Statica.dat has no {columns}x{rows} array descriptor")
    if columns <= 0 or rows <= 0 or len(data) < size + rows * columns * 2:
        raise ValueError(f"Statica.dat does not hold one {columns}x{rows} frame")
    header = RecordingHeader(
        version=version,
        platform=decode_name(name),
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
    return Recording(header=header, acquisitions=[acquisition], raw=data, data_end=len(data))
