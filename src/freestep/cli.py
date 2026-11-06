"""Command line interface: ``freestep info|exam|batch|dump``."""

from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

from .archive import DEFAULT_PASSWORD, FreeStepExport, FreFile
from .batch import FORMATS, batch_export, exam_stem
from .ini import DEFAULT_ANSI_ENCODING
from .models import Exam
from .writers import write_cop_csv, write_mat, write_npz


def _fmt(value: object) -> str:
    """Format a value for output, ``None`` as an empty string."""
    return "" if value is None else str(value)


def cmd_info(args: argparse.Namespace) -> int:
    """``freestep info``: print the export summary, patients and their exams.

    Args:
        args: Parsed arguments (``export``, ``password``, ``encoding``).

    Returns:
        Process exit code.
    """
    with FreeStepExport(args.export, args.password, args.encoding) as export:
        idx = export.index
        print(
            f"Centre: {idx.centre_code}  created: {_fmt(idx.created)}  "
            f"patients: {_fmt(idx.patient_count)}  exams: {_fmt(idx.exam_count)}"
        )
        print(f"Tables: {', '.join(export.database.table_names)}")
        for patient in export.patients:
            print(
                f"\n[{patient.id}] {patient.name}  born {_fmt(patient.birth_date)}  "
                f"{_fmt(patient.height_cm)} cm  {_fmt(patient.weight_kg)} kg"
            )
            for exam in export.exams_of(patient.id) if patient.id is not None else []:
                present = "" if exam.file_name in export.fre_names else "  (missing .fre)"
                print(
                    f"    exam {exam.id}: {_fmt(exam.exam_date)} {_fmt(exam.exam_time)}  "
                    f"{exam.exam_type_name}  {exam.footwear_name}  {exam.file_name}{present}"
                )
    return 0


def _open_exam(args: argparse.Namespace) -> tuple[FreFile, FreeStepExport | None]:
    """Open the exam given on the command line (a ``.fre`` file or an export + ``--exam``).

    Args:
        args: Parsed arguments (``source``, ``exam``, ``password``, ``encoding``).

    Returns:
        The exam and the export it was taken from (``None`` for a ``.fre`` file);
        the caller closes both.
    """
    path = Path(args.source)
    if path.suffix.lower() == ".fre":
        return FreFile(path, args.password, args.encoding), None
    if args.exam is None:
        sys.exit("--exam ID is required when the source is an export ZIP")
    export = FreeStepExport(path, args.password, args.encoding)
    return export.open_fre(args.exam), export


def cmd_exam(args: argparse.Namespace) -> int:
    """``freestep exam``: print exam details and optionally export its data.

    Args:
        args: Parsed arguments (``source``, ``exam``, ``csv``, ``npz``, ``mat``, ``preview``, ``raw``, …).

    Returns:
        Process exit code.
    """
    fre, export = _open_exam(args)
    try:
        exam, patient, flt = fre.exam, fre.patient, fre.filter
        print(
            f"Exam {exam.id} ({exam.file_name}): {_fmt(exam.exam_date)} {_fmt(exam.exam_time)}, "
            f"type {exam.exam_type_name}, filter={'raw' if flt.is_raw else flt.filter} freq={_fmt(flt.frequency)}"
        )
        print(f"Patient {patient.id}: {patient.name}, born {_fmt(patient.birth_date)}")
        print(f"Members: {', '.join(fre.members)}")

        post = fre.postura
        h = post.header
        print(
            f"Platform: {h.platform!r} v{h.version}, {h.columns}x{h.rows} cells of "
            f"{h.cell_width_mm}x{h.cell_height_mm} mm ({h.width_cm} x {h.height_cm} cm), max value {h.max_value}"
        )
        print(f"Settings (unknown meaning): {h.settings}, unused bytes: {post.unused_size}")
        for acq in post.acquisitions:
            sums = acq.pressure.reshape(acq.frame_count, -1).sum(axis=1)
            cop = acq.cop_total
            label = f" {acq.label!r}" if acq.label else ""
            print(
                f"Acquisition {acq.index}{label}: {acq.frame_count} frames, {acq.duration_s:.2f} s, "
                f"{acq.sampling_rate_hz:.2f} Hz, COP path {acq.path_length_mm():.1f} mm"
            )
            print(f"    pressure sum per frame: min {sums.min()}, mean {sums.mean():.0f}, max {sums.max()}")
            print(
                f"    COP x: {cop[:, 0].min():.2f}..{cop[:, 0].max():.2f} cm, "
                f"y: {cop[:, 1].min():.2f}..{cop[:, 1].max():.2f} cm"
            )

        if args.csv:
            write_cop_csv(post, args.csv)
            print(f"COP written to {args.csv}")
        if args.npz:
            write_npz(post, args.npz)
            print(f"Frames written to {args.npz}")
        if args.mat:
            write_mat(post, args.mat)
            print(f"MATLAB file written to {args.mat}")
        if args.preview:
            jpeg = fre.preview_jpeg()
            if jpeg is None:
                print("No preview image in the archive")
            else:
                Path(args.preview).write_bytes(jpeg)
                print(f"Preview written to {args.preview}")
        if args.raw:
            Path(args.raw).write_bytes(fre.postura_bytes)
            print(f"Postura.dat written to {args.raw}")
    finally:
        fre.close()
        if export is not None:
            export.close()
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """``freestep batch``: export every exam of an export into a directory.

    Files are named ``<birth date>_<name>_exam_<ID><suffix>`` (see :mod:`freestep.batch`).

    Args:
        args: Parsed arguments (``export``, ``output``, ``format``, ``dry_run``).

    Returns:
        Process exit code (1 when an exam could not be exported).
    """
    formats = args.format or ["mat"]

    def progress(index: int, count: int, exam: Exam) -> bool:
        print(f"[{index + 1}/{count}] exam {exam.id}", flush=True)
        return True

    with FreeStepExport(args.export, args.password, args.encoding) as export:
        patients = {p.id: p for p in export.patients}
        if args.dry_run:
            for exam in export.exams:
                print(exam_stem(patients.get(exam.patient_id), exam))
            return 0
        result = batch_export(export, args.output, formats, progress=progress)
    print(f"{len(result.written)} files written to {args.output}")
    for exam, reason in result.skipped:
        print(f"exam {exam.id} skipped: {reason}", file=sys.stderr)
    return 1 if result.skipped else 0


def cmd_dump(args: argparse.Namespace) -> int:
    """``freestep dump``: write every table of ``Export.mdb`` to ``<output>/<table>.csv``.

    Args:
        args: Parsed arguments (``export``, ``output``, ``password``, ``encoding``).

    Returns:
        Process exit code.
    """
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with FreeStepExport(args.export, args.password, args.encoding) as export:
        db = export.database
        for table in db.table_names:
            rows = db.rows(table)
            columns = db.columns(table)
            with open(out / f"{table}.csv", "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            print(f"{table}: {len(rows)} rows")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the ``info``, ``exam``, ``batch`` and ``dump`` sub-commands.

    Returns:
        The parser; each sub-command stores its handler in ``func``.
    """
    parser = argparse.ArgumentParser(prog="freestep", description="FreeSTEP export parser")
    parser.add_argument(
        "--password", default=DEFAULT_PASSWORD, help="ZIP password (default: the built-in FreeSTEP password)"
    )
    parser.add_argument(
        "--encoding", default=DEFAULT_ANSI_ENCODING, help="ANSI code page of Paziente.ini (default: %(default)s)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="list patients and exams of an export ZIP")
    info.add_argument("export")
    info.set_defaults(func=cmd_info)

    exam = sub.add_parser("exam", help="show/convert one exam (.fre or export ZIP + --exam)")
    exam.add_argument("source", help=".fre file or export ZIP")
    exam.add_argument("--exam", type=int, help="exam ID when the source is an export ZIP")
    exam.add_argument("--csv", help="write per-frame time and COP of all acquisitions to CSV")
    exam.add_argument("--npz", help="write frames and COP of all acquisitions to a NumPy .npz")
    exam.add_argument("--mat", help="write frames and COP of all acquisitions to a MATLAB .mat")
    exam.add_argument("--preview", help="save Preview1.jpg")
    exam.add_argument("--raw", help="save decrypted Postura.dat")
    exam.set_defaults(func=cmd_exam)

    batch = sub.add_parser("batch", help="export all exams of an export ZIP as <birth date>_<name>_exam_<ID>.<format>")
    batch.add_argument("export")
    batch.add_argument("output", help="output directory")
    batch.add_argument(
        "--format",
        action="append",
        choices=list(FORMATS),
        help="format to write, repeatable (default: mat): mat = frames and COP, cop-csv, npz",
    )
    batch.add_argument("--dry-run", action="store_true", help="only print the file name stems")
    batch.set_defaults(func=cmd_batch)

    dump = sub.add_parser("dump", help="dump Export.mdb tables to CSV")
    dump.add_argument("export")
    dump.add_argument("output", help="output directory")
    dump.set_defaults(func=cmd_dump)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``freestep`` command.

    Args:
        argv: Arguments without the program name; ``None`` uses ``sys.argv``.

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    try:
        return func(args)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        print(f"freestep: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:  # zipfile reports a wrong password as RuntimeError
        print(f"freestep: {exc} (use --password)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
