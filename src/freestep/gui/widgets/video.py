"""Player of the video recorded with an exam (Qt Multimedia)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

#: Largest difference between the video and the pressure recording during playback before the video is moved.
_MAX_DRIFT_MS = 150


class VideoPlayer(QWidget):
    """Video with play / pause, a position slider and the time; the picture can be rotated.

    The video can follow the pressure recording (:meth:`follow`): the recording time ``t``
    is shown at video frame ``start frame + t × frame rate``. The start frame comes from
    ``VideoTrack*.dat``, the frame rate is the one of the video file unless the user
    corrects it (some videos store a wrong one).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty player.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._rotation = 0
        self._item = QGraphicsVideoItem()
        self._item.nativeSizeChanged.connect(self._fit)
        scene = QGraphicsScene(self)
        scene.addItem(self._item)
        self._view = QGraphicsView(scene)
        self._view.setBackgroundBrush(QColor("black"))
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._screen = QStackedLayout()
        self._screen.addWidget(self._view)
        self._screen.addWidget(self._message)

        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self._item)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(lambda _error, text: self._show_message(text))

        self._play = QToolButton()
        self._play.setText("▶")
        self._play.setToolTip(tr("Play / pause"))
        self._play.clicked.connect(self.toggle)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.sliderMoved.connect(self._player.setPosition)
        self._time = QLabel("–")
        controls = QHBoxLayout()
        controls.addWidget(self._play)
        controls.addWidget(self._slider, 1)
        controls.addWidget(self._time)

        self._file_rate = 0.0  # frames per second of the video file (0: not known yet)
        self._frame_count: int | None = None
        self.sync = QCheckBox(tr("Follow the pressure recording"))
        self.sync.setChecked(True)
        self.sync.setToolTip(tr("Show the video at the time of the shown pressure frame and play it along"))
        self.sync_start = QSpinBox()
        self.sync_start.setRange(0, 1_000_000)
        self.sync_start.setPrefix(tr("start frame "))
        self.sync_start.setToolTip(tr("Video frame at the start of the pressure recording"))
        self.sync_rate = QDoubleSpinBox()
        self.sync_rate.setRange(1, 1000)
        self.sync_rate.setDecimals(1)
        self.sync_rate.setSuffix(tr(" fps"))
        self.sync_rate.setToolTip(tr("Real frame rate of the video; correct it when the video runs too fast or slow"))
        self._sync_row = QWidget()
        sync_layout = QHBoxLayout(self._sync_row)
        sync_layout.setContentsMargins(0, 0, 0, 0)
        sync_layout.addWidget(self.sync)
        sync_layout.addWidget(self.sync_start)
        sync_layout.addWidget(self.sync_rate)
        sync_layout.addStretch(1)
        self.sync.toggled.connect(self._update_sync_controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._screen, 1)
        layout.addLayout(controls)
        layout.addWidget(self._sync_row)
        self.set_video(None)

    def set_video(
        self,
        path: Path | None,
        rotation: int = 0,
        message: str = "",
        *,
        sync_start: int | None = None,
        frame_count: int | None = None,
    ) -> None:
        """Show a video file (stopped at the start), or ``message`` when there is none.

        Args:
            path: The video file; it must exist as long as it is shown.
            rotation: Clockwise rotation of the picture in degrees.
            message: Shown instead of a missing video.
            sync_start: Video frame at the start of the pressure recording; ``None``
                when the video cannot follow a pressure recording.
            frame_count: Number of frames of the video (for its frame rate), if known.
        """
        self._player.stop()
        self._player.setPlaybackRate(1.0)
        self._rotation = rotation
        self._file_rate = 0.0
        self._frame_count = frame_count
        self._sync_row.setVisible(path is not None and sync_start is not None)
        self.sync_start.setValue(sync_start or 0)
        self._update_sync_controls()
        self._player.setSource(QUrl.fromLocalFile(str(path)) if path is not None else QUrl())
        for widget in (self._play, self._slider):
            widget.setEnabled(path is not None)
        if path is None:
            self._show_message(message or tr("No video"))
        else:
            self._screen.setCurrentWidget(self._view)

    def stop(self) -> None:
        """Stop the playback (e.g. before the file is replaced)."""
        self._player.stop()

    @property
    def following(self) -> bool:
        """The video follows the pressure recording."""
        return self._sync_row.isVisible() and self.sync.isChecked() and self._file_rate > 0

    def follow(self, seconds: float, *, playing: bool = False, speed: float = 1.0) -> None:
        """Show the video at a time of the pressure recording (see the class description).

        Args:
            seconds: Time since the first pressure frame.
            playing: The pressure recording is being played; the video plays along.
            speed: Playback speed of the pressure recording (1 = real time).
        """
        if not self.following:
            return
        frame = self.sync_start.value() + seconds * self.sync_rate.value()
        target = max(0, min(round(frame / self._file_rate * 1000), self._player.duration()))
        if not playing:
            self._player.pause()
            self._player.setPosition(target)
            return
        self._player.setPlaybackRate(speed * self.sync_rate.value() / self._file_rate)
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.setPosition(target)
            self._player.play()
        elif abs(self._player.position() - target) > _MAX_DRIFT_MS:
            self._player.setPosition(target)

    def pause(self) -> None:
        """Pause the playback (e.g. when the video is hidden)."""
        self._player.pause()

    def toggle(self) -> None:
        """Play or pause (in real time)."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.setPlaybackRate(1.0)
            self._player.play()

    def _update_sync_controls(self) -> None:
        """Enable the start frame and the frame rate only when the video follows the pressure."""
        for widget in (self.sync_start, self.sync_rate):
            widget.setEnabled(self.sync.isChecked())

    def _show_message(self, text: str) -> None:
        """Show a text instead of the picture."""
        self._message.setText(text)
        self._screen.setCurrentWidget(self._message)

    def _fit(self, size: QSizeF | None = None) -> None:
        """Rotate the picture around its centre and fit it into the view."""
        size = self._item.nativeSize() if size is None else size
        if size.isEmpty():
            return
        self._item.setSize(size)
        self._item.setTransformOriginPoint(size.width() / 2, size.height() / 2)
        self._item.setRotation(self._rotation)
        bounds = self._item.sceneBoundingRect()
        self._view.setSceneRect(bounds)
        self._view.fitInView(QRectF(bounds), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt API)
        """Keep the picture fitted (Qt event handler)."""
        super().resizeEvent(event)
        self._fit()

    def _on_duration(self, duration: int) -> None:
        """Set the range of the position slider and the frame rate of the file."""
        self._slider.setRange(0, duration)
        if duration > 0:
            rate = self._player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
            if self._frame_count:
                rate = self._frame_count / (duration / 1000)
            self._file_rate = float(rate) if rate else 25.0
            self.sync_rate.setValue(self._file_rate)
        self._on_position(self._player.position())

    def _on_position(self, position: int) -> None:
        """Move the slider and show the time."""
        if not self._slider.isSliderDown():
            self._slider.setValue(position)
        self._time.setText(f"{position / 1000:.1f} / {self._player.duration() / 1000:.1f} s")

    def _on_state(self, state: QMediaPlayer.PlaybackState) -> None:
        """Show play or pause on the button."""
        self._play.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")
