"""Images of exams without pressure data, e.g. the foot scan of a podoscanner."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QComboBox, QSizePolicy, QVBoxLayout, QWidget

from ..i18n import tr
from .common import draw_readout, nice_step


class ImageView(QWidget):
    """Image of an exam without pressure data, e.g. the foot scan of a podoscanner.

    The image is fitted to the widget. When its scale is known (a scan), a scale
    bar in mm is drawn and the mouse position is shown in mm from the top-left corner.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty view.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image: QImage | None = None
        self._mm_per_pixel: float | None = None
        self._message = ""
        self._hover: QPointF | None = None
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Return the preferred size."""
        return QSize(560, 520)

    def set_image(self, image: QImage | None, message: str = "", mm_per_pixel: float | None = None) -> None:
        """Show an image, or ``message`` when it is ``None`` (or invalid).

        Args:
            image: The image.
            message: Shown instead of a missing image.
            mm_per_pixel: Size of a pixel in mm, ``None`` when the scale is unknown (no scale bar).
        """
        self._image = None if image is None or image.isNull() else image
        self._mm_per_pixel = mm_per_pixel
        self._message = message
        self.update()

    def _target_rect(self) -> QRectF:
        """Area of the image in widget coordinates (keeps the aspect ratio)."""
        assert self._image is not None
        avail = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        scale = min(avail.width() / self._image.width(), avail.height() / self._image.height())
        w, h = self._image.width() * scale, self._image.height() * scale
        return QRectF(avail.left() + (avail.width() - w) / 2, avail.top() + (avail.height() - h) / 2, w, h)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint the image, the scale bar and the position under the mouse (Qt event handler)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if self._image is None:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(self.rect()).adjusted(16, 16, -16, -16),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message or tr("No image"),
            )
            return
        rect = self._target_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(rect, self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mm = self._mm_per_pixel
        if mm is None:
            return
        px_per_mm = rect.width() / (self._image.width() * mm)
        length = nice_step(rect.width() / px_per_mm, 4)  # about a quarter of the width
        bar = QRectF(rect.left() + 12, rect.bottom() - 30, length * px_per_mm, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawRoundedRect(bar.adjusted(-6, -6, 6 + 60, 14), 3, 3)
        painter.setBrush(QColor(0, 0, 0))
        painter.drawRect(bar)
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(QPointF(bar.right() + 6, bar.bottom() + 1), f"{length:g} mm")
        hover = self._hover
        if hover is not None and rect.contains(hover):
            x = (hover.x() - rect.left()) / px_per_mm
            y = (hover.y() - rect.top()) / px_per_mm
            painter.setPen(QPen(QColor(255, 255, 255, 160), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(hover.x(), rect.top()), QPointF(hover.x(), rect.bottom()))
            painter.drawLine(QPointF(rect.left(), hover.y()), QPointF(rect.right(), hover.y()))
            draw_readout(painter, self, hover, [(f"x = {x:.1f} mm", None), (f"y = {y:.1f} mm", None)])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        """Show the position under the mouse (Qt event handler)."""
        self._hover = event.position()
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        """Hide the readout (Qt event handler)."""
        self._hover = None
        self.update()


class ImagePanel(QWidget):
    """:class:`ImageView` with a selection box when there are several images."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty panel.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.choice = QComboBox()
        self.choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.choice.currentIndexChanged.connect(self._show)
        self.view = ImageView()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.choice, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.view, 1)
        self._images: list[tuple[QImage, float | None]] = []
        self._message = ""
        self.set_images([])

    def set_images(self, images: list[tuple[str, QImage, float | None]], message: str = "") -> None:
        """Replace the images.

        Args:
            images: ``(name, image, mm per pixel or None)`` per image; the first one is shown.
            message: Shown instead when there is no image.
        """
        self._images = [(image, scale) for _, image, scale in images]
        self._message = message
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItems([name for name, _, _ in images])
        self.choice.blockSignals(False)
        self.choice.setVisible(len(images) > 1)
        self._show(0)

    def _show(self, index: int) -> None:
        """Show the image selected in the box."""
        image, scale = self._images[index] if 0 <= index < len(self._images) else (None, None)
        self.view.set_image(image, self._message, scale)
