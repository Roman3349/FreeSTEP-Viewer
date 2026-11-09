"""Parsers of the files stored in a ``.fre`` exam archive.

Each pressure recording is read into a :class:`~freestep.recording.Recording`:

- :func:`parse_postura` — ``Postura.dat`` of a posturography exam,
- :func:`parse_static` — ``Statica.dat`` of a static exam (one frame),
- :func:`parse_walkway` — ``Dinamica.dat`` / ``TreadMILL.dat``, gait over several plates.

:class:`VideoTrack` reads ``VideoTrack*.dat``, which relates the video to the recording.
See ``docs/FORMAT.md`` for the layouts.
"""

from .postura import parse_postura
from .static import parse_static
from .video_track import VideoTrack
from .walkway import parse_walkway

__all__ = ["VideoTrack", "parse_postura", "parse_static", "parse_walkway"]
