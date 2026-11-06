"""Create the anonymised test exam ``00000160.fre`` from the original FreeSTEP export.

The patient's name, birth date, body measurements and the SSID identifiers in
``Paziente.ini`` / ``PazienteW.ini`` are replaced; ``Postura.dat`` is kept as it
is. The archive is written with ZipCrypto and the FreeSTEP password like the
original, so the tests exercise the same decryption path.

Usage (the export is not part of the repository)::

    uv run python tests/data/anonymize_fre.py FreeStep_Export_2025-03-17_10-52.zip 160 tests/data/00000160.fre
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path

from freestep import DEFAULT_PASSWORD, FreeStepExport
from freestep.ini import decode_text

#: Replaced INI values (the same keys in ``Paziente.ini`` and ``PazienteW.ini``).
REPLACEMENTS = {
    "Nominativo": "Testovací Pacient",
    "DataNascita": "01.01.1990",
    "SSID": "00000-0000000000000",
    "SSIDEsame": "00000-0000000000001",
    "Altezza": "180",
    "Altezza_Esame": "180",
    "AltezzaINC": "71",
    "AltezzaINC_Esame": "71",
    "Peso": "75",
    "Peso_Esame": "75",
    "PesoLB": "165",
    "PesoLB_Esame": "165",
    "Calzatura": "43",
    "Calzatura_Esame": "43",
    "ShoeNum": "10,5",
    "ShoeNum_Esame": "10,5",
}


def _crc_table() -> list[int]:
    """CRC-32 lookup table (the cipher needs the single-byte CRC step)."""
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC_TABLE = _crc_table()


class _ZipCrypto:
    """Traditional PKWARE (ZipCrypto) stream cipher, encryption direction."""

    def __init__(self, password: bytes) -> None:
        self.keys = [0x12345678, 0x23456789, 0x34567890]
        for byte in password:
            self._update(byte)

    def _update(self, byte: int) -> None:
        k0, k1, k2 = self.keys
        k0 = (k0 >> 8) ^ _CRC_TABLE[(k0 ^ byte) & 0xFF]
        k1 = ((k1 + (k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        k2 = (k2 >> 8) ^ _CRC_TABLE[(k2 ^ (k1 >> 24)) & 0xFF]
        self.keys = [k0, k1, k2]

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt ``data`` (the cipher state continues across calls)."""
        out = bytearray(len(data))
        for i, byte in enumerate(data):
            temp = (self.keys[2] | 2) & 0xFFFF
            out[i] = byte ^ (((temp * (temp ^ 1)) >> 8) & 0xFF)
            self._update(byte)
        return bytes(out)


def anonymize_ini(data: bytes) -> bytes:
    """Replace the identifying values of an INI file, keeping its encoding, BOM and line ends."""
    bom = data.startswith(b"\xef\xbb\xbf")
    text = decode_text(data, "cp1250")
    lines = []
    for line in text.split("\r\n"):
        key, sep, _ = line.partition("=")
        lines.append(f"{key}={REPLACEMENTS[key]}" if sep and key in REPLACEMENTS else line)
    # FreeSTEP sometimes omits a line break ("ShoeTag=WShoeNum=11,5"); the parser repairs it, so keep it
    result = re.sub(r"(?<=.)(ShoeNum(?:_Esame)?)=[\d,.]*", lambda m: f"{m[1]}={REPLACEMENTS[m[1]]}", "\r\n".join(lines))
    return b"\xef\xbb\xbf" + result.encode() if bom else result.encode("cp1250")


def write_encrypted_zip(path: Path, members: list[tuple[str, bytes, tuple[int, ...]]], password: bytes) -> None:
    """Write a deflated, ZipCrypto encrypted ZIP archive.

    Args:
        path: Output file.
        members: ``(name, content, date_time)`` per member.
        password: Encryption password.
    """
    local, central = bytearray(), bytearray()
    for name, content, date_time in members:
        crc = zlib.crc32(content)
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        compressed = compressor.compress(content) + compressor.flush()
        cipher = _ZipCrypto(password)
        # 12 byte header: random bytes, the last one is the check byte (high byte of the CRC)
        encrypted = cipher.encrypt(os.urandom(11) + bytes([crc >> 24])) + cipher.encrypt(compressed)
        year, month, day, hour, minute, second = date_time[:6]
        dos_time = hour << 11 | minute << 5 | second // 2
        dos_date = (year - 1980) << 9 | month << 5 | day
        raw_name = name.encode()
        common = struct.pack(
            "<HHHHHIIIH", 20, 1, 8, dos_time, dos_date, crc, len(encrypted), len(content), len(raw_name)
        )
        offset = len(local)
        local += b"PK\x03\x04" + common + b"\x00\x00" + raw_name + encrypted
        central += b"PK\x01\x02" + b"\x14\x00" + common + struct.pack("<HHHHII", 0, 0, 0, 0, 0, offset) + raw_name
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(members), len(members), len(central), len(local), 0)
    path.write_bytes(bytes(local + central + end))


def main() -> None:
    """Extract one exam from an export and write it anonymised."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", help="FreeSTEP export ZIP")
    parser.add_argument("exam", type=int, help="exam ID")
    parser.add_argument("output", type=Path, help="output .fre file")
    args = parser.parse_args()
    members: list[tuple[str, bytes, tuple[int, ...]]] = []
    with FreeStepExport(args.export) as export, tempfile.TemporaryDirectory() as tmp:
        exam = next(e for e in export.exams if e.id == args.exam)
        with zipfile.ZipFile(export.extract_fre(exam.file_name, tmp)) as source:
            source.setpassword(DEFAULT_PASSWORD.encode())
            for info in source.infolist():
                content = source.read(info)
                if info.filename.lower().endswith(".ini"):
                    content = anonymize_ini(content)
                members.append((info.filename, content, info.date_time))
    write_encrypted_zip(args.output, members, DEFAULT_PASSWORD.encode())
    print(f"Written {args.output}")


if __name__ == "__main__":
    main()
