"""PDF reports of an exam, painted with Qt.

- :mod:`.stabilometric` — the stabilometric report of a posturography exam.

The reports render the widgets of the viewer (plots, heat maps), so they need PySide6
like the GUI.
"""

from .stabilometric import ReportData, write_report

__all__ = ["ReportData", "write_report"]
