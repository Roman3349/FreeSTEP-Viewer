"""Tests of loading exams in the viewer (needs PySide6, runs without a display)."""

import pytest
from test_units import _synthetic_fre, _synthetic_static

pytest.importorskip("PySide6")
import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from freestep.archive import FreFile
from freestep.gui.exam import decode_images, load_exam

PATIENT_INI = b"[Paziente]\r\nID=1\r\nIDEsame=7\r\nTipoEsame=8\r\nNominativo=Test Subject\r\nNomeFile=00000007.fre\r\n"


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    """Decoding images needs an application."""
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _bmp(dpi: int) -> bytes:
    """A small BMP image with the given resolution."""
    image = QImage(20, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "BMP")  # type: ignore[call-overload]
    return bytes(data.data())


def test_load_exam_without_pressure() -> None:
    """A podoscanner exam is loaded without pressure data; identical scans are shown once, with a scale."""
    scan, photo = _bmp(100), _bmp(96)
    members = {"Paziente.ini": PATIENT_INI, "Scanner.bmp": scan, "Originale.bmp": scan, "Foto.bmp": photo}
    loaded = load_exam(FreFile(_synthetic_fre(members)))
    assert loaded.postura is None and loaded.max_images == []
    assert [name for name, _ in loaded.images] == ["Scanner.bmp", "Foto.bmp"]
    (_, scanned, scale), (_, _, no_scale) = decode_images(loaded)
    assert scanned.width() == 20 and scale == pytest.approx(0.254, rel=0.01)
    assert no_scale is None  # a photo has only a nominal resolution


def test_load_static_exam() -> None:
    """A static exam is loaded with its frame as pressure data."""
    frame = np.full((3, 8), 5, np.uint16)
    members = {
        "Paziente.ini": PATIENT_INI.replace(b"TipoEsame=8", b"TipoEsame=1"),
        "Statica.dat": _synthetic_static(frame),
    }
    loaded = load_exam(FreFile(_synthetic_fre(members)))
    assert loaded.postura is not None and loaded.postura.acquisitions[0].frame_count == 1
    assert int(loaded.pressure_sums[0][0]) == frame.sum()
