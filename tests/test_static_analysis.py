"""Tests of the static analysis (footprint of one frame) and of the static PDF report with synthetic data."""

import re
from datetime import date, time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from test_units import _synthetic_static

from freestep.analysis.static import analyze_static
from freestep.formats import parse_static
from freestep.gui.reports.static import StaticReportData, write_static_report
from freestep.models import Exam, Patient


def _frame() -> np.ndarray:
    """Left foot of 3 × 2 cells (front row 0 loaded 10, rear rows 1–2 loaded 20), right foot of 2 × 1 cells."""
    frame = np.zeros((4, 10), np.uint16)
    frame[0, 1:3] = 10
    frame[1:3, 1:3] = 20
    frame[0:2, 7] = 30
    return frame


def test_footprint() -> None:
    """Load shares, front and rear halves, contact area, size and values of every foot."""
    post = parse_static(_synthetic_static(_frame()))
    analysis = analyze_static(post.acquisitions[0].pressure[0], post.header)
    left, right, total = analysis.left, analysis.right, analysis.total
    assert left is not None and right is not None
    assert left.load_pct == pytest.approx(100 / 160 * 100) and right.load_pct == pytest.approx(60 / 160 * 100)
    # The left foot is 3 rows long: the front half is row 0 and half of row 1
    assert left.forefoot_pct == pytest.approx((20 + 20) / 100 * 100)
    assert left.contact_area_cm2 == pytest.approx(6 * 0.25)
    assert (left.length_cm, left.width_cm) == pytest.approx((1.5, 1.0))
    assert (left.peak_value, right.peak_value, total.peak_value) == (20, 30, 30)
    assert total.contact_area_cm2 == pytest.approx(8 * 0.25)
    assert sum(q for q in analysis.quadrants if q is not None) == pytest.approx(100)
    with pytest.raises(ValueError):
        analyze_static(np.zeros((4, 10), np.uint16), post.header)


@pytest.mark.parametrize("photo", [False, True])
def test_static_report(tmp_path: Path, photo: bool) -> None:
    """The report is one page, the photo adds a page."""
    _ = QApplication.instance() or QApplication([])
    frame = np.zeros((40, 40), np.uint16)
    frame[5:35, 5:12] = 100
    frame[5:35, 25:32] = 120
    post = parse_static(_synthetic_static(frame))
    patient = Patient(1, "Test", date(1990, 1, 1), 170.0, 60.0, 40.0, None, "", None, None, "", None, "", "")
    exam = Exam(1, 1, 1, 0, date(2025, 1, 1), time(10, 0), "", "", "00000001.fre")
    image = QImage(60, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("gray"))
    path = tmp_path / "static.pdf"
    write_static_report(path, StaticReportData(patient, exam, post, [("Statica.BMP", image)] if photo else []))
    content = path.read_bytes()
    assert content.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b", content)) == (2 if photo else 1)
