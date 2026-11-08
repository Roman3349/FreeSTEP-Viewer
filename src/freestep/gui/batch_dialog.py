"""Dialog of the batch export of all exams."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..batch import FORMATS, INDEX_NAME, SUMMARY_NAME
from .i18n import tr


def _format_labels() -> dict[str, str]:
    """Translated descriptions of the batch export formats (keys of :data:`freestep.batch.FORMATS`)."""
    return {
        "mat": tr("MATLAB: data and stabilometric parameters (.mat)"),
        "mat-raw": tr("MATLAB: recorded data only (_raw.mat)"),
        "npz": tr("NumPy: frames and COP (.npz)"),
        "cop-csv": tr("COP per frame (_cop.csv)"),
        "stats-csv": tr("Stabilometric parameters (_stabilometry.csv)"),
    }


class BatchDialog(QDialog):
    """Options of the batch export: output directory, formats and the summary table."""

    def __init__(self, parent: QWidget, directory: str, formats: list[str], summary: bool) -> None:
        """Create the dialog.

        Args:
            parent: Parent window.
            directory: Proposed output directory.
            formats: Initially checked formats.
            summary: Initial state of the summary option.
        """
        super().__init__(parent)
        self.setWindowTitle(tr("Batch export"))
        layout = QVBoxLayout(self)
        info = QLabel(
            tr(
                "Exports every exam of the open export. Files are named "
                "<birth date>_<name>_<exam date>_<HH-MM>, e.g. 1990-01-31_Jan_Novak_2025-03-10_09-37.mat; "
                "{index} lists the exported exams.",
                index=INDEX_NAME,
            )
        )
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)

        row = QHBoxLayout()
        self.directory = QLineEdit(directory)
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(QLabel(tr("Directory")))
        row.addWidget(self.directory, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.checks: dict[str, QCheckBox] = {}
        for key, label in _format_labels().items():
            check = QCheckBox(label)
            check.setChecked(key in formats)
            check.toggled.connect(self._update_ok)
            self.checks[key] = check
            layout.addWidget(check)
        self.summary = QCheckBox(tr("Summary table of all acquisitions ({name})", name=SUMMARY_NAME))
        self.summary.setChecked(summary)
        self.summary.toggled.connect(self._update_ok)
        layout.addWidget(self.summary)
        note = QLabel(tr("The parameters use the filter, trend and Romberg settings of the Stabilometry tab."))
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.directory.textChanged.connect(self._update_ok)
        self._update_ok()

    def formats(self) -> list[str]:
        """Return the checked formats in the order of :data:`freestep.batch.FORMATS`."""
        return [key for key in FORMATS if self.checks[key].isChecked()]

    def _browse(self) -> None:
        """Let the user pick the output directory."""
        path = QFileDialog.getExistingDirectory(self, tr("Batch export"), self.directory.text())
        if path:
            self.directory.setText(path)

    def _update_ok(self) -> None:
        """Allow OK only with a directory and at least one format or the summary."""
        ok = bool(self.directory.text().strip()) and bool(self.formats() or self.summary.isChecked())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
