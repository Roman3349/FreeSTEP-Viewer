"""An exam loaded for display: decoded data, precomputed images and how they are shown."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from ..archive import FreFile
from ..load import LoadDistribution, LoadRegions, load_distribution, load_regions
from ..models import Exam, FilterSettings, Patient
from ..postura import Acquisition, Postura
from .widgets import COP_LEFT_COLOR, COP_RIGHT_COLOR, COP_TOTAL_COLOR, HeatmapView

# Indexes of the display mode combo box; also used in PNG file names.
MODE_FRAME, MODE_MAX, MODE_MEAN = 0, 1, 2
MODE_FILE_SUFFIX = {MODE_MAX: "max", MODE_MEAN: "mean"}


@dataclass
class LoadedExam:
    """An exam with its decoded recording and values precomputed for display.

    Attributes:
        fre: The open exam archive (closed when another exam is shown).
        exam: Exam metadata from the archive.
        patient: Patient from the archive.
        filter: Filter settings of the recording.
        postura: The decoded recording.
        pressure_sums: Per acquisition: sum of all sensor values per frame.
        max_images: Per acquisition: per-sensor maximum over all frames.
        loads: Per acquisition: load quadrants and the load distribution.
        preview: JPEG of ``Preview1.jpg``, if present.
    """

    fre: FreFile
    exam: Exam
    patient: Patient
    filter: FilterSettings
    postura: Postura
    pressure_sums: list[NDArray[np.int64]]
    max_images: list[NDArray[np.uint16]]
    loads: list[tuple[LoadRegions, LoadDistribution]]
    preview: bytes | None


def load_exam(fre: FreFile) -> LoadedExam:
    """Decode an exam and precompute the display values (runs in the worker thread).

    Args:
        fre: The open exam archive.

    Returns:
        The loaded exam.
    """
    post = fre.postura  # the slow part (ZipCrypto decryption)
    return LoadedExam(
        fre=fre,
        exam=fre.exam,
        patient=fre.patient,
        filter=fre.filter,
        postura=post,
        pressure_sums=[a.pressure.reshape(a.frame_count, -1).sum(axis=1, dtype=np.int64) for a in post.acquisitions],
        max_images=[a.pressure.max(axis=0) for a in post.acquisitions],
        loads=[(load_regions(a.pressure.sum(axis=0)), load_distribution(a.pressure)) for a in post.acquisitions],
        preview=fre.preview_jpeg(),
    )


def is_bad_password(exc: BaseException) -> bool:
    """Tell whether ``exc`` is the error :mod:`zipfile` raises for a wrong password."""
    return isinstance(exc, RuntimeError) and "password" in str(exc).lower()


def show_on_heatmap(
    view: HeatmapView, loaded: LoadedExam, acq: Acquisition, frame: int, mode: int, *, trail: int
) -> None:
    """Show a frame, or the maximum / mean image, of an acquisition on a heat map.

    Args:
        view: The heat map.
        loaded: The exam.
        acq: The acquisition.
        frame: Zero-based frame index (used in the frame mode).
        mode: ``MODE_FRAME``, ``MODE_MAX`` or ``MODE_MEAN``.
        trail: Number of previous frames drawn as the COP trail (0: none; the whole
            trajectory in the maximum / mean mode).
    """
    h = loaded.postura.header
    cell = (float(h.cell_width_mm), float(h.cell_height_mm))
    if mode == MODE_FRAME:
        view.set_frame(
            acq.pressure[frame],
            h.max_value,
            cell,
            cops=[
                (COP_LEFT_COLOR, acq.cop_left[frame]),
                (COP_RIGHT_COLOR, acq.cop_right[frame]),
                (COP_TOTAL_COLOR, acq.cop_total[frame]),
            ],
            trail=acq.cop_total[max(0, frame - trail) : frame + 1] if trail else None,
        )
        view.set_regions(None)
    else:
        image = loaded.max_images[acq.index] if mode == MODE_MAX else acq.mean_image
        view.set_frame(image, h.max_value, cell, trail=acq.cop_total if trail else None)
        regions, shares = loaded.loads[acq.index]
        view.set_regions(regions, load_shares(shares))


def load_shares(shares: LoadDistribution) -> tuple[float | None, float | None, float | None, float | None]:
    """Shares of the left front, left rear, right front and right rear quadrant for :meth:`HeatmapView.set_regions`."""
    return (
        shares.get("left_front_pct"),
        shares.get("left_rear_pct"),
        shares.get("right_front_pct"),
        shares.get("right_rear_pct"),
    )


def png_bytes(image: QImage) -> bytes:
    """Encode an image as PNG."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")  # type: ignore[call-overload]  # the stubs say bytes, PySide6 accepts only str
    buffer.close()
    return bytes(data.data())
