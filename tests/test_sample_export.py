"""Integration tests with the real sample export (skipped when it is not present)."""

import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from freestep import FreeStepExport

SAMPLE = Path(__file__).resolve().parent.parent / "FreeStep_Export_2025-03-17_10-52.zip"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="sample export not available")


@pytest.fixture(scope="module")
def export() -> Iterator[FreeStepExport]:
    """Open the sample export once for the whole module."""
    with FreeStepExport(SAMPLE) as exp:
        yield exp


def test_index_and_database(export: FreeStepExport) -> None:
    """Indice.ini and Export.mdb agree, and Czech names are decoded correctly."""
    assert export.index.centre_code == "00041292"
    assert export.index.patient_count == len(export.patients) == 11
    assert export.index.exam_count == len(export.exams) == len(export.fre_names) == 22
    names = {p.name for p in export.patients}
    assert "Roman Ondráček" in names and "Tomáš Kadlec" in names


# Decrypting one .fre takes ~4 s; FREESTEP_ALL_EXAMS=1 checks all of them.
_EXAMS = None if os.environ.get("FREESTEP_ALL_EXAMS") else ["00000158.fre", "00000160.fre"]


def _exam_names() -> list[str]:
    """Return the exams to test: two by default, all with ``FREESTEP_ALL_EXAMS=1``."""
    if not SAMPLE.exists():
        return []
    if _EXAMS is not None:
        return _EXAMS
    with FreeStepExport(SAMPLE) as exp:
        return exp.fre_names


@pytest.mark.parametrize("name", _exam_names())
def test_exam(export: FreeStepExport, name: str) -> None:
    """INI metadata match the MDB, and the stored COP matches the pressure data."""
    with export.open_fre(name) as fre:
        mdb_exam = next(e for e in export.exams if e.file_name == name)
        assert fre.exam.id == mdb_exam.id
        assert fre.exam.patient_id == mdb_exam.patient_id
        assert mdb_exam.patient_id is not None
        patient = export.patient(mdb_exam.patient_id)
        assert patient is not None and fre.patient.name == patient.name

        post = fre.recording
        assert post.header.platform == "Max 50x60"
        assert (post.header.columns, post.header.rows) == (120, 100)
        assert post.unused_size == 0
        # Romberg test: eyes open and eyes closed, 51.2 s at 25 Hz each
        assert len(post.acquisitions) == 2
        for acq in post.acquisitions:
            assert acq.frame_count == 1281
            assert acq.sampling_rate_hz == pytest.approx(25.0, abs=0.05)
            assert acq.pressure.max() <= post.header.max_value
            # Stored COP must match the COP computed from the pressure data
            for frame in (0, acq.frame_count // 2, acq.frame_count - 1):
                cop = acq.compute_cop(frame)
                assert cop is not None and np.allclose(cop, acq.cop_total[frame], atol=1e-3)
