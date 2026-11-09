"""Pages of the PDF reports: A4 portrait, content flowing from page to page, and the shared blocks.

Texts and tables are rich text (:class:`~PySide6.QtGui.QTextDocument`), plots are
rendered widgets; a block that does not fit on the rest of a page starts a new one.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QRectF, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QTextDocument,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ...models import Exam, Patient
from ..i18n import tr
from ..meta import APP_NAME, ICON_PATH
from ..texts import exam_type_label, footwear_label, sex_label

#: Resolution of the PDF in dots per inch; 1 dot = 1 px of the widgets.
_DPI = 96
#: Page margin in dots.
_MARGIN = 48
#: Height reserved for the footer in dots.
_FOOTER = 24
#: Gap between blocks and between plots side by side in dots.
_GAP = 12
#: Font size of the text in points.
_FONT_PT = 9


def _print_palette() -> QPalette:
    """Black on white, independent of the (possibly dark) theme of the screen."""
    palette = QPalette(QColor("white"))
    for role, colour in (
        (QPalette.ColorRole.Window, "white"),
        (QPalette.ColorRole.Base, "white"),
        (QPalette.ColorRole.WindowText, "black"),
        (QPalette.ColorRole.Text, "black"),
    ):
        palette.setColor(role, QColor(colour))
    return palette


def draw_document(painter: QPainter, doc: QTextDocument) -> None:
    """Paint rich text at the painter's origin, black on white whatever the theme of the screen.

    :meth:`QTextDocument.drawContents` paints text without an explicit colour
    in the text colour of the application palette, which is white in a dark theme.
    """
    context = QAbstractTextDocumentLayout.PaintContext()
    context.palette = _print_palette()
    doc.documentLayout().draw(painter, context)


class Pages:
    """Paints blocks one below another and starts a new page when a block does not fit."""

    def __init__(self, path: Path, title: str, footer: str) -> None:
        """Start the PDF ``path`` with the document title and the footer text of every page."""
        self.writer = QPdfWriter(str(path))
        self.writer.setResolution(_DPI)
        self.writer.setPageLayout(
            QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF())
        )
        self.writer.setTitle(title)
        self.writer.setCreator(APP_NAME)
        self.painter = QPainter(self.writer)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.font = QFont(self.painter.font())
        self.font.setPointSizeF(_FONT_PT)
        self.width = self.writer.width() - 2 * _MARGIN
        self.bottom = self.writer.height() - _MARGIN - _FOOTER
        self.footer = footer
        self.page = 1
        self.y = float(_MARGIN)

    def ensure(self, height: float) -> None:
        """Start a new page unless ``height`` fits below the current position (an empty page always fits)."""
        if self.y + height > self.bottom and self.y > _MARGIN:
            self.new_page()

    def new_page(self) -> None:
        """Finish the current page and continue at the top of a new one."""
        self._paint_footer()
        self.writer.newPage()
        self.page += 1
        self.y = float(_MARGIN)

    def document(self, text: str, width: float | None = None) -> QTextDocument:
        """Lay out rich text for the PDF."""
        doc = QTextDocument()
        doc.documentLayout().setPaintDevice(self.writer)
        doc.setDefaultFont(self.font)
        doc.setDocumentMargin(0)
        doc.setTextWidth(width or self.width)
        doc.setHtml(text)
        return doc

    def text(self, text: str, *, keep_with: float = 0) -> None:
        """Paint rich text across the page.

        Args:
            text: HTML (the subset of :class:`QTextDocument`).
            keep_with: Height of the following block that must fit on the same page (for headings).
        """
        doc = self.document(text)
        height = doc.size().height()
        self.ensure(height + keep_with)
        self.painter.save()
        self.painter.translate(_MARGIN, self.y)
        draw_document(self.painter, doc)
        self.painter.restore()
        self.y += height + _GAP / 2

    def widgets(self, items: Sequence[tuple[str, QWidget]], height: int, columns: int) -> None:
        """Paint widgets in rows of ``columns``, each with a caption above it.

        Args:
            items: ``(caption, widget)``; the widgets are resized to their cell.
            height: Height of a widget in dots.
            columns: Widgets per row.
        """
        cell = (self.width - _GAP * (columns - 1)) / columns
        for start in range(0, len(items), columns):
            row = items[start : start + columns]
            captions = [self.document(f"<b>{html.escape(c)}</b>", cell) for c, _ in row]
            caption_height = max(d.size().height() for d in captions)
            self.ensure(caption_height + height)
            for column, ((_, widget), caption) in enumerate(zip(row, captions, strict=True)):
                x = _MARGIN + column * (cell + _GAP)
                self.painter.save()
                self.painter.translate(x, self.y)
                draw_document(self.painter, caption)
                self.painter.restore()
                widget.setPalette(_print_palette())
                widget.setFont(self.font)
                widget.resize(int(cell), height)
                widget.render(self.painter, QPoint(int(x), int(self.y + caption_height)))
            self.y += caption_height + height + _GAP

    def image(self, caption: str, image: QImage, max_height: float) -> None:
        """Paint an image centred across the page, scaled to fit the page width and ``max_height``.

        Args:
            caption: Bold caption above the image (empty: none).
            image: The image.
            max_height: Largest height in dots (the rest of an empty page is the limit too).
        """
        if image.isNull():
            return
        doc = self.document(f"<b>{html.escape(caption)}</b>") if caption else None
        caption_height = doc.size().height() if doc is not None else 0
        limit = min(max_height, self.bottom - _MARGIN - caption_height)
        scale = min(self.width / image.width(), limit / image.height())
        width, height = image.width() * scale, image.height() * scale
        self.ensure(caption_height + height)
        if doc is not None:
            self.painter.save()
            self.painter.translate(_MARGIN, self.y)
            draw_document(self.painter, doc)
            self.painter.restore()
        target = QRectF(_MARGIN + (self.width - width) / 2, self.y + caption_height, width, height)
        self.painter.save()
        self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.painter.drawImage(target, image)
        self.painter.restore()
        self.y += caption_height + height + _GAP

    def logo(self, size: float) -> None:
        """Paint the application logo with its name below in the top right corner of the page."""
        right = _MARGIN + self.width
        QSvgRenderer(str(ICON_PATH)).render(self.painter, QRectF(right - size, _MARGIN, size, size))
        self.painter.save()
        self.painter.setFont(self.font)
        self.painter.setPen(QColor(100, 100, 100))
        # One word per line, centred under the logo
        name = QRectF(right - size * 1.5, _MARGIN + size + 2, size * 2, 2 * _FOOTER)
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        self.painter.drawText(name, align, APP_NAME.replace(" ", "\n"))
        self.painter.restore()

    def finish(self) -> None:
        """Paint the footer of the last page and close the file."""
        self._paint_footer()
        self.painter.end()

    def _paint_footer(self) -> None:
        """Paint the footer line: exam on the left, page number on the right."""
        top = self.writer.height() - _MARGIN - _FOOTER / 2
        rect = QRectF(_MARGIN, top, self.width, _FOOTER)
        self.painter.save()
        self.painter.setFont(self.font)
        self.painter.setPen(QColor(128, 128, 128))
        self.painter.drawLine(rect.topLeft(), rect.topRight())
        text_rect = rect.adjusted(0, 4, 0, 0)
        self.painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, self.footer)
        self.painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight, tr("Page {number}", number=self.page))
        self.painter.restore()


def age(patient: Patient, exam: Exam) -> int | None:
    """Age at the exam in years: as stored with the exam, else from the birth date."""
    if exam.age is not None:
        return exam.age
    born, day = patient.birth_date, exam.exam_date
    if born is None or day is None:
        return None
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def cell(value: object) -> str:
    """Escape a value for a table cell, empty values as a dash."""
    return "–" if value is None or value == "" else html.escape(str(value))


def info_table(rows: list[tuple[str, object]]) -> str:
    """Two-column table of labels (grey) and values."""
    body = "".join(
        f"<tr><td style='color:#666666; padding-right:10px'>{html.escape(k)}</td><td>{cell(v)}</td></tr>"
        for k, v in rows
    )
    return f"<table cellspacing='0' cellpadding='1'>{body}</table>"


def section(title: str) -> str:
    """Heading of a report section."""
    return f"<h2 style='margin-top:6px'>{html.escape(title)}</h2>"


def notes_html(items: list[str]) -> str:
    """Grey small paragraphs."""
    return "".join(f"<p style='color:#555555; font-size:small'>{html.escape(n)}</p>" for n in items)


def exam_when(exam: Exam) -> str:
    """Date and time of an exam."""
    time = exam.exam_time.strftime("%H:%M") if exam.exam_time else None
    return " ".join(str(v) for v in (exam.exam_date, time) if v)


def paint_header(pages: Pages, patient: Patient, exam: Exam, recording: list[tuple[str, object]]) -> None:
    """Subject and exam data side by side, then the note of the exam.

    Args:
        pages: The report.
        patient: The subject.
        exam: The exam.
        recording: Further rows of the exam table (e.g. the acquisitions and the plate).
    """
    height = exam.height_cm or patient.height_cm
    weight = exam.weight_kg or patient.weight_kg
    left = info_table(
        [
            (tr("Name"), patient.name),
            (tr("Born"), patient.birth_date),
            (tr("Age at exam"), age(patient, exam)),
            (tr("Sex"), sex_label(patient.sex)),
            (tr("Height"), f"{height:g} cm" if height else None),
            (tr("Weight"), f"{weight:g} kg" if weight else None),
        ]
    )
    right = info_table(
        [
            (tr("ID"), exam.id),
            (tr("Date"), exam_when(exam)),
            (tr("Type"), exam_type_label(exam)),
            (tr("Footwear"), footwear_label(exam)),
            *recording,
        ]
    )
    pages.text(
        f"<table width='100%' cellspacing='0'><tr><td width='50%' valign='top'><b>{html.escape(tr('Subject'))}</b>"
        f"{left}</td><td width='50%' valign='top'><b>{html.escape(tr('Exam'))}</b>{right}</td></tr></table>"
    )
    if exam.note:
        pages.text(f"<p><b>{html.escape(tr('Note'))}:</b> {html.escape(exam.note)}</p>")
