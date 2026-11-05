"""Helpers converting the raw values used by FreeSTEP into Python types.

INI files contain only strings, the MDB returns already typed values; all
helpers accept both. They never raise: values that cannot be converted
(including ``None`` and empty strings) yield ``None``.
"""

from __future__ import annotations

from datetime import date, datetime, time


def to_int(value: object) -> int | None:
    """Convert to an integer.

    Args:
        value: Integer or its decimal string representation.

    Returns:
        The integer, or ``None`` when ``value`` is empty or not an integer.
    """
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def to_float(value: object) -> float | None:
    """Convert to a float, accepting the decimal comma (``"8,5"``).

    Args:
        value: Number or its string representation.

    Returns:
        The number, or ``None`` when ``value`` is empty or not a number.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def to_date(value: object) -> date | None:
    """Convert to a date.

    Args:
        value: :class:`~datetime.date`, :class:`~datetime.datetime` or a string
            in ``dd.mm.yyyy`` (INI files), ``yyyy-mm-dd hh:mm:ss`` (MDB),
            ``yyyy-mm-dd`` or ``dd/mm/yyyy`` format.

    Returns:
        The date, or ``None`` when ``value`` is empty or in an unknown format.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_time(value: object) -> time | None:
    """Convert to a time of day.

    Args:
        value: String in ``h:mm:ss`` (INI files) or ``hh:mm`` (MDB) format.

    Returns:
        The time, or ``None`` when ``value`` is empty or in an unknown format.
    """
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def to_bool(value: object) -> bool | None:
    """Convert a numeric flag to a boolean.

    Args:
        value: Integer flag or its string; any non-zero value (including VB6
            ``True`` = -1) is ``True``.

    Returns:
        The flag, or ``None`` when ``value`` is empty or not an integer.
    """
    number = to_int(value)
    return None if number is None else number != 0


def to_str(value: object) -> str:
    """Convert to a stripped string.

    Args:
        value: Any value.

    Returns:
        ``str(value)`` without surrounding whitespace, ``""`` for ``None``.
    """
    return "" if value is None else str(value).strip()
