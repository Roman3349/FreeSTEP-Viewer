"""Tests of the stabilometric parameters, the Romberg evaluation and the MATLAB / CSV writers."""

import csv
import math
import struct
from pathlib import Path

import numpy as np
import pytest
from scipy.io import loadmat
from test_units import _synthetic_postura

from freestep.postura import Postura
from freestep.stabilometry import (
    POSTUROGRAPHY_EXAM_TYPE,
    analyze,
    analyze_all,
    compute,
    detect_romberg,
    evaluate_romberg,
    load_distribution,
    load_regions,
    speed,
    unit,
)
from freestep.writers import write_mat, write_stabilometry_csv

FS = 25.0


def _circle(radius_mm: float, frequency_hz: float, seconds: float = 40.0) -> np.ndarray:
    """COP moving on a circle around (100, 200) mm with constant speed."""
    t = np.arange(round(seconds * FS)) / FS
    phase = 2 * np.pi * frequency_hz * t
    return np.column_stack([100 + radius_mm * np.cos(phase), 200 + radius_mm * np.sin(phase)])


def test_circle_parameters() -> None:
    """Spread, path, velocity, areas and frequency of a circular trajectory match the analytic values."""
    radius, frequency = 4.0, 0.5
    cop = _circle(radius, frequency)
    result = compute(cop, FS, lowpass_hz=None)
    duration = (len(cop) - 1) / FS
    assert result.lowpass_hz is None
    assert (result.mean_x_mm, result.mean_y_mm) == pytest.approx((100, 200), abs=1e-6)
    assert result.sd_ml_mm == pytest.approx(radius / math.sqrt(2), rel=1e-2)
    assert result.mean_distance_mm == pytest.approx(radius)
    assert result.range_ml_mm == pytest.approx(2 * radius, rel=1e-3)
    assert result.path_length_mm == pytest.approx(2 * np.pi * radius * frequency * duration, rel=1e-3)
    assert result.mean_velocity_mm_s == pytest.approx(2 * np.pi * radius * frequency, rel=1e-3)
    # covariance r²/2 on both axes; chi-square(2) 95 % quantile = -2 ln 0.05
    assert result.ellipse_area_mm2 == pytest.approx(math.pi * -2 * math.log(0.05) * radius**2 / 2, rel=1e-2)
    assert result.convex_hull_area_mm2 == pytest.approx(math.pi * radius**2, rel=1e-2)
    assert result.sway_area_mm2_s == pytest.approx(math.pi * radius**2 * frequency, rel=1e-2)
    assert result.median_frequency_ml_hz == pytest.approx(frequency, abs=0.05)
    assert result.mean_frequency_ap_hz == pytest.approx(frequency, abs=0.1)


def test_speed_curve() -> None:
    """The speed curve of a circle is constant and its mean is the mean velocity."""
    cop = _circle(4.0, 0.5)
    curve = speed(cop, FS)
    assert curve.shape == (len(cop) - 1,)
    assert curve == pytest.approx(np.full(len(curve), 2 * np.pi * 4.0 * 0.5), rel=1e-2)
    assert curve.mean() == pytest.approx(compute(cop, FS, lowpass_hz=None).mean_velocity_mm_s)


def test_lowpass_removes_noise() -> None:
    """The low-pass filter removes high-frequency noise, which inflates the path length."""
    rng = np.random.default_rng(1)
    cop = _circle(4.0, 0.3) + rng.normal(0, 0.3, (1000, 2))
    raw = compute(cop, FS, lowpass_hz=None)
    filtered = compute(cop, FS, lowpass_hz=2.0)
    clean = compute(_circle(4.0, 0.3), FS, lowpass_hz=None)
    assert filtered.lowpass_hz == 2.0
    assert raw.path_length_mm > 1.5 * clean.path_length_mm
    assert abs(filtered.path_length_mm - clean.path_length_mm) < 0.3 * clean.path_length_mm
    # A cut-off at or above the Nyquist frequency disables the filter
    assert compute(cop, FS, lowpass_hz=13.0).lowpass_hz is None


def test_detrend_removes_drift() -> None:
    """With detrending a linear drift does not inflate the spread and the ellipse."""
    cop = _circle(2.0, 0.5)
    drifted = cop + np.linspace(0, 20, len(cop))[:, None] * [1.0, -0.5]
    plain = compute(drifted, FS, lowpass_hz=None)
    detrended = compute(drifted, FS, lowpass_hz=None, detrend=True)
    assert detrended.detrended and not plain.detrended
    assert detrended.sd_ml_mm == pytest.approx(2.0 / math.sqrt(2), rel=0.02)
    assert plain.sd_ml_mm > 2 * detrended.sd_ml_mm
    assert detrended.ellipse_area_mm2 == pytest.approx(compute(cop, FS, lowpass_hz=None).ellipse_area_mm2, rel=0.05)
    assert detrended.mean_x_mm == pytest.approx(plain.mean_x_mm)  # the position on the plate is kept


def test_load_distribution() -> None:
    """Shares of the feet and of their front and rear halves (toes toward row 0)."""
    frame = np.zeros((4, 4), np.uint16)
    frame[:, 0] = [1, 1, 2, 2]  # left foot over rows 0-3: front 2, rear 4
    frame[0:2, 2] = 3  # right foot over rows 0-1: front 3, rear 3
    shares = load_distribution(np.stack([frame, frame, np.zeros_like(frame)]))
    assert shares["left_load_pct"] == pytest.approx(50)
    assert shares["load_asymmetry_pct"] == pytest.approx(0)
    assert shares["left_load_sd_pct"] == pytest.approx(0)
    assert shares["left_forefoot_pct"] == pytest.approx(100 / 3)
    assert shares["right_forefoot_pct"] == pytest.approx(50)
    assert shares["forefoot_pct"] == pytest.approx(5 / 12 * 100)
    quadrants = [shares["left_front_pct"], shares["left_rear_pct"], shares["right_front_pct"], shares["right_rear_pct"]]
    assert quadrants == pytest.approx([2 / 12 * 100, 4 / 12 * 100, 25, 25])
    assert load_distribution(np.zeros((2, 4, 4), np.uint16)) == {}


def test_load_regions_off_centre() -> None:
    """The feet are split in the gap between them, also when the patient does not stand in the centre."""
    frame = np.zeros((4, 10), np.uint16)
    frame[:, 0:2] = 1  # left foot
    frame[:, 4] = 2  # right foot, left of the middle of the plate (column 5)
    regions = load_regions(frame)
    assert 2 <= regions.split_column <= 4
    assert regions.left is not None and regions.right is not None
    assert regions.left.cop == pytest.approx((0.5, 1.5))
    assert regions.right.cop == pytest.approx((4, 1.5))
    assert (regions.right.first_row, regions.right.last_row, regions.right.middle_row) == (0, 3, 2)
    assert load_distribution(frame[None])["left_load_pct"] == pytest.approx(50)
    # Touching feet: split at the least loaded column
    frame = np.array([[3, 3, 1, 3, 3]] * 2, np.uint16)
    assert load_regions(frame).split_column == 3


def test_too_short_is_rejected() -> None:
    """Too few frames cannot be analysed."""
    with pytest.raises(ValueError):
        compute(_circle(1.0, 1.0, seconds=1.0), FS)


def _postura(labels: tuple[str, str] = ("", ""), frames: int = 64) -> Postura:
    """Synthetic recording with two acquisitions; the first has unloaded frames at the start and inside."""
    data = bytearray(_synthetic_postura(3, [frames, frames]))
    header, frame_size = 74, 4 * 6 * 2 + 30
    for index in (0, 1, 10):  # clear the load (cell 1, 2) of three frames of the first acquisition
        struct.pack_into("<H", data, header + 6 + index * frame_size + 2 * (1 * 6 + 2), 0)
    label_offsets = [header + 6 + frames * frame_size + 48, 0]
    label_offsets[1] = label_offsets[0] + 100 + 6 + frames * frame_size + 48
    for offset, label in zip(label_offsets, labels, strict=True):
        data[offset : offset + 100] = label.ljust(100).encode()
    return Postura.parse(bytes(data))


def test_unloaded_frames_are_trimmed_and_interpolated() -> None:
    """Unloaded frames at the start are left out, those inside are interpolated."""
    post = _postura()
    result = analyze(post.acquisitions[0])
    assert result.frames == 62
    assert result.interpolated_frames == 1
    assert result.left_load_pct == 100 and result.load_asymmetry_pct == -100  # one loaded cell on the left
    assert result.left_forefoot_pct == pytest.approx(50) and result.right_forefoot_pct is None
    # The synthetic COP does not move: no spread, no area, no spectrum
    assert result.path_length_mm == pytest.approx(0, abs=1e-9)
    assert result.convex_hull_area_mm2 == 0
    assert math.isnan(result.mean_frequency_ml_hz)


@pytest.mark.parametrize(
    ("labels", "closed_first", "expected"),
    [
        (("", ""), False, (0, 1, False)),
        (("", ""), True, (1, 0, False)),
        (("Occhi chiusi", "Occhi aperti"), False, (1, 0, True)),
        (("EO", "EC"), True, (0, 1, True)),
    ],
)
def test_romberg_detection(labels: tuple[str, str], closed_first: bool, expected: tuple[int, int, bool]) -> None:
    """The eye condition comes from the labels, otherwise from the given order."""
    post = _postura(labels)
    assert detect_romberg(post, POSTUROGRAPHY_EXAM_TYPE, closed_first) == expected
    assert detect_romberg(post, 0) is None  # not a posturography exam


def test_romberg_quotients() -> None:
    """The quotients compare eyes closed with eyes open."""
    post = _postura()
    small, large = compute(_circle(2.0, 0.5), FS), compute(_circle(4.0, 0.5), FS)
    romberg = evaluate_romberg(post, [small, large], POSTUROGRAPHY_EXAM_TYPE)
    assert romberg is not None
    assert romberg.quotients["path_length_mm"] == pytest.approx(2, rel=1e-2)
    assert romberg.quotients["ellipse_area_mm2"] == pytest.approx(4, rel=1e-2)
    assert evaluate_romberg(post, [small, None]) is None


def test_write_mat(tmp_path: Path) -> None:
    """The MATLAB file holds the frames (rows × columns × frames), COP, parameters and the Romberg result."""
    post = _postura()
    results = analyze_all(post)
    romberg = evaluate_romberg(post, results, POSTUROGRAPHY_EXAM_TYPE)
    path = tmp_path / "exam.mat"
    write_mat(post, path, results, romberg)
    mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    assert mat["platform"] == "Test"
    assert list(mat["cell_size_mm"]) == [5, 5]
    first = mat["acq"][0]
    assert first.pressure.shape == (4, 6, 64)
    assert first.pressure[1, 2, 5] == post.acquisitions[0].pressure[5, 1, 2]
    assert first.time_s.shape == (64,)
    assert first.cop_total.shape == (64, 2)
    assert first.stabilometry.frames == 62
    assert mat["romberg"].eyes_open == 1 and mat["romberg"].eyes_closed == 2


def test_write_stabilometry_csv(tmp_path: Path) -> None:
    """One row per parameter with its unit, one column per acquisition."""
    results = [compute(_circle(2.0, 0.5), FS), None]
    path = tmp_path / "stats.csv"
    write_stabilometry_csv(results, path)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = {row["parameter"]: row for row in csv.DictReader(handle)}
    assert rows["path_length_mm"]["unit"] == unit("path_length_mm") == "mm"
    assert rows["sway_area_mm2_s"]["unit"] == "mm²/s"
    assert float(rows["mean_distance_mm"]["acquisition_1"]) == pytest.approx(2.0, rel=1e-2)
    assert rows["frames"]["acquisition_2"] == ""
