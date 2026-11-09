"""Parser of ``VideoTrack*.dat`` — how the video of an exam relates to its pressure recording."""

from __future__ import annotations

import struct
from dataclasses import dataclass

_VIDEO_TRACK_STRUCT = struct.Struct("<h3i")


@dataclass(frozen=True)
class VideoTrack:
    """Header of ``VideoTrack*.dat``: how the video relates to the pressure recording.

    Attributes:
        start_frame: Video frame at the first pressure frame (verified on a dynamic exam
            with video: the recording time ``t`` is at video frame ``start_frame + t × fps``).
        end_frame: Last video frame of the analysed range ❓.
        frame_count: Number of frames of the video.
    """

    start_frame: int
    end_frame: int
    frame_count: int

    @classmethod
    def parse(cls, data: bytes) -> VideoTrack | None:
        """Parse the header (int16 version, then three int32), ``None`` when the data are too short."""
        if len(data) < _VIDEO_TRACK_STRUCT.size:
            return None
        _, start, end, count = _VIDEO_TRACK_STRUCT.unpack_from(data)
        return cls(start, end, count)
