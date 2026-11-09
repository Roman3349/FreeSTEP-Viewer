"""Integration tests with the anonymised exams in ``tests/data`` (Romberg tests of one patient, run in CI)."""

import csv
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from scipy.io import loadmat

from freestep import FreFile
from freestep.analysis.protocols import evaluate_romberg
from freestep.analysis.stabilometry import analyze_all
from freestep.batch import FORMATS, exam_stem, export_exam, summary_rows, write_summary_csv
from freestep.cli import main
from freestep.history import (
    ROMBERG_CONDITION,
    ROMBERG_PREFIX,
    ExamAnalysis,
    analyze_exam,
    analyze_exams,
    history_rows,
    patient_exams,
    series,
)
from freestep.models import Exam
from freestep.writers import write_mat

FIXTURE = Path(__file__).resolve().parent / "data" / "00000160.fre"


@pytest.fixture(scope="module")
def fre() -> Iterator[FreFile]:
    """Open and decode the exam once for the whole module (decryption takes a few seconds)."""
    with FreFile(FIXTURE) as exam:
        _ = exam.recording
        yield exam


def test_metadata_is_anonymised(fre: FreFile) -> None:
    """The INI files hold no personal data, the exam metadata are intact."""
    assert fre.patient.name == "Testovací Pacient"
    assert str(fre.patient.birth_date) == "1990-01-01"
    assert fre.exam.id == 160 and fre.exam.exam_type == 4
    assert fre.patient.shoe_size_us == 10.5  # also in the malformed "ShoeTag=WShoeNum=" line


def test_stabilometry_and_romberg(fre: FreFile) -> None:
    """Parameters of both acquisitions (regression values) and the Romberg quotients."""
    post = fre.recording
    results = analyze_all(post)
    assert all(r is not None and r.frames == 1281 and r.lowpass_hz == 5.0 for r in results)
    first, second = results
    assert first is not None and second is not None
    assert first.path_length_mm == pytest.approx(114.83, abs=0.01)
    assert second.path_length_mm == pytest.approx(160.37, abs=0.01)
    assert first.ellipse_area_mm2 == pytest.approx(54.99, abs=0.01)
    assert second.ellipse_area_mm2 == pytest.approx(140.48, abs=0.01)
    assert first.left_load_pct == pytest.approx(57.05, abs=0.01)
    assert second.load_asymmetry_pct == pytest.approx(-30.32, abs=0.01)
    assert first.forefoot_pct == pytest.approx(41.48, abs=0.01)
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
    post = fre.recording
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
    write_mat(fre.recording, path)
    data = loadmat(path, squeeze_me=True, struct_as_record=False)
    assert "romberg" not in data
    first = data["acq"][0]
    assert "stabilometry" not in first._fieldnames
    assert first.unknown.shape == (1281,)
    assert data["header"].version == 3 and list(data["header"].settings) == list(fre.recording.header.settings)


def test_export_exam_names_and_formats(fre: FreFile, tmp_path: Path) -> None:
    """Every format is written under the ``<birth date>_<name>_<exam date>_<HH-MM>`` stem."""
    post = fre.recording
    results = analyze_all(post)
    stem = exam_stem(fre.patient, fre.exam)
    assert stem == "1990-01-01_Testovaci_Pacient_2025-03-10_09-37"
    # Without the time of the exam the ID is used
    assert exam_stem(fre.patient, replace(fre.exam, exam_time=None)) == "1990-01-01_Testovaci_Pacient_exam_160"
    files = export_exam(post, tmp_path, stem, list(FORMATS), results=results, romberg=None)
    assert sorted(f.name for f in files) == sorted(f"{stem}{suffix}" for suffix in FORMATS.values())
    assert all(f.stat().st_size > 0 for f in files)
    rows = summary_rows(fre.patient, fre.exam, post, results, None)
    write_summary_csv(rows, tmp_path / "summary.csv")
    with open(tmp_path / "summary.csv", encoding="utf-8", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [r["acquisition"] for r in table] == ["1", "2"]
    assert table[0]["name"] == "Testovací Pacient" and float(table[1]["path_length_mm"]) == pytest.approx(160.37, 0.01)


def test_history_series(fre: FreFile) -> None:
    """The history has one series per eye condition and one for the Romberg quotient."""
    analysis = analyze_exam(fre.recording, fre.exam)
    assert analysis.conditions == ["eyes_open", "eyes_closed"]
    failed = ExamAnalysis(exam=fre.exam, results=[None], romberg=None, conditions=["acquisition_1"])
    values = series([analysis, failed, analysis], "path_length_mm")
    assert list(values) == ["eyes_open", "eyes_closed", "acquisition_1"]
    assert values["eyes_open"][0] == pytest.approx(114.83, abs=0.01) and values["eyes_open"][1] is None
    assert values["acquisition_1"] == [None, None, None]
    quotient = series([analysis, failed], f"{ROMBERG_PREFIX}path_length_mm")
    assert list(quotient) == [ROMBERG_CONDITION]
    assert quotient[ROMBERG_CONDITION][0] == pytest.approx(160.37 / 114.83, rel=1e-3)
    assert quotient[ROMBERG_CONDITION][1] is None
    with pytest.raises(ValueError):
        series([analysis], "no_such_parameter")
    rows = history_rows([analysis])
    assert [(r["exam_id"], r["condition"]) for r in rows] == [(160, "eyes_open"), (160, "eyes_closed")]
    assert rows[0]["romberg_ellipse_area_mm2"] == pytest.approx(140.48 / 54.99, rel=1e-3)


class _Export:
    """Stand-in for :class:`~freestep.FreeStepExport` with the two test exams of one patient."""

    def __init__(self) -> None:
        self.exams = []
        for name in ("00000161.fre", "00000160.fre"):
            with FreFile(FIXTURE.with_name(name)) as fre:
                self.exams.append(fre.exam)
        self.fre_names = [e.file_name for e in self.exams if e.file_name]

    def exams_of(self, patient_id: int) -> list[Exam]:
        return sorted((e for e in self.exams if e.patient_id == patient_id), key=lambda e: e.id or 0)

    def open_fre(self, exam: Exam) -> FreFile:
        assert exam.file_name is not None
        return FreFile(FIXTURE.with_name(exam.file_name))


def test_patient_history() -> None:
    """Both exams of the test patient are analysed, oldest first, with progress and cancelling."""
    export = _Export()
    exams = patient_exams(export, 46)  # type: ignore[arg-type]
    assert [e.id for e in exams] == [160, 161]
    calls: list[int] = []

    def progress(index: int, count: int, exam: Exam) -> bool:
        calls.append(exam.id or 0)
        return True

    analyses, failed = analyze_exams(export, exams, progress=progress)  # type: ignore[arg-type]
    assert not failed and calls == [160, 161]
    values = series(analyses, "ellipse_area_mm2")
    assert values["eyes_open"] == pytest.approx([54.99, 134.87], abs=0.01)
    assert values["eyes_closed"] == pytest.approx([140.48, 64.72], abs=0.01)
    stopped, _ = analyze_exams(export, exams, progress=lambda i, n, e: i == 0)  # type: ignore[arg-type]
    assert [a.exam.id for a in stopped] == [160]
