"""Unit tests that need no sample data."""

import gettext
import io
import struct
from pathlib import Path

import numpy as np
import pytest

from freestep.convert import to_date, to_float
from freestep.gui import i18n, po
from freestep.gui.colormap import colorize
from freestep.ini import parse_ini
from freestep.mdb import decode_jet4_text
from freestep.postura import Postura


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
        assert i18n.tr("Exam {id}", id=158) == "Vyšetření 158"
        assert i18n.tr("not in the catalogue") == "not in the catalogue"
        assert "2 měření" in i18n.ntr(
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
