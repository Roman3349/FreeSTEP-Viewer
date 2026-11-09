"""Writing decoded recordings to common file formats (CSV, NumPy ``.npz``, MATLAB ``.mat``)."""

from __future__ import annotations

import csv
import dataclasses
import math
import os

import numpy as np
from scipy.io import savemat

from .analysis.protocols import ROMBERG_PARAMETERS, Romberg
from .analysis.stabilometry import Stabilometry, unit
from .recording import Recording

#: Columns of :func:`write_cop_csv`.
COP_CSV_COLUMNS = [
    "acquisition",
    "frame",
    "time_s",
    "timestamp_ms",
    "pressure_sum",
    "cop_x_cm",
    "cop_y_cm",
    "cop_left_x_cm",
    "cop_left_y_cm",
    "cop_right_x_cm",
    "cop_right_y_cm",
]


def write_cop_csv(post: Recording, path: str | os.PathLike[str]) -> None:
    """Write the per-frame time, pressure sum and COP of all acquisitions to a CSV file.

    Args:
        post: The recording.
        path: Output file (overwritten), UTF-8 with ``,`` as separator.
    """
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COP_CSV_COLUMNS)
        for acq in post.acquisitions:
            sums = acq.pressure.reshape(acq.frame_count, -1).sum(axis=1, dtype=np.int64)
            for i in range(acq.frame_count):
                writer.writerow(
                    [
                        acq.index,
                        i,
                        f"{acq.time_s[i]:.3f}",
                        int(acq.timestamps_ms[i]),
                        int(sums[i]),
                        *(f"{v:.4f}" for v in (*acq.cop_total[i], *acq.cop_left[i], *acq.cop_right[i])),
                    ]
                )


def write_npz(post: Recording, path: str | os.PathLike[str]) -> None:
    """Write all acquisitions to a compressed NumPy archive.

    Keys are prefixed with the acquisition index, e.g. ``a0_pressure``
    ``(frames, rows, columns)``, ``a0_time_s``, ``a0_cop_total``, ``a0_cop_left``,
    ``a0_cop_right`` ``(frames, 2)`` in cm and ``a0_mean_image``. ``cell_size_mm``
    and ``acquisitions`` (the count) describe the whole file.

    Args:
        post: The recording.
        path: Output file (overwritten).
    """
    arrays: dict[str, np.ndarray] = {
        "cell_size_mm": np.array([post.header.cell_width_mm, post.header.cell_height_mm]),
        "acquisitions": np.array(len(post.acquisitions)),
    }
    for acq in post.acquisitions:
        prefix = f"a{acq.index}_"
        arrays.update(
            {
                f"{prefix}pressure": acq.pressure,
                f"{prefix}time_s": acq.time_s,
                f"{prefix}cop_total": acq.cop_total,
                f"{prefix}cop_left": acq.cop_left,
                f"{prefix}cop_right": acq.cop_right,
                f"{prefix}mean_image": acq.mean_image,
            }
        )
    np.savez_compressed(path, **arrays)  # type: ignore[arg-type]


def write_stabilometry_csv(
    results: list[Stabilometry | None], path: str | os.PathLike[str], romberg: Romberg | None = None
) -> None:
    """Write the stabilometric parameters with one row per parameter and one column per acquisition.

    Args:
        results: Parameters of every acquisition (see :func:`freestep.analysis.stabilometry.analyze_all`);
            ``None`` gives empty cells.
        path: Output file (overwritten), UTF-8 with ``,`` as separator.
        romberg: Adds the ``romberg_quotient`` column (eyes closed / eyes open).
    """
    names = [f.name for f in dataclasses.fields(Stabilometry)]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["parameter", "unit", *(f"acquisition_{i + 1}" for i in range(len(results)))]
        writer.writerow([*header, "romberg_quotient"] if romberg else header)
        for name in names:
            values = [_csv_value(r.as_dict()[name]) if r is not None else "" for r in results]
            row = [name, unit(name), *values]
            if romberg is not None:
                row.append(_csv_value(romberg.quotients[name]) if name in romberg.quotients else "")
            writer.writerow(row)


def _csv_value(value: float | int | None) -> str:
    """Format a parameter for CSV: integers as they are, floats with 4 decimals, ``None``/NaN empty."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value) if isinstance(value, int) else f"{value:.4f}"


def write_mat(
    post: Recording,
    path: str | os.PathLike[str],
    results: list[Stabilometry | None] | None = None,
    romberg: Romberg | None = None,
) -> None:
    """Write all acquisitions to a compressed MATLAB file (format 5, readable by MATLAB and Octave).

    The file holds ``platform``, ``cell_size_mm`` (``[width height]``), ``header``
    (struct of the file header: ``version``, ``columns``, ``rows``, ``max_value``,
    ``settings``, …) and the struct array ``acq`` with one element per
    acquisition: ``label``, ``sampling_rate_hz``, ``time_s``, ``timestamp_ms`` and
    ``unknown`` (the per-frame int16 of unknown meaning; column vectors),
    ``pressure`` (``rows × columns × frames``, so ``imagesc(acq(1).pressure(:,:,k))``
    shows frame ``k``), ``cop_total``, ``cop_left``, ``cop_right`` (``frames × 2``
    in cm, as stored) and ``mean_image``.

    Without ``results`` and ``romberg`` the file holds only the recorded (raw)
    data. With ``results`` every ``acq`` element has a ``stabilometry`` struct
    (empty where the analysis failed); with a Romberg evaluation, ``romberg``
    holds the (1-based) ``eyes_open`` / ``eyes_closed`` indexes,
    ``order_from_labels`` and the ``quotients`` struct.

    Args:
        post: The recording.
        path: Output file (overwritten).
        results: Stabilometric parameters of every acquisition.
        romberg: Evaluation of the Romberg test.
    """
    fields = ["label", "sampling_rate_hz", "time_s", "timestamp_ms", "unknown", "pressure"]
    fields += ["cop_total", "cop_left", "cop_right", "mean_image"]
    if results is not None:
        fields.append("stabilometry")
    acq_array = np.empty((1, len(post.acquisitions)), dtype=[(name, object) for name in fields])
    for i, acq in enumerate(post.acquisitions):
        values: list[object] = [
            acq.label,
            acq.sampling_rate_hz,
            acq.time_s.reshape(-1, 1),
            acq.timestamps_ms.astype(np.int32).reshape(-1, 1),
            acq.records["unknown"].astype(np.int16).reshape(-1, 1),
            np.moveaxis(acq.pressure, 0, -1),
            np.ascontiguousarray(acq.cop_total),
            np.ascontiguousarray(acq.cop_left),
            np.ascontiguousarray(acq.cop_right),
            np.ascontiguousarray(acq.mean_image),
        ]
        if results is not None:
            result = results[i] if i < len(results) else None
            values.append(_mat_struct(result.as_dict()) if result is not None else np.empty((0, 0)))
        acq_array[0, i] = tuple(values)
    h = post.header
    variables: dict[str, object] = {
        "platform": h.platform,
        "cell_size_mm": np.array([[h.cell_width_mm, h.cell_height_mm]], dtype=np.float64),
        "header": {
            "version": h.version,
            "platform": h.platform,
            "columns": h.columns,
            "rows": h.rows,
            "cell_width_mm": h.cell_width_mm,
            "cell_height_mm": h.cell_height_mm,
            "max_value": h.max_value,
            "settings": np.array([h.settings], dtype=np.int16),
        },
        "acq": acq_array,
    }
    if romberg is not None:
        variables["romberg"] = {
            "eyes_open": romberg.eyes_open + 1,
            "eyes_closed": romberg.eyes_closed + 1,
            "order_from_labels": romberg.order_from_labels,
            "quotients": _mat_struct({name: romberg.quotients[name] for name in ROMBERG_PARAMETERS}),
        }
    savemat(path, variables, do_compression=True, oned_as="column")


def _mat_struct(values: dict[str, float | int | None]) -> dict[str, object]:
    """Convert parameters to a MATLAB struct, ``None`` as an empty matrix."""
    return {name: np.empty((0, 0)) if value is None else value for name, value in values.items()}
