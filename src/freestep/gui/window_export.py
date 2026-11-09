"""Saving the shown exam in other formats, the PDF report and the batch export."""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QProgressDialog

from ..batch import BatchResult, batch_export, exam_stem
from ..models import Exam
from ..postura import Postura
from ..writers import write_cop_csv, write_mat, write_npz, write_stabilometry_csv
from .batch_dialog import BatchDialog
from .exam import MODE_FILE_SUFFIX, MODE_FRAME, MODE_MAX, MODE_MEAN, png_bytes, show_on_heatmap
from .i18n import ntr, tr
from .report import ReportData, write_report
from .tasks import Progress
from .widgets import HeatmapView
from .window_base import WindowBase


class ExportMixin(WindowBase):
    """Saving the shown exam in other formats, the PDF report and the batch export."""

    def _save_name(self, title: str, suffix: str, filter_: str) -> Path | None:
        """Ask for an output file, proposing ``<birth date>_<name>_exam_<ID><suffix>`` like the batch export.

        Args:
            title: Translated dialog title.
            suffix: Appended to the proposed name, including the extension.
            filter_: File type filter of the dialog.

        Returns:
            The chosen path, or ``None`` when cancelled.
        """
        assert self._current is not None
        start = Path(str(self._settings.value("last_dir", str(Path.home()))))
        name = f"{exam_stem(self._current.patient, self._current.exam)}{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, title, str(start / name), filter_)
        return Path(path) if path else None

    def _recording(self) -> Postura | None:
        """Pressure data of the shown exam, ``None`` without an exam or for an exam without them."""
        return self._current.postura if self._current is not None else None

    def _export_csv(self) -> None:
        """Save the per-frame time, pressure sum and COP of all acquisitions as CSV."""
        if (post := self._recording()) is None or (
            path := self._save_name(tr("Export COP"), "_cop.csv", tr("CSV (*.csv)"))
        ) is None:
            return
        write_cop_csv(post, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_npz(self) -> None:
        """Save the frames and COP of all acquisitions as a compressed NumPy archive."""
        if (post := self._recording()) is None or (
            path := self._save_name(tr("Export frames"), ".npz", tr("NumPy (*.npz)"))
        ) is None:
            return
        write_npz(post, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_mat(self) -> None:
        """Save the frames, COP and stabilometric parameters as a MATLAB file."""
        title = tr("Export to MATLAB")
        if (post := self._recording()) is None or (
            path := self._save_name(title, ".mat", tr("MATLAB (*.mat)"))
        ) is None:
            return
        write_mat(post, path, self._stabilometry, self._romberg)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_mat_raw(self) -> None:
        """Save only the recorded data (no filtering, no parameters) as a MATLAB file."""
        title = tr("Export raw data to MATLAB")
        if (post := self._recording()) is None or (
            path := self._save_name(title, "_raw.mat", tr("MATLAB (*.mat)"))
        ) is None:
            return
        write_mat(post, path)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_stabilometry(self) -> None:
        """Save the stabilometric parameters (and Romberg quotients) as CSV."""
        title = tr("Export stabilometric parameters")
        if self._current is None or (path := self._save_name(title, "_stabilometry.csv", tr("CSV (*.csv)"))) is None:
            return
        write_stabilometry_csv(self._stabilometry, path, self._romberg)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_report(self) -> None:
        """Save the PDF report of the shown exam with the settings of the Stabilometry tab."""
        title = tr("Create PDF report")
        loaded, post = self._current, self._recording()
        if loaded is None or post is None:
            return
        if (path := self._save_name(title, "_report.pdf", tr("PDF (*.pdf)"))) is None:
            return
        data = ReportData(
            loaded.patient,
            loaded.exam,
            post,
            self._stabilometry,
            self._romberg,
            self._lowpass_hz or None,
            self._detrend,
        )
        write_report(path, data)
        self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _export_png(self) -> None:
        """Save the heat map as shown (frame, maximum or mean) as PNG."""
        if self._current is None:
            return
        mode = self.mode.currentIndex()
        acq = f"_a{self.acquisition.currentIndex() + 1}"
        suffix = (
            f"{acq}_frame{self.slider.value() + 1}.png" if mode == MODE_FRAME else f"{acq}_{MODE_FILE_SUFFIX[mode]}.png"
        )
        if (path := self._save_name(tr("Save image"), suffix, tr("PNG (*.png)"))) is None:
            return
        image = self.heatmap.image()
        if image is not None and image.save(str(path)):
            self.statusBar().showMessage(tr("Saved {path}", path=path))
        else:
            self.statusBar().showMessage(tr("Could not save {path}", path=path))

    def _export_png_zip(self) -> None:
        """Save every frame and the maximum and mean image of all acquisitions as PNGs in a ZIP.

        The images look like the heat map (same size and display options). The
        ZIP holds ``a<n>/frame_<k>.png``, ``a<n>_max.png`` and ``a<n>_mean.png``.
        Rendering runs in the GUI thread (widgets cannot paint elsewhere) behind a
        cancellable progress dialog; a cancelled export leaves no file.
        """
        loaded, post = self._current, self._recording()
        if loaded is None or post is None:
            return
        if (path := self._save_name(tr("Export images"), "_images.zip", tr("ZIP (*.zip)"))) is None:
            return
        self._stop()
        view = HeatmapView()
        view.resize(self.heatmap.size())
        for attribute, value in self._view_options.items():
            setattr(view, attribute, value)
        jobs = [
            (acq, frame, MODE_FRAME, f"a{acq.index + 1}/frame_{frame + 1:04d}.png")
            for acq in post.acquisitions
            for frame in range(acq.frame_count)
        ]
        for acq in post.acquisitions:
            jobs += [
                (acq, 0, MODE_MAX, f"a{acq.index + 1}_max.png"),
                (acq, 0, MODE_MEAN, f"a{acq.index + 1}_mean.png"),
            ]
        progress = QProgressDialog(tr("Rendering images…"), tr("Cancel"), 0, len(jobs), self)
        progress.setWindowTitle(tr("Export images"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        try:
            # PNG is compressed already, so the members are only stored
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
                for done, (acq, frame, mode, name) in enumerate(jobs):
                    if done % 20 == 0:
                        progress.setValue(done)
                        QApplication.processEvents()
                        if progress.wasCanceled():
                            break
                    show_on_heatmap(view, loaded, acq, frame, mode, trail=self.trail.value())
                    image = view.image()
                    if image is not None:
                        archive.writestr(name, png_bytes(image))
            cancelled = progress.wasCanceled()
        except OSError as exc:
            progress.close()
            self._error(tr("Could not save {path}", path=path), exc)
            return
        progress.close()
        if cancelled:
            path.unlink(missing_ok=True)
            self.statusBar().showMessage(tr("Export cancelled"))
        else:
            self.statusBar().showMessage(tr("Saved {path}", path=path))

    def _batch_export(self) -> None:
        """Export every exam of the open export into a directory (see :func:`freestep.batch.batch_export`).

        Decrypting runs in the worker thread behind a cancellable progress dialog;
        an export cancelled midway keeps the files written so far.
        """
        export = self._export
        if export is None:
            return
        saved_formats = str(self._settings.value("batch_formats", "mat")).split(",")
        start = str(self._settings.value("batch_dir", self._settings.value("last_dir", str(Path.home()))))
        summary = str(self._settings.value("batch_summary", "true")).lower() == "true"
        dialog = BatchDialog(self, start, saved_formats, summary)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        directory = Path(dialog.directory.text().strip())
        formats, summary = dialog.formats(), dialog.summary.isChecked()
        self._settings.setValue("batch_dir", str(directory))
        self._settings.setValue("batch_formats", ",".join(formats))
        self._settings.setValue("batch_summary", "true" if summary else "false")
        self._stop()

        progress = QProgressDialog(tr("Preparing…"), tr("Cancel"), 0, max(len(export.exams), 1), self)
        progress.setWindowTitle(tr("Batch export"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        cancel = threading.Event()
        progress.canceled.connect(cancel.set)
        reporter = Progress()

        def changed(index: int, count: int, exam_id: object) -> None:
            progress.setMaximum(count)
            progress.setValue(index)
            progress.setLabelText(tr("Exam {id} ({n} of {count})", id=exam_id, n=index + 1, count=count))

        reporter.changed.connect(changed)
        lowpass_hz, detrend, eyes_closed_first = self._lowpass_hz or None, self._detrend, self._eyes_closed_first

        def work() -> BatchResult:
            def report(index: int, count: int, exam: Exam) -> bool:
                reporter.changed.emit(index, count, exam.id)
                return not cancel.is_set()

            return batch_export(
                export,
                directory,
                formats,
                summary=summary,
                lowpass_hz=lowpass_hz,
                detrend=detrend,
                eyes_closed_first=eyes_closed_first,
                progress=report,
            )

        def done(result: BatchResult) -> None:
            progress.close()
            reporter.deleteLater()
            text = ntr(
                "{n} file written to {path}.",
                "{n} files written to {path}.",
                len(result.written),
                path=directory,
            )
            if result.cancelled:
                text += "\n" + tr("The export was cancelled.")
            if result.skipped:
                text += "\n\n" + tr("Skipped exams:") + "\n"
                text += "\n".join(f"{exam.id}: {reason}" for exam, reason in result.skipped)
            self.statusBar().showMessage(text.splitlines()[0])
            icon = QMessageBox.Icon.Warning if result.skipped else QMessageBox.Icon.Information
            QMessageBox(icon, tr("Batch export"), text, parent=self).exec()

        def fail(exc: BaseException) -> None:
            progress.close()
            reporter.deleteLater()
            self._error(tr("Batch export failed"), exc)

        self._run(work, done, fail)
