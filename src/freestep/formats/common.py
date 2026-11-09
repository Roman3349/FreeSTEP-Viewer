"""The file header shared by the pressure recordings (``Postura.dat``, ``Statica.dat``, ``Dinamica.dat``, …)."""

from __future__ import annotations

import struct

#: Size of the common part of the file header (up to the maximum value) in bytes.
COMMON_HEADER_SIZE = 62
#: Common header fields: version, plate name (``String * 50``), columns, rows, cell width, cell height, maximum value.
COMMON_STRUCT = struct.Struct("<h50s5h")


def decode_name(raw: bytes) -> str:
    """Decode a space padded VB ``String * n``."""
    return raw.decode("cp1252").rstrip(" \x00")
