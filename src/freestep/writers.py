"""Writing decoded recordings to common file formats (CSV, NumPy ``.npz``)."""

from __future__ import annotations

import csv
import os

import numpy as np

from .postura import Postura

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


def write_cop_csv(post: Postura, path: str | os.PathLike[str]) -> None:
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


def write_npz(post: Postura, path: str | os.PathLike[str]) -> None:
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
