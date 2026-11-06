"""Integration tests with the anonymised exam in ``tests/data`` (a Romberg test, runs in CI)."""

import csv
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from scipy.io import loadmat

from freestep import FreFile
from freestep.batch import FORMATS, exam_stem, export_exam, summary_rows, write_summary_csv
from freestep.cli import main
from freestep.stabilometry import analyze_all, evaluate_romberg
from freestep.writers import write_mat

FIXTURE = Path(__file__).resolve().parent / "data" / "00000160.fre"


@pytest.fixture(scope="module")
def fre() -> Iterator[FreFile]:
    """Open and decode the exam once for the whole module (decryption takes a few seconds)."""
    with FreFile(FIXTURE) as exam:
        _ = exam.postura
        yield exam


def test_metadata_is_anonymised(fre: FreFile) -> None:
    """The INI files hold no personal data, the exam metadata are intact."""
    assert fre.patient.name == "Testovací Pacient"
    assert str(fre.patient.birth_date) == "1990-01-01"
    assert fre.exam.id == 160 and fre.exam.exam_type == 4
    assert fre.patient.shoe_size_us == 10.5  # also in the malformed "ShoeTag=WShoeNum=" line


def test_stabilometry_and_romberg(fre: FreFile) -> None:
    """Parameters of both acquisitions (regression values) and the Romberg quotients."""
    post = fre.postura
    results = analyze_all(post)
    assert all(r is not None and r.frames == 1281 and r.lowpass_hz == 5.0 for r in results)
    first, second = results
    assert first is not None and second is not None
    assert first.path_length_mm == pytest.approx(114.83, abs=0.01)
    assert second.path_length_mm == pytest.approx(160.37, abs=0.01)
    assert first.ellipse_area_mm2 == pytest.approx(54.99, abs=0.01)
    assert second.ellipse_area_mm2 == pytest.approx(140.48, abs=0.01)
    detrended = analyze_all(post, detrend=True)[0]
    assert detrended is not None and detrended.detrended
    assert detrended.ellipse_area_mm2 == pytest.approx(22.75, abs=0.01)
    unfiltered = analyze_all(post, None)[0]
    assert unfiltered is not None
    assert unfiltered.path_length_mm == pytest.approx(post.acquisitions[0].path_length_mm())

    romberg = evaluate_romberg(post, results, fre.exam.exam_type)
    assert romberg is not None and (romberg.eyes_open, romberg.eyes_closed) == (0, 1)
    assert not romberg.order_from_labels
    assert romberg.quotients["path_length_mm"] == pytest.approx(160.37 / 114.83, rel=1e-3)


def test_cli_exports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``freestep exam`` prints the parameters and writes the MATLAB and CSV files."""
    mat, stats = tmp_path / "exam.mat", tmp_path / "stats.csv"
    assert main(["exam", str(FIXTURE), "--mat", str(mat), "--stats", str(stats)]) == 0
    out = capsys.readouterr().out
    assert "Romberg test" in out and "ellipse_area_mm2" in out
    data = loadmat(mat, squeeze_me=True, struct_as_record=False)
    assert data["acq"][1].pressure.shape == (100, 120, 1281)
    assert data["romberg"].eyes_closed == 2
    assert stats.read_text(encoding="utf-8").startswith("parameter,unit,acquisition_1,acquisition_2,romberg_quotient")


@pytest.mark.skipif(shutil.which("octave-cli") is None, reason="GNU Octave not installed")
def test_mat_loads_in_octave(fre: FreFile, tmp_path: Path) -> None:
    """GNU Octave reads the MATLAB file with the documented layout."""
    post = fre.postura
    results = analyze_all(post)
    path = tmp_path / "exam.mat"
    write_mat(post, path, results, evaluate_romberg(post, results, fre.exam.exam_type))
    frame = post.acquisitions[0].pressure[99]
    script = (
        f"d = load('{path.as_posix()}');"
        "printf('%s|%s|%s|%d|%d|%.2f|%d\\n', mat2str(size(d.acq)), mat2str(size(d.acq(1).pressure)),"
        " class(d.acq(1).pressure), sum(double(d.acq(1).pressure(:, :, 100)(:))), d.acq(1).pressure(2, 3, 100),"
        " d.acq(2).stabilometry.ellipse_area_mm2, d.romberg.eyes_closed);"
    )
    output = subprocess.run(
        ["octave-cli", "--no-gui", "--quiet", "--eval", script], capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()
    second = results[1]
    assert second is not None
    expected = f"[1 2]|[100 120 1281]|uint16|{int(frame.sum())}|{int(frame[1, 2])}|{second.ellipse_area_mm2:.2f}|2"
    assert output == expected


def test_raw_mat_has_no_analysis(fre: FreFile, tmp_path: Path) -> None:
    """The raw MATLAB file holds the recorded data and the header, but no parameters."""
    path = tmp_path / "raw.mat"
    write_mat(fre.postura, path)
    data = loadmat(path, squeeze_me=True, struct_as_record=False)
    assert "romberg" not in data
    first = data["acq"][0]
    assert "stabilometry" not in first._fieldnames
    assert first.unknown.shape == (1281,)
    assert data["header"].version == 3 and list(data["header"].settings) == list(fre.postura.header.settings)


def test_export_exam_names_and_formats(fre: FreFile, tmp_path: Path) -> None:
    """Every format is written under the ``<birth date>_<name>_exam_<ID>`` stem."""
    post = fre.postura
    results = analyze_all(post)
    stem = exam_stem(fre.patient, fre.exam)
    assert stem == "1990-01-01_Testovaci_Pacient_exam_160"
    files = export_exam(post, tmp_path, stem, list(FORMATS), results=results, romberg=None)
    assert sorted(f.name for f in files) == sorted(f"{stem}{suffix}" for suffix in FORMATS.values())
    assert all(f.stat().st_size > 0 for f in files)
    rows = summary_rows(fre.patient, fre.exam, post, results, None)
    write_summary_csv(rows, tmp_path / "summary.csv")
    with open(tmp_path / "summary.csv", encoding="utf-8", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [r["acquisition"] for r in table] == ["1", "2"]
    assert table[0]["name"] == "Testovací Pacient" and float(table[1]["path_length_mm"]) == pytest.approx(160.37, 0.01)
