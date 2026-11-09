"""Tests of the gait recordings over several plates (``Dinamica.dat``, ``TreadMILL.dat``) with synthetic data."""

import struct

import numpy as np
import pytest
from test_units import _synthetic_fre

from freestep.analysis.stabilometry import analyze_all
from freestep.archive import FreFile
from freestep.formats.walkway import WALKWAY_LAYOUT, parse_walkway

ROWS, COLUMNS, PLATES = 4, 3, 2


def _frame(index: int) -> np.ndarray:
    """A footprint of two cells moving over the plates."""
    frame = np.zeros((ROWS, COLUMNS * PLATES), np.uint16)
    frame[index % ROWS, index % (COLUMNS * PLATES)] = 10 + index
    frame[(index + 1) % ROWS, 0] = 5
    return frame


def _synthetic_walkway(version: int, passes: list[list[int]]) -> bytes:
    """Build a recording: header, summary image, then the passes with frames at the given times and junk between."""
    header_size, trailer_size = WALKWAY_LAYOUT[version]
    header = struct.pack("<h50s6h", version, b"Walk".ljust(50), COLUMNS, ROWS, PLATES, 5, 5, 255)
    data = bytearray(header.ljust(header_size, b"\0"))
    data += np.full((ROWS, COLUMNS * PLATES), 7, "<u2").tobytes()  # summary image
    index = 0
    for number, times in enumerate(passes):
        if number:  # block between passes with a fake small time and a frame count
            data += struct.pack("<hi", 1, 270) + bytes(50) + struct.pack("<i", len(times) - 1) + bytes(32)
        for time in times:
            trailer = struct.pack("<ih", time, int(_frame(index).max())).ljust(trailer_size, b"\0")
            data += _frame(index).astype("<u2").tobytes() + trailer
            index += 1
    return bytes(data) + bytes(100)  # preallocated, unused space


@pytest.mark.parametrize("version", [2, 4])
def test_walkway_passes(version: int) -> None:
    """The plates are joined, every pass becomes an acquisition and the junk between passes is skipped."""
    passes = [[1000 + 6 * i for i in range(5)], [9000 + 6 * i for i in range(7)]]
    post = parse_walkway(_synthetic_walkway(version, passes))
    assert post.gait
    assert (post.header.columns, post.header.rows, post.header.size) == (
        COLUMNS * PLATES,
        ROWS,
        WALKWAY_LAYOUT[version][0],
    )
    assert post.unused_size == 100
    assert [a.frame_count for a in post.acquisitions] == [5, 7]
    assert list(post.acquisitions[1].timestamps_ms) == passes[1]
    second = post.acquisitions[1]
    assert np.array_equal(second.pressure[2], _frame(7))
    assert second.cop_total[2] == pytest.approx(second.compute_cop(2))
    assert not second.cop_left.any() and not second.cop_right.any()
    assert second.mean_image[0, 0] == 5  # loaded in some frames only: mean over those
    assert analyze_all(post) == [None, None]  # no stabilometry for gait


def test_walkway_in_fre() -> None:
    """Dynamic and treadmill exams are read from their recording."""
    data = _synthetic_walkway(4, [[500 + 5 * i for i in range(3)]])
    for name in ("Dinamica.dat", "TreadMILL.dat"):
        with FreFile(_synthetic_fre({name: data, "Old.dat": b"junk"})) as fre:
            assert fre.has_pressure
            assert fre.recording.gait and fre.recording.acquisitions[0].frame_count == 3


def test_walkway_without_frames() -> None:
    """A recording without frames is rejected."""
    with pytest.raises(ValueError, match="no frames"):
        parse_walkway(_synthetic_walkway(2, [])[:-100] + bytes(1000))
