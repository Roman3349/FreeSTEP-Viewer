"""Reading the Microsoft Access database (``Export.mdb``) in pure Python.

Uses the ``access-parser`` package, so no ODBC driver is needed (works on Windows,
Linux and macOS alike).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import access_parser.access_parser as _ap

#: One table row as ``{column name: value}``. Values are ``int``, ``float``,
#: ``str`` (texts and dates as ``yyyy-mm-dd hh:mm:ss``), ``bytes`` or ``None``.
Row = dict[str, object]


def _install_primary_key_workaround() -> None:
    """Work around an ``access-parser`` 0.0.6 bug.

    For tables with deleted columns (e.g. ``Esami``) it maps the primary key
    column ids to the wrong columns and raises ``KeyError``. We do not need the
    primary keys at all, so we just drop the index list before it is used.
    """
    if getattr(_ap.parse_table_data, "_freestep_patched", False):
        return
    original = _ap.parse_table_data

    def parse_table_data(*args: Any, **kwargs: Any) -> Any:
        """Call the original parser and drop the index definitions."""
        parsed = original(*args, **kwargs)
        parsed["all_indexes"] = []
        return parsed

    parse_table_data._freestep_patched = True  # type: ignore[attr-defined]
    _ap.parse_table_data = parse_table_data


def decode_jet4_text(data: bytes) -> str:
    r"""Decode a Jet4 TEXT/MEMO value.

    Values starting with ``FF FE`` use "compressed unicode": one byte per
    character (U+0000–U+00FF), and a ``00`` byte toggles between compressed and
    plain UTF-16LE mode (e.g. ``Ondr\xe1 00 0d01 00 ek`` = "Ondráček").
    Values without the marker are plain UTF-16LE.

    Args:
        data: Raw bytes of the value as stored in the data page.

    Returns:
        The decoded text.
    """
    if not data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le", errors="replace").replace("\x00", "")
    chars: list[str] = []
    compressed = True
    i, size = 2, len(data)
    while i < size:
        if data[i] == 0:
            compressed = not compressed
            i += 1
        elif compressed:
            chars.append(chr(data[i]))
            i += 1
        elif i + 1 < size:
            chars.append(data[i : i + 2].decode("utf-16-le", errors="replace"))
            i += 2
        else:
            break
    return "".join(chars)


def _install_text_decoding_fix() -> None:
    """Replace the text decoding of ``access-parser`` 0.0.6 with :func:`decode_jet4_text`.

    The original ignores the mode toggles of compressed unicode, which mangles
    non Latin-1 characters (Czech "č", "š", …). Other data types are left to the
    original implementation.
    """
    if getattr(_ap.parse_type, "_freestep_patched", False):
        return
    original = _ap.parse_type

    def parse_type(
        data_type: int, buffer: bytes, length: int | None = None, version: int = 3, props: Any = None
    ) -> Any:
        """Decode TEXT values ourselves, delegate everything else."""
        if data_type == _ap.TYPE_TEXT and version > 3 and length is not None:
            return decode_jet4_text(bytes(buffer[:length]))
        return original(data_type, buffer, length, version=version, props=props)

    parse_type._freestep_patched = True  # type: ignore[attr-defined]
    _ap.parse_type = parse_type


_install_primary_key_workaround()
_install_text_decoding_fix()


class AccessDatabase:
    """Read-only access to a Microsoft Access (Jet4) database.

    Thin wrapper around :class:`access_parser.AccessParser` returning rows as dicts.
    Tables are parsed again on every call, so cache the results when needed.
    """

    def __init__(self, source: str | os.PathLike[str] | bytes):
        """Load the database.

        Args:
            source: Path to the ``.mdb`` file or its content.
        """
        if isinstance(source, bytes):
            # access-parser only accepts a path
            fd, tmp = tempfile.mkstemp(suffix=".mdb")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(source)
                self._parser = _ap.AccessParser(tmp)
            finally:
                os.unlink(tmp)
        else:
            self._parser = _ap.AccessParser(str(Path(source)))

    @property
    def table_names(self) -> list[str]:
        """User tables (system ``MSys*`` tables are omitted)."""
        return [name for name in self._parser.catalog if not name.startswith("MSys")]

    def columns(self, table: str) -> list[str]:
        """List the columns of a table.

        Args:
            table: Table name, e.g. ``"Pazienti"``.

        Returns:
            Column names in table order.
        """
        return list(self._parser.parse_table(table).keys())

    def rows(self, table: str) -> list[Row]:
        """Read all rows of a table.

        Args:
            table: Table name, e.g. ``"Pazienti"``.

        Returns:
            Rows in storage order; an empty list for an empty table.
        """
        data = self._parser.parse_table(table)
        names = list(data.keys())
        if not names:
            return []
        count = max(len(data[name]) for name in names)
        return [{name: (data[name][i] if i < len(data[name]) else None) for name in names} for i in range(count)]
