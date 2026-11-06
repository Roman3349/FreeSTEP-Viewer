"""Batch export of all exams of a FreeSTEP export into one directory.

Files are named ``<birth date>_<name>_exam_<ID><suffix>``, e.g.
``1998-06-06_Jan_Novak_exam_160.mat``: the birth date in ISO format, the name
without diacritics and with ``_`` instead of spaces and other characters.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .archive import FreeStepExport
from .models import Exam, Patient
from .postura import Postura
from .writers import write_cop_csv, write_mat, write_npz

#: Export formats: key -> file name suffix (appended to the exam stem).
FORMATS: dict[str, str] = {
    "mat": ".mat",
    "cop-csv": "_cop.csv",
    "npz": ".npz",
}
#: Name of the index of the exported exams written by :func:`batch_export`.
INDEX_NAME = "index.csv"


def safe_name(text: str) -> str:
    """Make a file-name-safe ASCII version of a name: no diacritics, ``_`` instead of spaces etc.

    Args:
        text: E.g. ``"Jan Nováček"``.

    Returns:
        E.g. ``"Jan_Novacek"``; ``"unknown"`` when nothing is left.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_") or "unknown"


def exam_stem(patient: Patient | None, exam: Exam) -> str:
    """Return the file name stem ``<birth date>_<name>_exam_<ID>`` of an exam.

    Args:
        patient: The exam's patient (``None`` when unknown).
        exam: The exam.
    """
    birth = patient.birth_date.isoformat() if patient is not None and patient.birth_date else "unknown"
    name = safe_name(patient.name) if patient is not None else "unknown"
    return f"{birth}_{name}_exam_{exam.id}"


def export_exam(
    post: Postura,
    directory: str | os.PathLike[str],
    stem: str,
    formats: Sequence[str],
) -> list[Path]:
    """Write one exam in the given formats.

    Args:
        post: The recording.
        directory: Output directory (must exist).
        stem: File name stem (see :func:`exam_stem`).
        formats: Keys of :data:`FORMATS`.

    Returns:
        The written files.
    """
    written = []
    for key in formats:
        path = Path(directory) / f"{stem}{FORMATS[key]}"
        if key == "mat":
            write_mat(post, path)
        elif key == "cop-csv":
            write_cop_csv(post, path)
        elif key == "npz":
            write_npz(post, path)
        else:
            raise ValueError(f"Unknown export format {key!r}")
        written.append(path)
    return written


def write_summary_csv(rows: list[dict[str, object]], path: str | os.PathLike[str]) -> None:
    """Write rows of ``{column: value}`` as a CSV table, UTF-8 with ``,`` as separator."""
    columns: list[str] = []  # union of the keys in first-seen order
    for row in rows:
        columns += [key for key in row if key not in columns]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if v is None else v for k, v in row.items()})


@dataclass
class BatchResult:
    """Outcome of :func:`batch_export`.

    Attributes:
        written: Written files.
        skipped: ``(exam, reason)`` of the exams that could not be exported.
        cancelled: The progress callback stopped the export.
    """

    written: list[Path] = field(default_factory=list)
    skipped: list[tuple[Exam, str]] = field(default_factory=list)
    cancelled: bool = False


def batch_export(
    export: FreeStepExport,
    directory: str | os.PathLike[str],
    formats: Sequence[str],
    *,
    progress: Callable[[int, int, Exam], bool] | None = None,
) -> BatchResult:
    """Export every exam of an export (decrypting each takes a few seconds).

    Args:
        export: The open export.
        directory: Output directory (created when missing).
        formats: Keys of :data:`FORMATS`. :data:`INDEX_NAME` with one row per exam
            (patient, exam, files, status) is written always.
        progress: Called before each exam with ``(index, count, exam)``; returning
            ``False`` cancels the export (the files written so far are kept).

    Returns:
        The written files and the skipped exams.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    unknown = set(formats) - FORMATS.keys()
    if unknown:
        raise ValueError(f"Unknown export format(s): {', '.join(sorted(unknown))}")
    present = {name.lower() for name in export.fre_names}
    patients = {p.id: p for p in export.patients}
    exams = sorted(export.exams, key=lambda e: exam_stem(patients.get(e.patient_id), e))
    result = BatchResult()
    index_rows: list[dict[str, object]] = []
    for index, exam in enumerate(exams):
        if progress is not None and not progress(index, len(exams), exam):
            result.cancelled = True
            break
        patient = patients.get(exam.patient_id)
        stem = exam_stem(patient, exam)
        entry = _index_row(stem, patient, exam)
        index_rows.append(entry)
        if not exam.file_name or exam.file_name.lower() not in present:
            result.skipped.append((exam, "missing .fre file"))
            entry["status"] = "missing .fre file"
            continue
        try:
            with export.open_fre(exam.file_name) as fre:
                post = fre.postura
                files = export_exam(post, out, stem, formats)
        except (OSError, ValueError, RuntimeError) as exc:
            result.skipped.append((exam, str(exc)))
            entry["status"] = str(exc)
            continue
        result.written += files
        entry.update(
            acquisitions=len(post.acquisitions),
            frames=" ".join(str(a.frame_count) for a in post.acquisitions),
            sampling_rate_hz=" ".join(f"{a.sampling_rate_hz:.3f}" for a in post.acquisitions),
            files=" ".join(f.name for f in files),
            status="ok",
        )
    if index_rows:
        path = out / INDEX_NAME
        write_summary_csv(index_rows, path)
        result.written.append(path)
    return result


def _index_row(stem: str, patient: Patient | None, exam: Exam) -> dict[str, object]:
    """Start the row of an exam in :data:`INDEX_NAME` (the recording columns are added after decoding)."""
    return {
        "stem": stem,
        "patient_id": patient.id if patient is not None else "",
        "name": patient.name if patient is not None else "",
        "birth_date": patient.birth_date or "" if patient is not None else "",
        "exam_id": exam.id,
        "exam_date": exam.exam_date or "",
        "exam_time": exam.exam_time or "",
        "exam_type": exam.exam_type_name,
        "source": exam.file_name,
        "acquisitions": "",
        "frames": "",
        "sampling_rate_hz": "",
        "files": "",
        "status": "",
    }
