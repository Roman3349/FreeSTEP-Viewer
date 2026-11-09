"""FreeSTEP export ZIP and ``.fre`` exam archives.

Both are ZIP files encrypted with legacy ZipCrypto. Python's :mod:`zipfile`
decrypts it natively, but in pure Python (~1 MB/s of compressed data), so
everything big is loaded lazily via :func:`functools.cached_property`.

Typical use::

    with FreeStepExport("FreeStep_Export.zip") as export:
        for exam in export.exams:
            with export.open_fre(exam) as fre:
                print(fre.patient.name, fre.postura.header.frame_count)
"""

from __future__ import annotations

import io
import os
import struct
import zipfile
from dataclasses import dataclass
from datetime import date, time
from functools import cached_property
from pathlib import Path
from typing import BinaryIO, Self

from .ini import DEFAULT_ANSI_ENCODING, IniData, parse_ini
from .mdb import AccessDatabase
from .models import Exam, ExportIndex, FilterSettings, Patient
from .postura import Postura
from .walkway import parse_walkway

#: Password FreeSTEP uses for both the export ZIP and the ``.fre`` archives.
DEFAULT_PASSWORD = "pwtecnico"
#: Members holding a gait recording over several plates (dynamic and treadmill exams).
GAIT_RECORDINGS = ("Dinamica.dat", "TreadMILL.dat")

#: Anything an archive can be opened from: a path, the raw bytes or a binary file object.
Source = str | os.PathLike[str] | bytes | BinaryIO


def _open_zip(source: Source, password: str | bytes | None) -> zipfile.ZipFile:
    """Open a ZIP archive and set its decryption password.

    Args:
        source: Path, raw bytes or binary file object of the archive.
        password: ZipCrypto password, or ``None`` for unencrypted archives.

    Returns:
        The opened archive. The password is only checked when a member is read.

    Raises:
        zipfile.BadZipFile: ``source`` is not a ZIP archive.
    """
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    archive = zipfile.ZipFile(source)
    if password is not None:
        archive.setpassword(password.encode() if isinstance(password, str) else password)
    return archive


def _find_member(archive: zipfile.ZipFile, name: str) -> str | None:
    """Find a member by name, ignoring case (the files come from Windows).

    Args:
        archive: Archive to search.
        name: Member name, e.g. ``"Postura.dat"``.

    Returns:
        The member name exactly as stored in the archive, or ``None``.
    """
    lowered = name.lower()
    for member in archive.namelist():
        if member.lower() == lowered:
            return member
    return None


@dataclass(frozen=True)
class VideoTrack:
    """Header of ``VideoTrack*.dat``: how the video relates to the pressure recording.

    Attributes:
        start_frame: Video frame at the first pressure frame (verified on a dynamic exam
            with video: the recording time ``t`` is at video frame ``start_frame + t × fps``).
        end_frame: Last video frame of the analysed range ❓.
        frame_count: Number of frames of the video.
    """

    start_frame: int
    end_frame: int
    frame_count: int

    @classmethod
    def parse(cls, data: bytes) -> VideoTrack | None:
        """Parse the header (int16 version, then three int32), ``None`` when the data are too short."""
        if len(data) < _VIDEO_TRACK_STRUCT.size:
            return None
        _, start, end, count = _VIDEO_TRACK_STRUCT.unpack_from(data)
        return cls(start, end, count)


_VIDEO_TRACK_STRUCT = struct.Struct("<h3i")


class _ZipBacked:
    """Common base of :class:`FreFile` and :class:`FreeStepExport`.

    Holds the open archive and provides member reading and context manager support.
    """

    _zip: zipfile.ZipFile
    _encoding: str = DEFAULT_ANSI_ENCODING

    def _read(self, name: str) -> bytes | None:
        """Read and decrypt a member.

        Args:
            name: Member name (case-insensitive).

        Returns:
            The member content, or ``None`` when the member does not exist.

        Raises:
            RuntimeError: Wrong password (message contains "Bad password").
        """
        member = _find_member(self._zip, name)
        return None if member is None else self._zip.read(member)

    def _read_ini(self, name: str) -> IniData:
        """Read and parse an INI member.

        Args:
            name: Member name (case-insensitive).

        Returns:
            Parsed content, or an empty dict when the member does not exist.
        """
        data = self._read(name)
        return {} if data is None else parse_ini(data, self._encoding)

    @property
    def members(self) -> list[str]:
        """Names of all files in the archive, in stored order."""
        return self._zip.namelist()

    def close(self) -> None:
        """Close the underlying archive. Already loaded data stays usable."""
        self._zip.close()

    def __enter__(self) -> Self:
        """Enter the context manager; the archive is closed on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the archive when leaving the ``with`` block."""
        self.close()


class FreFile(_ZipBacked):
    """One exam archive (``NNNNNNNN.fre``, the name is the exam ID).

    Contains ``Filtro.ini``, ``Paziente.ini``, ``PazienteW.ini``, ``Postura.dat``
    and optionally ``Preview1.jpg``; not every exam contains all of them.
    """

    def __init__(
        self,
        source: Source,
        password: str | bytes | None = DEFAULT_PASSWORD,
        encoding: str = DEFAULT_ANSI_ENCODING,
    ):
        """Open an exam archive.

        Args:
            source: Path, raw bytes or binary file object of the ``.fre`` file.
            password: ZipCrypto password.
            encoding: ANSI code page of ``Paziente.ini`` (see
                :data:`freestep.ini.DEFAULT_ANSI_ENCODING`).
        """
        self._zip = _open_zip(source, password)
        self._encoding = encoding

    @cached_property
    def filter_ini(self) -> IniData:
        """Raw content of ``Filtro.ini`` (empty dict when missing)."""
        return self._read_ini("Filtro.ini")

    @cached_property
    def patient_ini(self) -> IniData:
        """Raw content of ``Paziente.ini`` (empty dict when missing)."""
        return self._read_ini("Paziente.ini")

    @cached_property
    def patient_w_ini(self) -> IniData:
        """Raw content of ``PazienteW.ini``, a superset of ``Paziente.ini`` (address, notes, …)."""
        return self._read_ini("PazienteW.ini")

    @property
    def _patient_section(self) -> dict[str, str]:
        """``[Paziente]`` merged from both INI files; non-empty values of ``PazienteW.ini`` win."""
        merged = dict(self.patient_ini.get("Paziente", {}))
        merged.update({k: v for k, v in self.patient_w_ini.get("Paziente", {}).items() if v != ""})
        return merged

    @cached_property
    def filter(self) -> FilterSettings:
        """Filter applied to the recording (``Filtro.ini``)."""
        return FilterSettings.from_ini(self.filter_ini)

    @cached_property
    def patient(self) -> Patient:
        """Patient as stored in the exam's INI files."""
        return Patient.from_ini(self._patient_section)

    @cached_property
    def exam(self) -> Exam:
        """Exam metadata as stored in the exam's INI files."""
        return Exam.from_ini(self._patient_section)

    @cached_property
    def postura_bytes(self) -> bytes:
        """Decrypted content of ``Postura.dat`` (~60 MB, takes ~2 s).

        Raises:
            FileNotFoundError: The archive contains no ``Postura.dat``.
        """
        data = self._read("Postura.dat")
        if data is None:
            raise FileNotFoundError("Postura.dat not found in .fre archive")
        return data

    @cached_property
    def postura(self) -> Postura:
        """Decoded pressure recording (decrypting it takes ~2 s for a 2.5 MB ``.fre``).

        A static exam stores a single frame in ``Statica.dat`` instead; it is
        returned as one acquisition of one frame (see :meth:`Postura.parse_static`).
        A dynamic or treadmill exam stores passes over several plates in
        ``Dinamica.dat`` or ``TreadMILL.dat``; they are returned as one acquisition
        per pass (see :func:`~freestep.walkway.parse_walkway`).

        Raises:
            FileNotFoundError: The archive contains no pressure recording.
            ValueError: The data are malformed.
        """
        if _find_member(self._zip, "Postura.dat") is None:
            static = self._read("Statica.dat")
            if static is not None:
                return Postura.parse_static(static)
            for name in GAIT_RECORDINGS:
                data = self._read(name)
                if data is not None:
                    return parse_walkway(data, name)
        return Postura.parse(self.postura_bytes)

    @property
    def has_pressure(self) -> bool:
        """The archive contains pressure data :attr:`postura` can decode.

        Other exam types store data of unknown format or only images (see :attr:`image_names`).
        """
        return any(
            _find_member(self._zip, name) is not None for name in ("Postura.dat", "Statica.dat", *GAIT_RECORDINGS)
        )

    @property
    def image_names(self) -> list[str]:
        """Names of the stored images other than the previews, e.g. ``Scanner.bmp`` of a podoscanner exam.

        The scanner image comes first, the others in archive order.
        """
        names = [
            m
            for m in self.members
            if m.lower().endswith((".bmp", ".jpg", ".jpeg", ".png")) and not m.lower().startswith("preview")
        ]
        return sorted(names, key=lambda m: m.lower() != "scanner.bmp")

    def image(self, name: str) -> bytes | None:
        """Return a stored image (see :attr:`image_names`), ``None`` when missing."""
        return self._read(name)

    @property
    def video(self) -> bytes | None:
        """The video recorded with the exam (``Capture.avi``, e.g. XviD), ``None`` when there is none."""
        return self._read("Capture.avi")

    @property
    def video_track(self) -> VideoTrack | None:
        """Header of ``VideoTrack.dat`` / ``VideoTrack_0.dat`` stored with the video, ``None`` when there is none."""
        name = next(
            (m for m in self.members if m.lower().startswith("videotrack") and m.lower().endswith(".dat")), None
        )
        data = self._read(name) if name is not None else None
        return VideoTrack.parse(data) if data is not None else None

    @property
    def video_rotation(self) -> int:
        """Clockwise rotation of the video in degrees for display (``ROTAZIONE`` in ``Capture.ini``)."""
        value = self._read_ini("Capture.ini").get("CAPINFO", {}).get("ROTAZIONE", "0")
        try:
            return int(value) % 360
        except ValueError:
            return 0

    @property
    def preview_names(self) -> list[str]:
        """Names of the preview images (``Preview1.jpg``, …), sorted."""
        return sorted(m for m in self.members if m.lower().startswith("preview") and m.lower().endswith(".jpg"))

    def preview_jpeg(self, index: int = 0) -> bytes | None:
        """Return a preview image.

        Args:
            index: Zero-based index into :attr:`preview_names` (0 = ``Preview1.jpg``).

        Returns:
            JPEG bytes, or ``None`` when there is no such preview.
        """
        names = self.preview_names
        return self._zip.read(names[index]) if index < len(names) else None


class FreeStepExport(_ZipBacked):
    """The export ZIP (``FreeStep_Export_YYYY-MM-DD_HH-MM.zip``).

    Contains ``Indice.ini`` (summary), ``Export.mdb`` (patients and exams) and
    one ``.fre`` archive per exam.
    """

    def __init__(
        self,
        source: Source,
        password: str | bytes | None = DEFAULT_PASSWORD,
        encoding: str = DEFAULT_ANSI_ENCODING,
    ):
        """Open an export.

        Args:
            source: Path, raw bytes or binary file object of the export ZIP.
            password: ZipCrypto password, also used for the contained ``.fre`` files.
            encoding: ANSI code page of the INI files (see
                :data:`freestep.ini.DEFAULT_ANSI_ENCODING`).
        """
        self._zip = _open_zip(source, password)
        self._encoding = encoding
        self._password = password

    @cached_property
    def index(self) -> ExportIndex:
        """Summary of the export from ``Indice.ini``."""
        return ExportIndex.from_ini(self._read_ini("Indice.ini"))

    @cached_property
    def database(self) -> AccessDatabase:
        """The ``Export.mdb`` database (decrypted and parsed on first access).

        Raises:
            FileNotFoundError: The export contains no ``Export.mdb``.
        """
        data = self._read("Export.mdb")
        if data is None:
            raise FileNotFoundError("Export.mdb not found in export")
        return AccessDatabase(data)

    @cached_property
    def patients(self) -> list[Patient]:
        """All patients (table ``Pazienti``), in database order."""
        return [Patient.from_mdb(row) for row in self.database.rows("Pazienti")]

    @cached_property
    def exams(self) -> list[Exam]:
        """All exams (table ``Esami``), in database order."""
        return [Exam.from_mdb(row) for row in self.database.rows("Esami")]

    def patient(self, patient_id: int) -> Patient | None:
        """Find a patient.

        Args:
            patient_id: Patient ID (``Pazienti.ID``, ``Esami.IDPaziente``).

        Returns:
            The patient, or ``None`` when there is no such ID.
        """
        return next((p for p in self.patients if p.id == patient_id), None)

    def exams_of(self, patient_id: int) -> list[Exam]:
        """List the exams of one patient.

        Args:
            patient_id: Patient ID.

        Returns:
            The patient's exams sorted by date, time and ID.
        """
        return sorted(
            (e for e in self.exams if e.patient_id == patient_id),
            key=lambda e: (e.exam_date or date.min, e.exam_time or time.min, e.id or 0),
        )

    @property
    def fre_names(self) -> list[str]:
        """Names of the exam archives (``*.fre``) present in the export, sorted."""
        return sorted(m for m in self.members if m.lower().endswith(".fre"))

    def open_fre(self, name_or_exam_id: str | int | Exam) -> FreFile:
        """Open an exam archive contained in the export.

        This decrypts the whole ``.fre`` into memory (~2 s); its content is
        decrypted again lazily when accessed.

        Args:
            name_or_exam_id: File name (``"00000158.fre"``), exam ID (``158``)
                or an :class:`~freestep.models.Exam`.

        Returns:
            The opened exam. Close it (or use ``with``) when done.

        Raises:
            FileNotFoundError: The export does not contain that exam.
        """
        if isinstance(name_or_exam_id, Exam):
            name = name_or_exam_id.file_name
        elif isinstance(name_or_exam_id, int):
            name = f"{name_or_exam_id:08d}.fre"
        else:
            name = name_or_exam_id
        data = self._read(name)
        if data is None:
            raise FileNotFoundError(f"{name} not found in export")
        return FreFile(data, self._password, self._encoding)

    def extract_fre(self, name: str, target_dir: str | os.PathLike[str]) -> Path:
        """Write an exam archive to disk.

        The ``.fre`` file itself stays encrypted, exactly as FreeSTEP wrote it.

        Args:
            name: File name of the exam archive, e.g. ``"00000158.fre"``.
            target_dir: Existing directory to write into.

        Returns:
            Path of the written file.

        Raises:
            FileNotFoundError: The export does not contain that file.
        """
        target = Path(target_dir) / Path(name).name
        data = self._read(name)
        if data is None:
            raise FileNotFoundError(f"{name} not found in export")
        target.write_bytes(data)
        return target
