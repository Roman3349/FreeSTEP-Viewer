"""Unit tests that need no sample data."""

import gettext
import io
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest
from scipy.io import loadmat

from freestep.archive import FreFile, VideoTrack
from freestep.batch import safe_name
from freestep.convert import to_date, to_float
from freestep.gui import i18n, po
from freestep.gui.colormap import colorize
from freestep.ini import parse_ini
from freestep.mdb import decode_jet4_text
from freestep.postura import Postura
from freestep.writers import write_mat


def test_ini_repairs_malformed_lines() -> None:
    """Missing ``=`` and missing line breaks in ``PazienteW.ini`` are repaired."""
    data = (
        "﻿[Paziente]\r\nNomeFile00000158.fre\r\nShoeTag=WShoeNum=8,5\r\n"
        "ShoeTag_Esame=WShoeNum_Esame=8,5\r\nNota_Esame=\r\n"
    ).encode()
    section = parse_ini(data)["Paziente"]
    assert section == {
        "NomeFile": "00000158.fre",
        "ShoeTag": "W",
        "ShoeNum": "8,5",
        "ShoeTag_Esame": "W",
        "ShoeNum_Esame": "8,5",
        "Nota_Esame": "",
    }


def test_ini_ansi_encoding() -> None:
    """Non-UTF-8 files are decoded with the ANSI code page (cp1250)."""
    data = "[Paziente]\r\nNominativo=Roman Ondráček\r\n".encode("cp1250")
    assert parse_ini(data)["Paziente"]["Nominativo"] == "Roman Ondráček"


def test_jet4_compressed_unicode() -> None:
    """Compressed unicode with mode toggles and plain UTF-16 are decoded."""
    assert decode_jet4_text(b"\xff\xfeOndr\xe1\x00\x0d\x01\x00ek") == "Ondráček"
    assert decode_jet4_text("Tomáš".encode("utf-16-le")) == "Tomáš"


def test_converters() -> None:
    """Decimal comma and both date formats are converted."""
    assert to_float("8,5") == 8.5
    assert str(to_date("10.03.2025")) == "2025-03-10"
    assert str(to_date("2003-04-21 00:00:00")) == "2003-04-21"


def _synthetic_postura(version: int, acquisitions: list[int], rows: int = 4, cols: int = 6, tail: int = 0) -> bytes:
    """Build a ``Postura.dat`` like FreeSTEP writes it.

    Every frame has one loaded cell (value 10 + frame index at row 1, column 2) and
    a timestamp every 40 ms; each acquisition is followed by its mean image and,
    for version 3, a 100 character label.
    """
    settings = {1: (0, 0), 3: (-1, 512, 0, 0, 1, 1)}[version]
    data = struct.pack(f"<h50s5h{len(settings)}h", version, b"Test".ljust(50), cols, rows, 5, 5, 255, *settings)
    for number, count in enumerate(acquisitions):
        data += struct.pack("<hi", -1, count - 1)
        for i in range(count):
            p = np.zeros((rows, cols), "<u2")
            p[1, 2] = 10 + i
            data += p.tobytes() + struct.pack("<ih6f", 1000 + 40 * i, 246, 1.0, 0.5, 1.0, 0.5, 0, 0)
        data += np.full((rows, cols), number, "<u2").tobytes()
        if version == 3:
            data += f"label {number}".ljust(100).encode()
    return data + bytes(tail)


def _synthetic_static(frame: np.ndarray) -> bytes:
    """Build a ``Statica.dat``: header, 7 int16 settings, the array descriptor, one frame and the record rest."""
    rows, cols = frame.shape
    header = struct.pack("<h50s5h", 3, b"Test 40x40".ljust(50), cols, rows, 5, 5, 255)
    descriptor = struct.pack("<h4i", 2, cols, 0, rows, 0)
    return header + bytes(2 * 7) + descriptor + frame.astype("<u2").tobytes() + bytes(26)


def _synthetic_fre(members: dict[str, bytes]) -> bytes:
    """Build an (unencrypted) ``.fre`` archive with the given members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_postura_static() -> None:
    """``Statica.dat`` is read as one acquisition of one frame with the COPs computed like FreeSTEP's."""
    frame = np.zeros((3, 8), np.uint16)
    frame[1, 1], frame[2, 2] = 10, 30  # left half
    frame[0, 5] = 20  # right half
    post = Postura.parse_static(_synthetic_static(frame))
    assert (post.header.columns, post.header.rows, post.header.size) == (8, 3, 94)
    (acq,) = post.acquisitions
    assert acq.frame_count == 1
    assert np.array_equal(acq.pressure[0], frame) and np.array_equal(acq.mean_image, frame)
    assert acq.cop_total[0] == pytest.approx(acq.compute_cop(0))
    assert acq.cop_left[0] == pytest.approx(((10 * 1 + 30 * 2) / 40 * 0.5, (10 * 1 + 30 * 2) / 40 * 0.5))
    assert acq.cop_right[0] == pytest.approx((5 * 0.5, 0))
    without_descriptor = bytearray(_synthetic_static(frame))
    without_descriptor[76:94] = bytes(18)
    with pytest.raises(ValueError, match="no 8x3 array descriptor"):
        Postura.parse_static(bytes(without_descriptor))
    with pytest.raises(ValueError, match="one 8x3 frame"):
        Postura.parse_static(_synthetic_static(frame)[: 94 + frame.size * 2 - 1])


def test_fre_exam_types() -> None:
    """Static exams are read from ``Statica.dat``; other exams without pressure data offer their images."""
    frame = np.ones((3, 8), np.uint16)
    with FreFile(_synthetic_fre({"Statica.dat": _synthetic_static(frame), "Statica.BMP": b"photo"})) as fre:
        assert fre.has_pressure
        assert fre.postura.acquisitions[0].pressure.sum() == frame.sum()
        assert fre.image_names == ["Statica.BMP"]
    scan = {"Originale.bmp": b"scan", "Thumbs.db": b"", "Scanner.bmp": b"scan", "Preview1.jpg": b"jpeg"}
    with FreFile(_synthetic_fre(scan)) as fre:
        assert not fre.has_pressure
        assert fre.image_names == ["Scanner.bmp", "Originale.bmp"]  # the scan first, no previews
        assert fre.image("Scanner.bmp") == b"scan" and fre.image("missing.bmp") is None
        with pytest.raises(FileNotFoundError):
            _ = fre.postura
        assert fre.video is None and fre.video_rotation == 0
    video = {"Capture.avi": b"RIFF....AVI ", "Capture.ini": b"[CAPINFO]\r\nCOMPRESSIONE=1\r\nROTAZIONE=90\r\n"}
    with FreFile(_synthetic_fre(video)) as fre:
        assert fre.video == video["Capture.avi"] and fre.video_rotation == 90
        assert fre.video_track is None
    video["VideoTrack_0.dat"] = struct.pack("<h3i", 1, 89, 153, 674) + bytes(40)
    with FreFile(_synthetic_fre(video)) as fre:
        assert fre.video_track == VideoTrack(start_frame=89, end_frame=153, frame_count=674)


@pytest.mark.parametrize("version", [1, 3])
def test_postura_synthetic(version: int) -> None:
    """Header, all acquisitions, frame data, timestamps and COP of a synthetic file are decoded."""
    post = Postura.parse(_synthetic_postura(version, [3, 5], tail=10))
    assert post.header.platform == "Test"
    assert post.header.size == {1: 66, 3: 74}[version]
    assert [a.frame_count for a in post.acquisitions] == [3, 5]
    assert post.unused_size == 10
    second = post.acquisitions[1]
    assert second.pressure.shape == (5, 4, 6)
    assert second.pressure[2, 1, 2] == 12
    assert second.mean_image.max() == 1
    assert second.label == ("label 1" if version == 3 else "")
    assert second.duration_s == pytest.approx(0.16)
    assert second.sampling_rate_hz == pytest.approx(25.0)
    assert second.compute_cop(0) == pytest.approx((1.0, 0.5))
    assert tuple(second.cop_total[0]) == pytest.approx((1.0, 0.5))


def test_postura_unknown_version_is_detected() -> None:
    """For an unknown version the header size is found from the first acquisition."""
    data = bytearray(_synthetic_postura(3, [2]))
    data[0:2] = struct.pack("<h", 7)
    post = Postura.parse(bytes(data))
    assert post.header.size == 74
    assert post.acquisitions[0].frame_count == 2


def test_postura_rejects_truncated_file() -> None:
    """A file shorter than its first acquisition is rejected."""
    with pytest.raises(ValueError):
        Postura.parse(_synthetic_postura(3, [5])[:-2000])


def test_colormap() -> None:
    """Zero is white, small values are visible and the maximum is red."""
    img = colorize(np.array([[0, 1, 255]], dtype=np.uint16), 255)
    assert img.shape == (1, 3, 4)
    assert tuple(img[0, 0]) == (255, 255, 255, 255)  # no load = white
    assert tuple(img[0, 1]) != (255, 255, 255, 255)  # small load is visible
    assert img[0, 2, 2] > 150 and img[0, 2, 0] < 50  # max = red (BGRA)


def test_po_files_are_up_to_date() -> None:
    """``freestep.pot`` and the ``*.po`` files match the sources (run ``python -m freestep.gui.po update``)."""
    for path, content in po.build_files().items():
        assert path.read_text(encoding="utf-8") == content, f"{path.name} is outdated"


@pytest.mark.parametrize("path", sorted(po.LOCALE_DIR.glob("*.po")), ids=lambda p: p.name)
def test_translation_is_complete(path: Path) -> None:
    """Every message is translated, not fuzzy, and keeps the placeholders of the source."""
    catalog = po.read_po(path)
    assert "Plural-Forms" in catalog.header
    for entry in catalog.entries:
        assert entry.translated and not entry.fuzzy, entry.msgid
        sources = po.placeholders(entry.msgid) | po.placeholders(entry.msgid_plural or "")
        forms = [entry.msgstr] if entry.msgid_plural is None else list(entry.msgstr_plural.values())
        for form in forms:
            assert po.placeholders(form) == sources, entry.msgid


def test_po_round_trip_and_plurals() -> None:
    """Parsing and writing is lossless; compiled plurals follow the Czech rules."""
    text = (
        '# comment\nmsgid ""\nmsgstr ""\n"Language: cs\\n"\n'
        '"Plural-Forms: nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;\\n"\n\n'
        '#: a.py:1\n#, python-brace-format\nmsgid "{n} frame"\nmsgid_plural "{n} frames"\n'
        'msgstr[0] "{n} snímek"\nmsgstr[1] "{n} snímky"\nmsgstr[2] "{n} snímků"\n\n'
        'msgctxt "menu"\nmsgid "Say \\"hi\\"\\n"\nmsgstr "Řekni \\"ahoj\\"\\n"\n\n'
        '#, fuzzy\nmsgid "Draft"\nmsgstr "Koncept"\n'
    )
    catalog = po.parse_po(text)
    assert po.write_po(catalog) == text
    translations = gettext.GNUTranslations(io.BytesIO(po.compile_mo(catalog)))
    assert [translations.ngettext("{n} frame", "{n} frames", n) for n in (1, 3, 5)] == [
        "{n} snímek",
        "{n} snímky",
        "{n} snímků",
    ]
    assert translations.pgettext("menu", 'Say "hi"\n') == 'Řekni "ahoj"\n'
    assert translations.gettext("Draft") == "Draft"  # fuzzy entries are not used


def test_resolve_language_follows_os() -> None:
    """``AUTO_LANGUAGE`` and unknown values resolve to the OS language; codes stay."""
    system = i18n.system_language()
    assert system in i18n.LANGUAGES
    assert i18n.resolve_language(i18n.AUTO_LANGUAGE) == system
    assert i18n.resolve_language("does-not-exist") == system
    assert i18n.resolve_language("cs") == "cs"
    assert i18n.resolve_language("en") == "en"


def test_tr_switches_language() -> None:
    """``tr``/``ntr`` translate, fill in placeholders and fall back to English."""
    try:
        i18n.set_language("cs")
        assert i18n.tr("Exam {id}", id=158) == "Měření 158"
        assert i18n.tr("not in the catalogue") == "not in the catalogue"
        assert "2 záznamy" in i18n.ntr(
            "Exam {id} — {name}: {n} acquisition, {platform}",
            "Exam {id} — {name}: {n} acquisitions, {platform}",
            2,
            id=1,
            name="X",
            platform="P",
        )
        i18n.set_language("xx")
        assert i18n.language() == i18n.DEFAULT_LANGUAGE
        assert i18n.tr("Exam {id}", id=158) == "Exam 158"
    finally:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)


def test_safe_name() -> None:
    """Diacritics are removed, spaces and other characters become one underscore."""
    assert safe_name("Roman Ondráček") == "Roman_Ondracek"
    assert safe_name("  Žluťoučký   kůň / O'Neil ") == "Zlutoucky_kun_O_Neil"
    assert safe_name("") == "unknown"


def test_write_mat(tmp_path: Path) -> None:
    """The MATLAB file holds the frames (rows × columns × frames), COP, timestamps and the header."""
    post = Postura.parse(_synthetic_postura(3, [3, 5]))
    path = tmp_path / "exam.mat"
    write_mat(post, path)
    mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    assert mat["platform"] == "Test" and list(mat["cell_size_mm"]) == [5, 5]
    assert mat["header"].version == 3 and mat["header"].rows == 4
    second = mat["acq"][1]
    assert second.pressure.shape == (4, 6, 5)
    assert second.pressure[1, 2, 2] == 12
    assert list(second.timestamp_ms) == [1000, 1040, 1080, 1120, 1160]
    assert second.cop_total.shape == (5, 2) and second.label == "label 1"
