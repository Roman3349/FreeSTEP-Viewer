"""Parser of ``Dinamica.dat`` and ``TreadMILL.dat`` — gait recordings over several pressure plates in a row.

A dynamic exam records walks over a walkway of plates laid one after another
(``Dinamica.dat``), a treadmill exam walking on plates built into a treadmill
(``TreadMILL.dat``). Both files have the same layout (see ``docs/FORMAT.md``)::

    File header
    0       2     int16       format version (2 and 4 are known)
    2       50    char[50]    platform name, space padded ("Free4 40x80")
    52      2     int16       columns of one plate C
    54      2     int16       rows R
    56      2     int16       number of plates P
    58      2     int16       cell width in mm
    60      2     int16       cell height in mm
    62      2     int16       maximum sensor value (255)
    64      ...   int16[k]    settings of unknown meaning; the header has 90 bytes (v2) or 102 bytes (v4)
                              and ends with the last frame index of the first pass (int32)

    uint16[R][C*P]            summary image of the whole exam (all footprints), no trailer
    Frame (repeated in passes)
    0       2*R*C*P           uint16[R][C*P] pressure of all plates side by side
    +0      4     int32       timestamp in ms (system tick count)
    +4      2     int16       maximum value of the frame
    +6      16/20             unknown (v2: the COP in cells as float32 at +14 and +18)

The frames of one pass (walk over the plates) follow each other directly; between
passes there are blocks of not yet known content. The frames are therefore found
by their timestamps: a frame is accepted when its time and that of the next frame
continue the recording.
"""

from __future__ import annotations

import struct

import numpy as np
from numpy.typing import NDArray

from .postura import COMMON_HEADER_SIZE, Acquisition, Postura, PosturaHeader, frame_dtype

#: Size of the file header and of the frame trailer in bytes, by format version (2 and 4 are known).
WALKWAY_LAYOUT: dict[int, tuple[int, int]] = {2: (90, 22), 4: (102, 26)}
#: Longest time between two frames of a pass in ms.
MAX_FRAME_STEP_MS = 1000
#: Longest pause between two passes in ms.
MAX_PASS_PAUSE_MS = 10 * 60 * 1000
#: How far behind a pass the next one is looked for, in frames.
_SEARCH_FRAMES = 32

_WALKWAY_STRUCT = struct.Struct("<h50s6h")


def parse_walkway(data: bytes, name: str = "Dinamica.dat") -> Postura:
    """Parse ``Dinamica.dat`` or ``TreadMILL.dat`` as a recording with one acquisition per pass.

    The plates are joined into one wide plate. The COP of every frame is computed
    from the pressure like FreeSTEP's (cell index times cell size); there is no
    left and right COP. The result has :attr:`Postura.gait` set.

    Args:
        data: Complete (decrypted) file content.
        name: File name for error messages.

    Returns:
        The recording.

    Raises:
        ValueError: The header is invalid or no frames are found.
    """
    if len(data) < COMMON_HEADER_SIZE + 2:
        raise ValueError(f"{name} is too short for a header")
    version, platform, columns, rows, plates, cell_w, cell_h, max_value = _WALKWAY_STRUCT.unpack_from(data)
    if columns <= 0 or rows <= 0 or plates <= 0:
        raise ValueError(f"{name}: invalid plate size {columns}x{rows}, {plates} plates")
    size, trailer = WALKWAY_LAYOUT[2] if version <= 2 else WALKWAY_LAYOUT[4]
    width = columns * plates
    header = PosturaHeader(
        version=version,
        platform=platform.decode("cp1252").rstrip(" \x00"),
        columns=width,
        rows=rows,
        cell_width_mm=cell_w,
        cell_height_mm=cell_h,
        max_value=max_value,
        settings=struct.unpack_from(f"<{(size - COMMON_HEADER_SIZE - 2) // 2}h", data, COMMON_HEADER_SIZE + 2),
        size=size,
        plates=plates,
    )
    matrix = rows * width * 2
    passes = _find_passes(data, size + 2 * matrix, matrix, trailer)
    if not passes:
        raise ValueError(f"{name} contains no frames")
    raw_dtype = np.dtype(
        {
            "names": ["pressure", "timestamp_ms", "max"],
            "formats": [("<u2", (rows, width)), "<i4", "<i2"],
            "offsets": [0, matrix, matrix + 4],
            "itemsize": matrix + trailer,
        }
    )
    acquisitions: list[Acquisition] = []
    for first, count in passes:
        raw = np.frombuffer(data, dtype=raw_dtype, count=count, offset=first - matrix)
        records = np.zeros(count, dtype=frame_dtype(rows, width))
        records["pressure"] = raw["pressure"]
        records["timestamp_ms"] = raw["timestamp_ms"]
        records["unknown"] = raw["max"]
        records["cop_total"] = _cop(records["pressure"], cell_w, cell_h)
        records.flags.writeable = False
        acquisitions.append(
            Acquisition(
                index=len(acquisitions),
                records=records,
                mean_image=_mean_image(records["pressure"]),
                label="",
                header=header,
            )
        )
    last, count = passes[-1]
    end = last + (count - 1) * (matrix + trailer) + trailer
    return Postura(header=header, acquisitions=acquisitions, raw=data, data_end=end, gait=True)


def _find_passes(data: bytes, start: int, matrix: int, trailer: int) -> list[tuple[int, int]]:
    """Find the passes as ``(position of the first frame trailer, number of frames)``.

    Args:
        data: File content.
        start: Earliest position of the first trailer (behind the header, the summary image and one frame).
        matrix: Size of the pressure data of one frame in bytes.
        trailer: Size of the frame trailer in bytes.
    """
    stride = matrix + trailer
    passes: list[tuple[int, int]] = []
    previous: int | None = None
    while (first := _find_pass(data, start, stride, previous)) is not None:
        position, time, count = first, _time(data, first), 1
        while position + stride + 4 <= len(data):
            following = _time(data, position + stride)
            if not 0 <= following - time <= MAX_FRAME_STEP_MS:
                break
            position, time, count = position + stride, following, count + 1
        passes.append((first, count))
        previous = time
        start = position + trailer + matrix
    return passes


def _find_pass(data: bytes, start: int, stride: int, previous: int | None) -> int | None:
    """Find the first trailer at or after ``start`` that begins a pass, ``None`` if there is none.

    A pass begins where the time continues the previous pass (or is positive for
    the first one) and the time of the next frame continues it.
    """
    stop = min(len(data), start + (_SEARCH_FRAMES + 1) * stride + 4)
    if stop - start < stride + 4:
        return None
    halves = np.frombuffer(data, dtype="<u2", count=(stop - start) // 2, offset=start).astype(np.int64)
    times = halves[:-1] + (halves[1:].astype(np.int16).astype(np.int64) << 16)  # int32 at every even offset
    step = stride // 2
    current, following = times[:-step], times[step:]
    valid = (following - current >= 0) & (following - current <= MAX_FRAME_STEP_MS)
    if previous is None:
        valid &= current > 0
    else:
        valid &= (current > previous) & (current - previous <= MAX_PASS_PAUSE_MS)
    hits = np.flatnonzero(valid)
    return start + 2 * int(hits[0]) if len(hits) else None


def _time(data: bytes, position: int) -> int:
    """Timestamp of the trailer at ``position``."""
    return int(struct.unpack_from("<i", data, position)[0])


def _cop(pressure: NDArray[np.uint16], cell_w: int, cell_h: int) -> NDArray[np.float64]:
    """COP of every frame in cm (cell index times cell size); (0, 0) for frames without load."""
    total = pressure.sum(axis=(1, 2), dtype=np.int64).astype(np.float64)
    x = pressure.sum(axis=1, dtype=np.int64) @ np.arange(pressure.shape[2])
    y = pressure.sum(axis=2, dtype=np.int64) @ np.arange(pressure.shape[1])
    loaded = np.maximum(total, 1)
    cop = np.column_stack([x / loaded * cell_w / 10, y / loaded * cell_h / 10])
    cop[total == 0] = 0
    return cop


def _mean_image(pressure: NDArray[np.uint16]) -> NDArray[np.uint16]:
    """Mean of every cell over the frames where it is loaded."""
    sums = pressure.sum(axis=0, dtype=np.int64)
    counts = np.count_nonzero(pressure, axis=0)
    return np.where(counts > 0, sums // np.maximum(counts, 1), 0).astype(np.uint16)
