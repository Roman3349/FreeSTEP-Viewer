"""PDF reports of an exam, painted with Qt.

- :mod:`.stabilometric` — the stabilometric report of a posturography exam,
- :mod:`.static` — the report of a static exam (footprint, optionally the photo),
- :mod:`.pages` — pages flowing over A4 and the blocks shared by the reports.

The reports render the widgets of the viewer (plots, heat maps), so they need PySide6
like the GUI.
"""

from .stabilometric import ReportData, write_report
from .static import StaticReportData, write_static_report

__all__ = ["ReportData", "StaticReportData", "write_report", "write_static_report"]
