"""Tolerant parser for the INI files written by FreeSTEP (VB6 application).

The files are not always well formed, e.g. ``PazienteW.ini`` contains lines such as
``NomeFile00000158.fre`` (missing ``=``) or ``ShoeTag=WShoeNum=8,5`` (missing line
break). ``configparser`` cannot cope with that, so we use a small custom parser.
"""

from __future__ import annotations

import re

#: Keys known to appear in FreeSTEP INI files. Used to repair malformed lines.
KNOWN_KEYS: frozenset[str] = frozenset(
    {
        # Indice.ini
        "Codice",
        "DataCreazione",
        "Pazienti",
        "Esami",
        # Filtro.ini
        "Filtro",
        "Freq",
        # Paziente.ini / PazienteW.ini
        "LangID",
        "ID",
        "SSID",
        "CodiceLibero",
        "RemotoCentro",
        "RemotoID",
        "TipoEsame",
        "FlagCalz",
        "IDEsame",
        "NomeFile",
        "DataEsame",
        "OraEsame",
        "SSIDEsame",
        "Altezza_Esame",
        "Peso_Esame",
        "Calzatura_Esame",
        "Metrica_Esame",
        "AltezzaINC_Esame",
        "PesoLB_Esame",
        "ShoeTag_Esame",
        "ShoeNum_Esame",
        "Nota_Esame",
        "Nominativo",
        "DataNascita",
        "Altezza",
        "Peso",
        "Calzatura",
        "Metrica",
        "PesoLB",
        "AltezzaINC",
        "ShoeTag",
        "ShoeNum",
        "Indirizzo",
        "Cap",
        "Citta",
        "PR",
        "Telefono",
        "Fax",
        "Email",
        "Cellulare",
        "CodiceFiscale",
        "TesseraSanitaria",
        "Professione",
        "Medico",
        "Sesso",
        "Note",
        "Patologia",
        "Prescrizione",
    }
)

# Longest keys first, so that e.g. "ShoeNum_Esame" wins over "ShoeNum".
_KEYS_BY_LENGTH = sorted(KNOWN_KEYS, key=len, reverse=True)
# A known key followed by "=" inside a value, i.e. a missing line break.
_GLUED_KEY_RE = re.compile(r"(?<=[^=])(" + "|".join(re.escape(k) for k in _KEYS_BY_LENGTH) + r")=")

#: Parsed INI file: ``{section: {key: value}}``, all values as strings.
IniData = dict[str, dict[str, str]]

#: ANSI code page of the Windows machine that created the export. The VB6
#: application writes ``Paziente.ini`` in it (Czech Windows = cp1250), while
#: ``PazienteW.ini`` is UTF-8 with BOM.
DEFAULT_ANSI_ENCODING = "cp1250"


def decode_text(data: bytes, ansi_encoding: str = DEFAULT_ANSI_ENCODING) -> str:
    """Decode the content of an INI file.

    Tries UTF-8 with BOM, then plain UTF-8, and falls back to the ANSI code page.

    Args:
        data: Raw file content.
        ansi_encoding: Code page used when the content is not valid UTF-8.

    Returns:
        The decoded text (undecodable bytes are replaced by U+FFFD).
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(ansi_encoding, errors="replace")


def _split_line(line: str) -> list[tuple[str, str]]:
    """Split one ``key=value`` line into one or more key/value pairs.

    Repairs the malformed lines described in the module docstring using
    :data:`KNOWN_KEYS`.

    Args:
        line: One stripped, non-empty line that is not a section header.

    Returns:
        The key/value pairs; a line without ``=`` and without a known key
        prefix yields ``[(line, "")]``.
    """
    if "=" not in line:
        # Missing "=", e.g. "NomeFile00000158.fre"
        for key in _KEYS_BY_LENGTH:
            if line.startswith(key):
                return [(key, line[len(key) :])]
        return [(line, "")]

    key, value = line.split("=", 1)
    pairs: list[tuple[str, str]] = []
    # Missing line break, e.g. "ShoeTag=WShoeNum=8,5"
    while (match := _GLUED_KEY_RE.search(value)) is not None:
        pairs.append((key, value[: match.start()]))
        key, value = match.group(1), value[match.end() :]
    pairs.append((key, value))
    return pairs


def parse_ini(data: bytes | str, ansi_encoding: str = DEFAULT_ANSI_ENCODING) -> IniData:
    """Parse INI content into ``{section: {key: value}}``.

    Keys outside of any section are stored under the empty section name ``""``.
    Values are kept as raw strings; see :mod:`freestep.models` for typed access.
    Lines starting with ``;`` or ``#`` are comments. Later duplicate keys win.

    Args:
        data: File content as bytes (decoded by :func:`decode_text`) or text.
        ansi_encoding: Code page for files that are not UTF-8.

    Returns:
        The parsed sections.
    """
    text = decode_text(data, ansi_encoding) if isinstance(data, bytes) else data
    result: IniData = {}
    section = result.setdefault("", {})
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = result.setdefault(line[1:-1].strip(), {})
            continue
        for key, value in _split_line(line):
            section[key.strip()] = value.strip()
    if not result[""]:
        del result[""]
    return result
