"""Tests of the PDF report (needs PySide6, runs without a display)."""

import re
from datetime import date, time
from pathlib import Path

import pytest
from test_units import _synthetic_postura

from freestep.models import Exam, Patient
from freestep.postura import Postura
from freestep.stabilometry import POSTUROGRAPHY_EXAM_TYPE, analyze_all, evaluate_romberg

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from freestep.gui.report import ReportData, write_report


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    """The widgets rendered into the report need an application."""
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _data(acquisitions: list[int]) -> ReportData:
    """Report data of a synthetic posturography exam."""
    post = Postura.parse(_synthetic_postura(3, acquisitions))
    patient = Patient(
        id=1,
        name="Jan Novák",
        birth_date=date(1990, 5, 17),
        height_cm=180.0,
        weight_kg=75.0,
        shoe_size_eu=43.0,
        shoe_size_us=None,
        shoe_tag="",
        height_in=None,
        weight_lb=None,
        units="",
        sex="M",
        ssid="",
        note="",
    )
    exam = Exam(
        id=7,
        patient_id=1,
        exam_type=POSTUROGRAPHY_EXAM_TYPE,
        footwear=0,
        exam_date=date(2025, 3, 10),
        exam_time=time(9, 37),
        ssid="",
        note="Test",
        file_name="00000007.fre",
    )
    results = analyze_all(post, None)
    romberg = evaluate_romberg(post, results, exam.exam_type)
    return ReportData(patient, exam, post, results, romberg, None)


@pytest.mark.parametrize("acquisitions", [[40], [40, 40]])
def test_write_report(tmp_path: Path, acquisitions: list[int]) -> None:
    """A report with one acquisition or a Romberg test is a multi-page PDF."""
    data = _data(acquisitions)
    assert (data.romberg is not None) == (len(acquisitions) == 2)
    path = tmp_path / "report.pdf"
    write_report(path, data)
    content = path.read_bytes()
    assert content.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b", content)) >= 3
