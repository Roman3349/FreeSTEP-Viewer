"""Custom painted widgets: pressure heat map, plots of the COP and of the history, images.

The widgets paint everything themselves with :class:`~PySide6.QtGui.QPainter`,
so no plotting library is needed. Coordinates follow the image convention:
x grows to the right, y grows downward (row 0 at the top).
"""

from .common import (
    AP_COLOR,
    COP_LEFT_COLOR,
    COP_RIGHT_COLOR,
    COP_TOTAL_COLOR,
    CURRENT_COLOR,
    ELLIPSE_COLOR,
    ML_COLOR,
    ORIGIN_MEAN,
    ORIGIN_PLATE,
    ORIGIN_PLATE_CENTRE,
)
from .heatmap import HeatmapView
from .history import HistoryPlot
from .images import ImagePanel, ImageView
from .plots import CopPlot, StabilogramPlot

__all__ = [
    "AP_COLOR",
    "COP_LEFT_COLOR",
    "COP_RIGHT_COLOR",
    "COP_TOTAL_COLOR",
    "CURRENT_COLOR",
    "ELLIPSE_COLOR",
    "ML_COLOR",
    "ORIGIN_MEAN",
    "ORIGIN_PLATE",
    "ORIGIN_PLATE_CENTRE",
    "CopPlot",
    "HeatmapView",
    "HistoryPlot",
    "ImagePanel",
    "ImageView",
    "StabilogramPlot",
]
