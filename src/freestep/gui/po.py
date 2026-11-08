"""Minimal gettext PO/POT support without third-party dependencies.

Covers what the viewer and Weblate need: reading and writing ``.po``/``.pot``
files (contexts, plurals, flags, comments), compiling a catalogue to the binary
MO format for :class:`gettext.GNUTranslations`, extracting the texts passed to
``tr()``/``ntr()`` from the sources and merging a template into translations
(like ``msgmerge``).

Lines are never wrapped, so files written here and by Weblate stay identical
when Weblate's "Customize gettext output" add-on is set to not wrap lines.

Command line (run from anywhere)::

    python -m freestep.gui.po update   # regenerate freestep.pot, merge it into *.po
    python -m freestep.gui.po check    # exit code 1 when the files are outdated
"""

from __future__ import annotations

import ast
import gettext
import io
import string
import struct
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Directory with ``freestep.pot`` and the ``<language>.po`` files.
LOCALE_DIR = Path(__file__).with_name("locale")
#: Name of the template file (gettext domain).
DOMAIN = "freestep"
#: Path of the template.
TEMPLATE = LOCALE_DIR / f"{DOMAIN}.pot"

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}


@dataclass
class Entry:
    """One message of a catalogue.

    Attributes:
        msgid: Source text (singular); ``""`` is the header entry.
        msgstr: Translation (unused for plural entries).
        msgid_plural: Source text of the plural, ``None`` for non-plural entries.
        msgstr_plural: Plural translations by form index.
        msgctxt: Disambiguating context, ``None`` when absent.
        flags: Flags such as ``fuzzy`` or ``python-brace-format`` (``#,`` line).
        translator_comments: Comments written by translators (``#`` lines).
        extracted_comments: Comments for translators from the sources (``#.`` lines).
        references: Source locations ``file:line`` (``#:`` lines).
    """

    msgid: str
    msgstr: str = ""
    msgid_plural: str | None = None
    msgstr_plural: dict[int, str] = field(default_factory=dict)
    msgctxt: str | None = None
    flags: list[str] = field(default_factory=list)
    translator_comments: list[str] = field(default_factory=list)
    extracted_comments: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str | None, str]:
        """Identity of the message: ``(msgctxt, msgid)``."""
        return self.msgctxt, self.msgid

    @property
    def fuzzy(self) -> bool:
        """``True`` when the translation needs review (and is not used at runtime)."""
        return "fuzzy" in self.flags

    @property
    def translated(self) -> bool:
        """``True`` when every form is translated."""
        if self.msgid_plural is None:
            return self.msgstr != ""
        return bool(self.msgstr_plural) and all(self.msgstr_plural.values())


@dataclass
class Catalog:
    """A parsed PO or POT file.

    Attributes:
        header: Header fields (``Name: value`` lines of the ``msgid ""`` entry), in order.
        header_comments: Comment lines above the header entry, without the leading ``#``.
        entries: Messages in file order, without the header.
    """

    header: dict[str, str] = field(default_factory=dict)
    header_comments: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    def find(self, msgid: str, msgctxt: str | None = None) -> Entry | None:
        """Find a message.

        Args:
            msgid: Source text.
            msgctxt: Context, ``None`` for messages without context.

        Returns:
            The entry, or ``None``.
        """
        return next((e for e in self.entries if e.key == (msgctxt, msgid)), None)


# -- reading -------------------------------------------------------------------


def _unquote(token: str, where: str) -> str:
    """Decode one quoted PO string token (``"..."``) with its escapes."""
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise ValueError(f"{where}: expected a quoted string, got {token!r}")
    out: list[str] = []
    chars = iter(token[1:-1])
    for char in chars:
        if char == "\\":
            escaped = next(chars, "")
            if escaped not in _UNESCAPES:
                raise ValueError(f"{where}: unknown escape \\{escaped}")
            out.append(_UNESCAPES[escaped])
        else:
            out.append(char)
    return "".join(out)


def parse_po(text: str) -> Catalog:  # noqa: PLR0912 — line-oriented state machine
    """Parse the content of a PO or POT file.

    Obsolete entries (``#~``) are skipped.

    Args:
        text: File content.

    Returns:
        The catalogue.

    Raises:
        ValueError: The content is not valid PO syntax.
    """
    catalog = Catalog()
    entries: list[Entry] = []
    entry = Entry(msgid="")
    started = False  # a msgid/msgctxt was seen for the current entry
    field_name: str | None = None
    plural_index = 0

    def finish() -> None:
        nonlocal entry, started, field_name
        if started:
            entries.append(entry)
        entry, started, field_name = Entry(msgid=""), False, None

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        where = f"line {number}"
        if not line:
            finish()
            continue
        if line.startswith("#~"):
            continue
        if line.startswith("#"):
            if started:  # comments start a new entry
                finish()
            kind, rest = line[:2], line[2:].strip()
            if kind == "#,":
                entry.flags.extend(f.strip() for f in rest.split(",") if f.strip())
            elif kind == "#.":
                entry.extracted_comments.append(rest)
            elif kind == "#:":
                entry.references.extend(rest.split())
            elif kind == "#|":
                pass  # previous msgid of a fuzzy entry, not needed
            else:
                entry.translator_comments.append(line[1:].removeprefix(" "))
            continue
        if line.startswith('"'):
            if field_name is None:
                raise ValueError(f"{where}: string continuation without a keyword")
            value = _unquote(line, where)
        else:
            keyword, _, rest = line.partition(" ")
            value = _unquote(rest.strip(), where)
            if keyword in ("msgctxt", "msgid") and started and field_name not in ("msgctxt", None):
                finish()
            if keyword.startswith("msgstr["):
                plural_index = int(keyword[7:-1])
                keyword = "msgstr_plural"
            if keyword not in ("msgctxt", "msgid", "msgid_plural", "msgstr", "msgstr_plural"):
                raise ValueError(f"{where}: unknown keyword {keyword!r}")
            field_name = keyword
            started = True
            if keyword == "msgctxt":
                entry.msgctxt = ""
            elif keyword == "msgid_plural":
                entry.msgid_plural = ""
            elif keyword == "msgstr_plural":
                entry.msgstr_plural[plural_index] = ""
        if field_name == "msgstr_plural":
            entry.msgstr_plural[plural_index] += value
        elif field_name == "msgctxt":
            entry.msgctxt = (entry.msgctxt or "") + value
        elif field_name == "msgid_plural":
            entry.msgid_plural = (entry.msgid_plural or "") + value
        else:
            assert field_name is not None
            setattr(entry, field_name, getattr(entry, field_name) + value)
    finish()

    for item in entries:
        if item.key == (None, ""):
            for header_line in item.msgstr.splitlines():
                name, _, value = header_line.partition(":")
                if name.strip():
                    catalog.header[name.strip()] = value.strip()
            catalog.header_comments = item.translator_comments
        else:
            catalog.entries.append(item)
    return catalog


def read_po(path: Path) -> Catalog:
    """Read a PO or POT file (UTF-8).

    Args:
        path: File to read.

    Returns:
        The catalogue.
    """
    return parse_po(path.read_text(encoding="utf-8"))


# -- writing -------------------------------------------------------------------


def _quote(text: str) -> str:
    """Encode a string as a quoted PO token."""
    return '"' + "".join(_ESCAPES.get(char, char) for char in text) + '"'


def _field(keyword: str, value: str) -> list[str]:
    r"""Format one field; multi-line values are split after each ``\\n`` like gettext does."""
    parts = value.splitlines(keepends=True)
    if len(parts) <= 1:
        return [f"{keyword} {_quote(value)}"]
    return [f'{keyword} ""', *(_quote(part) for part in parts)]


def write_po(catalog: Catalog) -> str:
    """Serialise a catalogue to PO syntax (no line wrapping).

    Args:
        catalog: The catalogue.

    Returns:
        File content ending with a newline.
    """
    lines = [f"# {c}".rstrip() for c in catalog.header_comments]
    header = "".join(f"{name}: {value}\n" for name, value in catalog.header.items())
    lines += ['msgid ""', *_field("msgstr", header)]
    for entry in catalog.entries:
        lines.append("")
        lines += [f"# {c}".rstrip() for c in entry.translator_comments]
        lines += [f"#. {c}" for c in entry.extracted_comments]
        lines += [f"#: {ref}" for ref in entry.references]
        if entry.flags:
            lines.append("#, " + ", ".join(entry.flags))
        if entry.msgctxt is not None:
            lines += _field("msgctxt", entry.msgctxt)
        lines += _field("msgid", entry.msgid)
        if entry.msgid_plural is not None:
            lines += _field("msgid_plural", entry.msgid_plural)
            forms = entry.msgstr_plural or {0: "", 1: ""}
            for index in sorted(forms):
                lines += _field(f"msgstr[{index}]", forms[index])
        else:
            lines += _field("msgstr", entry.msgstr)
    return "\n".join(lines) + "\n"


# -- compiling -----------------------------------------------------------------


def compile_mo(catalog: Catalog) -> bytes:
    """Compile a catalogue to the binary MO format.

    Fuzzy and untranslated messages are left out, so their source text is used.

    Args:
        catalog: The catalogue.

    Returns:
        MO file content (little-endian, without hash table).
    """
    # Messages are always encoded as UTF-8, so the header must say so
    fields = {**catalog.header, "Content-Type": "text/plain; charset=UTF-8"}
    header = "".join(f"{name}: {value}\n" for name, value in fields.items())
    messages: dict[bytes, bytes] = {b"": header.encode()}
    for entry in catalog.entries:
        if entry.fuzzy or not entry.translated:
            continue
        msgid = entry.msgid if entry.msgid_plural is None else f"{entry.msgid}\0{entry.msgid_plural}"
        if entry.msgctxt is not None:
            msgid = f"{entry.msgctxt}\x04{msgid}"
        if entry.msgid_plural is None:
            msgstr = entry.msgstr
        else:
            msgstr = "\0".join(entry.msgstr_plural[i] for i in sorted(entry.msgstr_plural))
        messages[msgid.encode()] = msgstr.encode()

    keys = sorted(messages)
    count = len(keys)
    originals_offset = 7 * 4
    translations_offset = originals_offset + count * 8
    data_offset = translations_offset + count * 8
    originals, translations, data = [], [], io.BytesIO()
    for key in keys:
        originals.append((len(key), data_offset + data.tell()))
        data.write(key + b"\0")
    for key in keys:
        value = messages[key]
        translations.append((len(value), data_offset + data.tell()))
        data.write(value + b"\0")
    out = io.BytesIO()
    out.write(struct.pack("<7I", 0x950412DE, 0, count, originals_offset, translations_offset, 0, data_offset))
    for length, offset in originals + translations:
        out.write(struct.pack("<2I", length, offset))
    out.write(data.getvalue())
    return out.getvalue()


def load_translations(path: Path) -> gettext.GNUTranslations:
    """Load a PO file as :mod:`gettext` translations (plural rules from its ``Plural-Forms`` header).

    Args:
        path: The ``.po`` file.

    Returns:
        Translations usable with ``gettext()``, ``ngettext()`` and ``pgettext()``.
    """
    return gettext.GNUTranslations(io.BytesIO(compile_mo(read_po(path))))


# -- extraction and merging ------------------------------------------------------


def placeholders(text: str) -> set[str]:
    """Return the names of the ``str.format`` placeholders in ``text`` (e.g. ``{"id"}``)."""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError:
        return set()


def extract(paths: Iterable[Path], root: Path) -> Catalog:
    """Extract the literal texts passed to ``tr()`` and ``ntr()`` into a template.

    ``tr(text, ...)`` gives a simple message, ``ntr(singular, plural, n, ...)`` a
    plural one. Texts with ``{placeholders}`` get the ``python-brace-format``
    flag, so Weblate checks the placeholders.

    Args:
        paths: Python source files.
        root: Directory the references (source file paths) are relative to.

    Returns:
        The template, messages in order of first occurrence.
    """
    catalog = Catalog()
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "ntr")
        ]
        for call in sorted(calls, key=lambda c: (c.lineno, c.col_offset)):
            texts = [a.value for a in call.args[:2] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            plural = call.func.id == "ntr"  # type: ignore[attr-defined]
            if not texts or (plural and len(texts) < 2):
                continue
            # File only, like ``xgettext --add-location=file``: line numbers would
            # change the catalogues with every unrelated edit.
            reference = path.relative_to(root).as_posix()
            entry = catalog.find(texts[0])
            if entry is None:
                entry = Entry(msgid=texts[0], msgid_plural=texts[1] if plural else None)
                if any(placeholders(t) for t in texts):
                    entry.flags.append("python-brace-format")
                catalog.entries.append(entry)
            if reference not in entry.references:
                entry.references.append(reference)
    return catalog


def merge(template: Catalog, translation: Catalog) -> Catalog:
    """Update a translation to a new template (like ``msgmerge --no-fuzzy-matching``).

    Translations, translator comments and the ``fuzzy`` flag of messages still
    in the template are kept; new messages are empty, obsolete ones are dropped.

    Args:
        template: The new template.
        translation: The existing translation.

    Returns:
        The merged translation with the header of ``translation``.
    """
    merged = Catalog(header=dict(translation.header), header_comments=list(translation.header_comments))
    for source in template.entries:
        old = translation.find(source.msgid, source.msgctxt)
        entry = Entry(
            msgid=source.msgid,
            msgid_plural=source.msgid_plural,
            msgctxt=source.msgctxt,
            flags=list(source.flags),
            extracted_comments=list(source.extracted_comments),
            references=list(source.references),
        )
        if old is not None and old.msgid_plural == source.msgid_plural:
            entry.msgstr, entry.msgstr_plural = old.msgstr, dict(old.msgstr_plural)
            entry.translator_comments = list(old.translator_comments)
            if old.fuzzy:
                entry.flags.insert(0, "fuzzy")
        merged.entries.append(entry)
    return merged


def _template_header(previous: Catalog | None, template: Catalog) -> dict[str, str]:
    """Header of the template; the creation date only changes when the messages change."""
    unchanged = previous is not None and write_po(Catalog(entries=previous.entries)) == write_po(
        Catalog(entries=template.entries)
    )
    created = (
        previous.header.get("POT-Creation-Date", "")
        if unchanged and previous is not None
        else datetime.now(UTC).strftime("%Y-%m-%d %H:%M+0000")
    )
    return {
        "Project-Id-Version": "FreeStepViewer",
        "Report-Msgid-Bugs-To": "",
        "POT-Creation-Date": created,
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
    }


def build_files() -> dict[Path, str]:
    """Compute the up-to-date content of the template and of every translation.

    Returns:
        ``{path: content}`` for ``freestep.pot`` and each ``*.po`` in :data:`LOCALE_DIR`.
    """
    package_root = LOCALE_DIR.parent.parent.parent  # the "src" directory
    template = extract(sorted(LOCALE_DIR.parent.rglob("*.py")), package_root)
    previous = read_po(TEMPLATE) if TEMPLATE.exists() else None
    template.header = _template_header(previous, template)
    template.header_comments = [
        "Template of the FreeStepViewer GUI texts. Generated by: python -m freestep.gui.po update"
    ]
    files = {TEMPLATE: write_po(template)}
    for path in sorted(LOCALE_DIR.glob("*.po")):
        files[path] = write_po(merge(template, read_po(path)))
    return files


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (``update`` or ``check``).

    Args:
        argv: Arguments without the program name; ``None`` uses ``sys.argv``.

    Returns:
        Process exit code; ``check`` returns 1 when a file is outdated.
    """
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "update"
    files = build_files()
    outdated = [p for p, content in files.items() if not p.exists() or p.read_text(encoding="utf-8") != content]
    if command == "check":
        for path in outdated:
            print(f"outdated: {path}")
        return 1 if outdated else 0
    if command != "update":
        print(f"usage: python -m freestep.gui.po [update|check], not {command!r}", file=sys.stderr)
        return 2
    LOCALE_DIR.mkdir(exist_ok=True)
    for path in outdated:
        path.write_text(files[path], encoding="utf-8", newline="\n")
        print(f"updated: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
