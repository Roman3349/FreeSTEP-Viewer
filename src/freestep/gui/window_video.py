"""The video recorded with an exam, shown in the Video tab and following the pressure playback."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Slot

from .exam import LoadedExam
from .window_base import WindowBase


class VideoMixin(WindowBase):
    """The video recorded with an exam, shown in the Video tab and following the pressure playback."""

    def _show_video(self, loaded: LoadedExam) -> None:
        """Show the video of an exam in the Video tab.

        The video is written to a temporary file, which Qt Multimedia needs; it is
        replaced by the next one and removed when the window closes. It follows the
        pressure recording when the exam has one and ``VideoTrack*.dat`` tells where it starts.
        """
        self.exam_video.set_video(None)  # releases the previous file
        if loaded.video is None:
            return
        if self._video_dir is None:
            self._video_dir = tempfile.TemporaryDirectory(prefix="freestep-")
        path = Path(self._video_dir.name) / "video.avi"
        path.write_bytes(loaded.video)
        track = loaded.video_track
        self.exam_video.set_video(
            path,
            loaded.video_rotation,
            sync_start=track.start_frame if track is not None and loaded.recording is not None else None,
            frame_count=track.frame_count if track is not None else None,
        )

    @Slot()
    def _pause_hidden_video(self) -> None:
        """Pause the video when another tab is shown, else bring it to the shown pressure frame."""
        if self.tabs.currentWidget() is not self.exam_video:
            self.exam_video.pause()
        else:
            self._follow_video(playing=self._timer.isActive(), speed=self._playback_speed())

    def _follow_video(self, *, playing: bool = False, speed: float = 1.0) -> None:
        """Move the video to the time of the shown pressure frame (only while the Video tab is shown).

        Args:
            playing: The pressure recording is being played; the video plays along.
            speed: Playback speed of the pressure recording (1 = real time).
        """
        acq = self._current_acquisition()
        if acq is None or self._current is None or self._current.recording is None:
            return
        if self.tabs.currentWidget() is not self.exam_video or not self.exam_video.following:
            return
        first = int(self._current.recording.acquisitions[0].timestamps_ms[0])
        frame = min(self.slider.value(), acq.frame_count - 1)
        self.exam_video.follow((int(acq.timestamps_ms[frame]) - first) / 1000, playing=playing, speed=speed)
