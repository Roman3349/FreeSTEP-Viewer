"""Stabilometric parameters of the centre of pressure and evaluation of the Romberg test.

The COP of the whole plate is used, x is the medio-lateral (ML) and y the
antero-posterior (AP) direction. All parameters are computed after removing the
mean position (optionally the linear trend); lengths are in mm, areas in mm²,
frequencies in Hz. Definitions follow Prieto et al., *Measures of postural
steadiness*, IEEE Trans. Biomed. Eng. 43 (1996) and Duarte & Freitas, *Revision
of posturography based on force plate for balance evaluation*, Rev. Bras.
Fisioter. 14 (2010).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np
from numpy.typing import NDArray
from scipy import signal
from scipy.spatial import ConvexHull, QhullError

from .postura import Acquisition, Postura

#: Default cut-off of the zero-phase low-pass filter in Hz (4th order Butterworth).
DEFAULT_LOWPASS_HZ = 5.0
#: Frequency band of the spectral parameters in Hz (Prieto et al.).
FREQUENCY_BAND_HZ = (0.15, 5.0)
#: Coverage of the confidence ellipse.
ELLIPSE_PROBABILITY = 0.95
#: Minimum number of loaded frames needed for the analysis.
MIN_FRAMES = 32
#: Exam type code of posturography (see :data:`freestep.models.EXAM_TYPES`).
POSTUROGRAPHY_EXAM_TYPE = 4
#: Maximum relative difference of the durations of the two Romberg acquisitions.
ROMBERG_DURATION_TOLERANCE = 0.1

# Words in acquisition labels telling the eye condition (English, Italian, Czech).
_OPEN_WORDS = ("open", "aperti", "otevř", "otevr", "eo", "oa")
_CLOSED_WORDS = ("closed", "chiusi", "zavř", "zavr", "ec", "oc")


@dataclass(frozen=True)
class Stabilometry:
    """Stabilometric parameters of one acquisition.

    Attributes:
        frames: Number of analysed frames (unloaded frames at the start and end are left out).
        interpolated_frames: Unloaded frames inside the recording, linearly interpolated.
        duration_s: Analysed duration.
        sampling_rate_hz: Sampling rate.
        lowpass_hz: Cut-off of the low-pass filter, ``None`` when not filtered.
        mean_x_mm: Mean ML position on the plate.
        mean_y_mm: Mean AP position on the plate.
        range_ml_mm: Range (max − min) in ML.
        range_ap_mm: Range in AP.
        sd_ml_mm: Standard deviation in ML.
        sd_ap_mm: Standard deviation in AP.
        mean_distance_mm: Mean distance from the mean position.
        rms_distance_mm: RMS distance from the mean position.
        path_length_mm: Length of the trajectory.
        path_length_ml_mm: Length of the ML component of the trajectory.
        path_length_ap_mm: Length of the AP component of the trajectory.
        mean_velocity_mm_s: Mean velocity (path length / duration).
        mean_velocity_ml_mm_s: Mean ML velocity.
        mean_velocity_ap_mm_s: Mean AP velocity.
        ellipse_area_mm2: Area of the 95 % confidence ellipse.
        ellipse_major_mm: Semi-major axis of the ellipse.
        ellipse_minor_mm: Semi-minor axis of the ellipse.
        ellipse_angle_deg: Angle of the major axis from the x axis (−90…90°).
        convex_hull_area_mm2: Area of the convex hull of the trajectory.
        sway_area_mm2_s: Area swept by the COP vector per second.
        mean_frequency_ml_hz: Mean frequency of the ML power spectrum.
        mean_frequency_ap_hz: Mean frequency of the AP power spectrum.
        median_frequency_ml_hz: Frequency below which 50 % of the ML power lies.
        median_frequency_ap_hz: Frequency below which 50 % of the AP power lies.
        f95_ml_hz: Frequency below which 95 % of the ML power lies.
        f95_ap_hz: Frequency below which 95 % of the AP power lies.
        total_power_ml_mm2: ML power in :data:`FREQUENCY_BAND_HZ`.
        total_power_ap_mm2: AP power in :data:`FREQUENCY_BAND_HZ`.
        detrended: The linear trend was removed instead of only the mean.
    """

    frames: int
    interpolated_frames: int
    duration_s: float
    sampling_rate_hz: float
    lowpass_hz: float | None
    mean_x_mm: float
    mean_y_mm: float
    range_ml_mm: float
    range_ap_mm: float
    sd_ml_mm: float
    sd_ap_mm: float
    mean_distance_mm: float
    rms_distance_mm: float
    path_length_mm: float
    path_length_ml_mm: float
    path_length_ap_mm: float
    mean_velocity_mm_s: float
    mean_velocity_ml_mm_s: float
    mean_velocity_ap_mm_s: float
    ellipse_area_mm2: float
    ellipse_major_mm: float
    ellipse_minor_mm: float
    ellipse_angle_deg: float
    convex_hull_area_mm2: float
    sway_area_mm2_s: float
    mean_frequency_ml_hz: float
    mean_frequency_ap_hz: float
    median_frequency_ml_hz: float
    median_frequency_ap_hz: float
    f95_ml_hz: float
    f95_ap_hz: float
    total_power_ml_mm2: float
    total_power_ap_mm2: float
    detrended: bool = False

    def as_dict(self) -> dict[str, float | int | bool | None]:
        """Return the parameters as ``{attribute name: value}`` in declaration order."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: Parameters compared by the Romberg quotient (eyes closed / eyes open).
ROMBERG_PARAMETERS = (
    "path_length_mm",
    "mean_velocity_mm_s",
    "ellipse_area_mm2",
    "sway_area_mm2_s",
    "sd_ml_mm",
    "sd_ap_mm",
    "range_ml_mm",
    "range_ap_mm",
)


@dataclass(frozen=True)
class Romberg:
    """Evaluation of a Romberg test (standing with eyes open, then with eyes closed).

    Attributes:
        eyes_open: Index of the eyes-open acquisition.
        eyes_closed: Index of the eyes-closed acquisition.
        order_from_labels: The eye condition was read from the acquisition labels;
            otherwise the order given by the caller was used (FreeSTEP stores none).
        quotients: Romberg quotient (eyes closed / eyes open) of every parameter in
            :data:`ROMBERG_PARAMETERS`; ``nan`` when the eyes-open value is zero.
    """

    eyes_open: int
    eyes_closed: int
    order_from_labels: bool
    quotients: dict[str, float]


def unit(name: str) -> str:
    """Return the unit of a :class:`Stabilometry` attribute from its name suffix (empty for counts)."""
    for suffix, text in (
        ("_mm2_s", "mm²/s"),
        ("_mm2", "mm²"),
        ("_mm_s", "mm/s"),
        ("_mm", "mm"),
        ("_hz", "Hz"),
        ("_s", "s"),
        ("_deg", "°"),
    ):
        if name.endswith(suffix):
            return text
    return ""


def analyze_all(
    post: Postura, lowpass_hz: float | None = DEFAULT_LOWPASS_HZ, *, detrend: bool = False
) -> list[Stabilometry | None]:
    """Analyse every acquisition of a recording (see :func:`analyze`), ``None`` where it is not possible."""
    results: list[Stabilometry | None] = []
    for acq in post.acquisitions:
        try:
            results.append(analyze(acq, lowpass_hz, detrend=detrend))
        except ValueError:
            results.append(None)
    return results


def loaded_mask(acq: Acquisition) -> NDArray[np.bool_]:
    """Return which frames of an acquisition have any load (the COP of the others is invalid)."""
    return np.asarray(acq.pressure.reshape(acq.frame_count, -1).any(axis=1))


def analyze(acq: Acquisition, lowpass_hz: float | None = DEFAULT_LOWPASS_HZ, *, detrend: bool = False) -> Stabilometry:
    """Compute the stabilometric parameters of an acquisition from the COP of the whole plate.

    Unloaded frames at the start and end are left out, those in between are
    linearly interpolated.

    Args:
        acq: The acquisition.
        lowpass_hz: Cut-off of the zero-phase low-pass filter; ``None`` or 0
            disables filtering, as does a cut-off at or above the Nyquist frequency.
        detrend: Remove the linear trend (slow drift) instead of only the mean.

    Returns:
        The parameters.

    Raises:
        ValueError: Fewer than :data:`MIN_FRAMES` loaded frames or an unknown sampling rate.
    """
    cop, _, interpolated = trimmed_cop(acq)
    return compute(cop, acq.sampling_rate_hz, lowpass_hz, interpolated_frames=interpolated, detrend=detrend)


def trimmed_cop(acq: Acquisition) -> tuple[NDArray[np.float64], int, int]:
    """Return the COP of the whole plate in mm as analysed by :func:`analyze` (before filtering).

    Unloaded frames at the start and end are left out, those in between are
    linearly interpolated.

    Args:
        acq: The acquisition.

    Returns:
        ``(positions (frames, 2) in mm, index of the first frame, number of interpolated frames)``.

    Raises:
        ValueError: Fewer than :data:`MIN_FRAMES` loaded frames.
    """
    loaded = loaded_mask(acq)
    indexes = np.flatnonzero(loaded)
    if len(indexes) < MIN_FRAMES:
        raise ValueError(f"Too few loaded frames for the analysis ({len(indexes)}, at least {MIN_FRAMES} needed)")
    first, last = int(indexes[0]), int(indexes[-1])
    cop = acq.cop_total[first : last + 1].astype(np.float64) * 10  # cm -> mm
    inside = loaded[first : last + 1]
    frames = np.arange(len(cop))
    for axis in range(2):
        cop[:, axis] = np.interp(frames, frames[inside], cop[inside, axis])
    return cop, first, int((~inside).sum())


def lowpass(
    values: NDArray[np.floating], sampling_rate_hz: float, cutoff_hz: float | None
) -> tuple[NDArray[np.float64], float | None]:
    """Apply the zero-phase 4th order Butterworth low-pass filter along the first axis.

    Args:
        values: Uniformly sampled signal(s), e.g. positions ``(frames, 2)``.
        sampling_rate_hz: Sampling rate.
        cutoff_hz: Cut-off; ``None``, 0 or a value at or above the Nyquist frequency disables filtering.

    Returns:
        The filtered signal and the cut-off actually used (``None`` when not filtered).
    """
    data = np.asarray(values, dtype=np.float64)
    if not cutoff_hz or cutoff_hz >= sampling_rate_hz / 2:
        return data, None
    sos = signal.butter(4, cutoff_hz, fs=sampling_rate_hz, output="sos")
    return signal.sosfiltfilt(sos, data, axis=0), cutoff_hz


def compute(
    cop_mm: NDArray[np.floating],
    sampling_rate_hz: float,
    lowpass_hz: float | None = DEFAULT_LOWPASS_HZ,
    interpolated_frames: int = 0,
    *,
    detrend: bool = False,
) -> Stabilometry:
    """Compute the stabilometric parameters of a uniformly sampled COP trajectory.

    Args:
        cop_mm: Positions, shape ``(frames, 2)`` as (ML, AP) in mm.
        sampling_rate_hz: Sampling rate.
        lowpass_hz: Cut-off of the zero-phase low-pass filter (``None`` or 0: no filtering).
        interpolated_frames: Reported in :attr:`Stabilometry.interpolated_frames`.
        detrend: Remove the linear trend instead of only the mean.

    Returns:
        The parameters.

    Raises:
        ValueError: Fewer than :data:`MIN_FRAMES` positions or an invalid sampling rate.
    """
    if len(cop_mm) < MIN_FRAMES:
        raise ValueError(f"Too few frames for the analysis ({len(cop_mm)}, at least {MIN_FRAMES} needed)")
    if not sampling_rate_hz > 0:
        raise ValueError("Unknown sampling rate")
    fs = float(sampling_rate_hz)
    cop, cutoff = lowpass(cop_mm, fs, lowpass_hz)

    mean = cop.mean(axis=0)
    centred = signal.detrend(cop, axis=0, type="linear") if detrend else cop - mean
    distance = np.hypot(centred[:, 0], centred[:, 1])
    steps = np.diff(centred, axis=0)
    duration = (len(cop) - 1) / fs
    path = float(np.hypot(steps[:, 0], steps[:, 1]).sum())
    path_ml, path_ap = (float(v) for v in np.abs(steps).sum(axis=0))

    # Confidence ellipse from the covariance (chi-square quantile with 2 degrees of freedom)
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov(centred, rowvar=False))
    scale = -2 * math.log(1 - ELLIPSE_PROBABILITY)
    minor, major = (math.sqrt(scale * max(float(v), 0.0)) for v in eigenvalues)
    angle = math.degrees(math.atan2(eigenvectors[1, 1], eigenvectors[0, 1]))
    angle = (angle + 90) % 180 - 90

    try:
        hull_area = float(ConvexHull(centred).volume)  # the "volume" of a 2-D hull is its area
    except QhullError:  # all positions on a line
        hull_area = 0.0
    swept = np.abs(centred[:-1, 0] * centred[1:, 1] - centred[1:, 0] * centred[:-1, 1]).sum() / 2

    spectra = [_spectrum(centred[:, axis], fs) for axis in range(2)]
    return Stabilometry(
        frames=len(cop),
        interpolated_frames=interpolated_frames,
        duration_s=duration,
        sampling_rate_hz=fs,
        lowpass_hz=cutoff,
        mean_x_mm=float(mean[0]),
        mean_y_mm=float(mean[1]),
        range_ml_mm=float(np.ptp(centred[:, 0])),
        range_ap_mm=float(np.ptp(centred[:, 1])),
        sd_ml_mm=float(centred[:, 0].std(ddof=1)),
        sd_ap_mm=float(centred[:, 1].std(ddof=1)),
        mean_distance_mm=float(distance.mean()),
        rms_distance_mm=float(np.sqrt((distance**2).mean())),
        path_length_mm=path,
        path_length_ml_mm=path_ml,
        path_length_ap_mm=path_ap,
        mean_velocity_mm_s=path / duration,
        mean_velocity_ml_mm_s=path_ml / duration,
        mean_velocity_ap_mm_s=path_ap / duration,
        ellipse_area_mm2=math.pi * major * minor,
        ellipse_major_mm=major,
        ellipse_minor_mm=minor,
        ellipse_angle_deg=angle,
        convex_hull_area_mm2=hull_area,
        sway_area_mm2_s=float(swept) / duration,
        mean_frequency_ml_hz=spectra[0][0],
        mean_frequency_ap_hz=spectra[1][0],
        median_frequency_ml_hz=spectra[0][1],
        median_frequency_ap_hz=spectra[1][1],
        f95_ml_hz=spectra[0][2],
        f95_ap_hz=spectra[1][2],
        total_power_ml_mm2=spectra[0][3],
        total_power_ap_mm2=spectra[1][3],
        detrended=detrend,
    )


def sway(
    acq: Acquisition, lowpass_hz: float | None = DEFAULT_LOWPASS_HZ, *, detrend: bool = False
) -> tuple[NDArray[np.float64], int]:
    """Return the COP displacement of an acquisition as analysed by :func:`analyze` (for plotting).

    Args:
        acq: The acquisition.
        lowpass_hz: Cut-off of the low-pass filter (see :func:`lowpass`).
        detrend: Remove the linear trend instead of only the mean.

    Returns:
        ``(displacement (frames, 2) in mm, index of the first frame)``.

    Raises:
        ValueError: Fewer than :data:`MIN_FRAMES` loaded frames.
    """
    cop, first, _ = trimmed_cop(acq)
    cop, _ = lowpass(cop, acq.sampling_rate_hz, lowpass_hz)
    return (signal.detrend(cop, axis=0, type="linear") if detrend else cop - cop.mean(axis=0)), first


def _spectrum(values: NDArray[np.float64], fs: float) -> tuple[float, float, float, float]:
    """Return the mean, median and 95 % frequency and the total power of a signal in :data:`FREQUENCY_BAND_HZ`.

    The power spectral density is estimated by Welch's method with segments of
    20 s (resolution 0.05 Hz) or the whole signal when it is shorter.
    """
    segment = min(len(values), round(20 * fs))
    freqs, psd = signal.welch(values, fs=fs, nperseg=segment)
    low, high = FREQUENCY_BAND_HZ
    band = (freqs >= low) & (freqs <= high)
    freqs, psd = freqs[band], psd[band]
    total = float(psd.sum())
    if total <= 0:
        return math.nan, math.nan, math.nan, 0.0
    cumulative = np.cumsum(psd) / total
    median = int(np.searchsorted(cumulative, 0.5))
    f95 = min(int(np.searchsorted(cumulative, 0.95)), len(freqs) - 1)
    return float((freqs * psd).sum() / total), float(freqs[median]), float(freqs[f95]), total * fs / segment


def _eye_condition(label: str) -> bool | None:
    """Return ``True`` for an eyes-open label, ``False`` for eyes closed, ``None`` when unknown."""
    words = "".join(c if c.isalnum() else " " for c in label.casefold()).split()

    def matches(hints: tuple[str, ...]) -> bool:
        # Short hints (EO, OC, …) must be whole words, longer ones may be prefixes
        return any(w == h if len(h) <= 2 else w.startswith(h) for w in words for h in hints)

    is_open, is_closed = matches(_OPEN_WORDS), matches(_CLOSED_WORDS)
    return None if is_open == is_closed else is_open


def detect_romberg(
    post: Postura, exam_type: int | None = None, eyes_closed_first: bool = False
) -> tuple[int, int, bool] | None:
    """Detect a Romberg test: two acquisitions of about the same duration in a posturography exam.

    The eye condition is taken from the acquisition labels when they tell it.
    FreeSTEP usually leaves them empty, then ``eyes_closed_first`` decides.

    Args:
        post: The recording.
        exam_type: Type code of the exam; another type than posturography rules the test out.
        eyes_closed_first: Without labels, the first acquisition is with eyes closed.

    Returns:
        ``(eyes open index, eyes closed index, order read from labels)`` or ``None``.
    """
    if exam_type is not None and exam_type != POSTUROGRAPHY_EXAM_TYPE:
        return None
    if len(post.acquisitions) != 2:
        return None
    first, second = post.acquisitions
    longer = max(first.duration_s, second.duration_s)
    if longer <= 0 or abs(first.duration_s - second.duration_s) > ROMBERG_DURATION_TOLERANCE * longer:
        return None
    conditions = (_eye_condition(first.label), _eye_condition(second.label))
    if conditions == (True, False):
        return 0, 1, True
    if conditions == (False, True):
        return 1, 0, True
    return (1, 0, False) if eyes_closed_first else (0, 1, False)


def evaluate_romberg(
    post: Postura,
    results: list[Stabilometry | None],
    exam_type: int | None = None,
    eyes_closed_first: bool = False,
) -> Romberg | None:
    """Evaluate the Romberg test of a recording.

    Args:
        post: The recording.
        results: Parameters of every acquisition (``None`` where the analysis failed).
        exam_type: Type code of the exam (see :func:`detect_romberg`).
        eyes_closed_first: Order of the acquisitions without labels (see :func:`detect_romberg`).

    Returns:
        The evaluation, or ``None`` when the recording is not a Romberg test or
        one of the acquisitions could not be analysed.
    """
    detected = detect_romberg(post, exam_type, eyes_closed_first)
    if detected is None:
        return None
    eo, ec, from_labels = detected
    open_result, closed_result = results[eo], results[ec]
    if open_result is None or closed_result is None:
        return None
    quotients = {}
    for name in ROMBERG_PARAMETERS:
        reference = float(getattr(open_result, name))
        quotients[name] = float(getattr(closed_result, name)) / reference if reference else math.nan
    return Romberg(eyes_open=eo, eyes_closed=ec, order_from_labels=from_labels, quotients=quotients)
