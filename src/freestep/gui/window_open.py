"""Opening exports and exam files, the tree of subjects and exams and showing a loaded exam."""

from __future__ import annotations

import html
import traceback
import zipfile
from collections.abc import Callable
from datetime import date, time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QLineEdit, QMessageBox, QTreeWidgetItem

from ..archive import FreeStepExport, FreFile
from ..models import Exam, Patient
from ..stabilometry import MIN_FRAMES
from .exam import LoadedExam, decode_images, is_bad_password, load_exam
from .i18n import ntr, tr
from .info import info_html
from .meta import APP_NAME
from .tasks import Task
from .texts import acquisition_label, exam_type_label, plain_value
from .window_base import EXAM_ROLE, WindowBase


class OpenMixin(WindowBase):
    """Opening exports and exam files, the tree of subjects and exams and showing a loaded exam."""

    def _run(
        self, func: Callable[[], object], done: Callable[[Any], None], fail: Callable[[BaseException], None]
    ) -> None:
        """Run ``func`` in the worker thread.

        Args:
            func: Work to do; must not touch widgets.
            done: Called in the GUI thread with the result.
            fail: Called in the GUI thread with the raised exception.
        """
        task = Task(func)
        self._tasks.add(task)  # keep a reference until finished

        def finished(result: object) -> None:
            self._tasks.discard(task)
            done(result)

        def failed(exc: BaseException) -> None:
            self._tasks.discard(task)
            fail(exc)

        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
        self._pool.start(task)

    def _error(self, title: str, exc: BaseException) -> None:
        """Show an error dialog with the traceback in its details.

        Args:
            title: Translated dialog title.
            exc: The error.
        """
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"{title}: {exc}")
        box = QMessageBox(QMessageBox.Icon.Critical, title, str(exc) or exc.__class__.__name__, parent=self)
        box.setDetailedText("".join(traceback.format_exception(exc)))
        box.exec()

    def _ask_password(self) -> bool:
        """Ask for the archive password and remember it.

        Returns:
            ``False`` when the user cancelled the dialog.
        """
        password, ok = QInputDialog.getText(
            self, tr("Password"), tr("The archive is encrypted with a different password:"), QLineEdit.EchoMode.Password
        )
        if ok:
            self._password = password
        return ok

    def open_dialog(self) -> None:
        """Let the user pick an export or exam files and open them."""
        start = str(self._settings.value("last_dir", str(Path.home())))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Open FreeSTEP data"),
            start,
            tr("FreeSTEP data (*.zip *.fre);;Export (*.zip);;Exam (*.fre);;All (*)"),
        )
        if paths:
            self.open_paths([Path(p) for p in paths])

    def open_folder_dialog(self) -> None:
        """Let the user pick a folder and open all exam files in it and its subfolders."""
        start = str(self._settings.value("last_dir", str(Path.home())))
        path = QFileDialog.getExistingDirectory(self, tr("Open a folder with exams"), start)
        if path:
            self.open_paths([Path(path)])

    def open_path(self, path: Path) -> None:
        """Open an export (``.zip``), an exam (``.fre``) or the exams in a folder (see :meth:`open_paths`)."""
        self.open_paths([path])

    def open_paths(self, paths: list[Path]) -> None:
        """Open an export (``.zip``), or exam files (``.fre``) and the exam files in folders.

        Exam files take precedence: a ``.zip`` given together with them is ignored.

        Args:
            paths: Files and folders to open; the type of a file is decided by its suffix.
        """
        if not paths:
            return
        self._settings.setValue("last_dir", str(paths[0] if paths[0].is_dir() else paths[0].parent))
        self._stop()
        exams = sorted({f for p in paths for f in (p.rglob("*") if p.is_dir() else [p]) if f.suffix.lower() == ".fre"})
        if exams:
            self._open_fre_files(exams)
        elif any(p.is_dir() for p in paths):
            QMessageBox.information(self, APP_NAME, tr("The folder contains no exam files (.fre)."))
        else:
            self._open_export(paths[0])

    def _open_export(self, path: Path) -> None:
        """Open an export in the worker thread and fill the tree; asks for the password if needed."""
        self._fre_files = []
        self.statusBar().showMessage(tr("Reading {name}…", name=path.name))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        password = self._password

        def work() -> FreeStepExport:
            export = FreeStepExport(path, password)
            try:
                _ = export.index, export.patients, export.exams  # decrypts Indice.ini and Export.mdb
            except BaseException:
                export.close()
                raise
            return export

        def done(export: FreeStepExport) -> None:
            QApplication.restoreOverrideCursor()
            self._close_export()
            self._export = export
            self._export_name = path.name
            self.batch_action.setEnabled(True)
            self._populate_tree(export)
            self._update_tabs()
            self.setWindowTitle(f"{path.name} — {APP_NAME}")
            self._show_export_status()

        def fail(exc: BaseException) -> None:
            QApplication.restoreOverrideCursor()
            if is_bad_password(exc) and self._ask_password():
                self._open_export(path)
            elif isinstance(exc, zipfile.BadZipFile):
                self._error(tr("Not a FreeSTEP export"), exc)
            else:
                self._error(tr("Cannot open export"), exc)

        self._run(work, done, fail)

    def _show_export_status(self) -> None:
        """Show the summary of the open export in the status bar."""
        assert self._export is not None
        self.statusBar().showMessage(
            tr(
                "{name}: centre {centre}, {patients} subjects, {exams} exams",
                name=self._export_name,
                centre=self._export.index.centre_code,
                patients=len(self._export.patients),
                exams=len(self._export.exams),
            )
        )

    def _open_fre_files(self, paths: list[Path]) -> None:
        """Open exam files: list them in the tree by subject and show the first one.

        Only the small INI files are read here; the data are decoded when an exam is shown.
        Files that cannot be read are skipped and listed afterwards.
        """
        self._close_export()
        self.tree.clear()
        entries: list[tuple[Path, Patient, Exam]] = []
        failed: list[str] = []
        for path in paths:
            try:
                with FreFile(path, self._password) as fre:
                    entries.append((path, fre.patient, fre.exam))  # reading the INI files checks the password
            except BaseException as exc:
                if is_bad_password(exc) and not entries and self._ask_password():
                    self._open_fre_files(paths)
                    return
                failed.append(f"{path.name}: {exc}")
        self._fre_files = sorted(
            entries, key=lambda e: (e[1].name.casefold(), e[2].exam_date or date.min, e[2].exam_time or time.min)
        )
        self._populate_files()
        if failed:
            shown = "\n".join(failed[:10]) + ("\n…" if len(failed) > 10 else "")
            QMessageBox.warning(self, APP_NAME, tr("These exam files cannot be opened:") + "\n\n" + shown)
        if not self._fre_files:
            return
        first = self._fre_files[0][0]
        self._load(lambda: FreFile(first, self._password), ("file", str(first)))

    def _populate_files(self) -> None:
        """Fill the tree with the opened exam files grouped by subject (see :attr:`_fre_files`) and set the title."""
        self.tree.clear()
        count = len(self._fre_files)
        name = self._fre_files[0][0].name if count == 1 else ntr("{n} exam file", "{n} exam files", count)
        self.setWindowTitle(f"{name} — {APP_NAME}" if count else APP_NAME)
        nodes: dict[tuple[str, object], QTreeWidgetItem] = {}
        for path, patient, exam in self._fre_files:
            subject = (patient.name, patient.birth_date)
            if subject not in nodes:
                nodes[subject] = QTreeWidgetItem([patient.name or tr("Unknown"), plain_value(patient.birth_date), ""])
                self.tree.addTopLevelItem(nodes[subject])
                nodes[subject].setExpanded(True)
            child = QTreeWidgetItem(
                [path.name, f"{plain_value(exam.exam_date)} {plain_value(exam.exam_time)}", exam_type_label(exam)]
            )
            child.setToolTip(0, str(path))
            child.setData(0, EXAM_ROLE, ("file", str(path)))
            nodes[subject].addChild(child)
        self.tree.resizeColumnToContents(1)

    def _update_tabs(self) -> None:
        """Show only the tabs relevant for the shown exam; Info is always shown and comes first.

        The analysis tabs need a recording with enough frames (posturography; a static
        exam has one frame), the History tab also an open export, the Images tab images.
        Without an exam, the History tab is shown for the subjects of an open export.
        """
        loaded = self._current
        post = loaded.postura if loaded is not None else None
        analysable = post is not None and not post.gait and any(a.frame_count >= MIN_FRAMES for a in post.acquisitions)
        visible = {self.info: True, self.history_tab: self._export is not None and (analysable or loaded is None)}
        visible |= dict.fromkeys(self.analysis_tabs, analysable)
        visible[self.images] = loaded is not None and bool(loaded.images)
        visible[self.exam_video] = loaded is not None and loaded.video is not None
        for widget, shown in visible.items():
            self.tabs.setTabVisible(self.tabs.indexOf(widget), shown)
        if not visible.get(self.tabs.currentWidget(), False):
            self.tabs.setCurrentWidget(self.info)

    def _close_export(self) -> None:
        """Close the open export, if any."""
        if self._export is not None:
            self._export.close()
            self._export = None
        self.batch_action.setEnabled(False)

    def _populate_tree(self, export: FreeStepExport) -> None:
        """Fill the tree with the patients of ``export`` and their exams.

        Exams whose ``.fre`` file is missing in the export are shown disabled.
        """
        self.tree.clear()
        present = set(export.fre_names)
        for patient in sorted(export.patients, key=lambda p: p.name.casefold()):
            node = QTreeWidgetItem([patient.name, plain_value(patient.birth_date), ""])
            node.setToolTip(0, tr("ID {id}", id=patient.id))
            if patient.id is not None:
                node.setData(0, EXAM_ROLE, ("patient", patient.id))
            self.tree.addTopLevelItem(node)
            exams = export.exams_of(patient.id) if patient.id is not None else []
            for exam in exams:
                if exam.file_name in present:
                    label = tr("Exam {id}", id=exam.id)
                else:
                    label = tr("Exam {id} (missing)", id=exam.id)
                child = QTreeWidgetItem(
                    [label, f"{plain_value(exam.exam_date)} {plain_value(exam.exam_time)}", exam_type_label(exam)]
                )
                child.setData(0, EXAM_ROLE, ("exam", exam.id))
                child.setDisabled(exam.file_name not in present)
                node.addChild(child)
            node.setExpanded(True)
        self.tree.resizeColumnToContents(1)

    @Slot(QTreeWidgetItem, int)
    def _on_item_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """Load the exam of a clicked tree item; a patient shows their history (the shown exam is ignored)."""
        key = item.data(0, EXAM_ROLE)
        if not key or key == self._loading_exam_id or (self._current is not None and key == self._current_key):
            return
        kind, value = key
        if kind == "patient":
            self._history_patient = int(value)
            self.tabs.setTabVisible(self.tabs.indexOf(self.history_tab), True)
            self.tabs.setCurrentWidget(self.history_tab)
            self._update_history()
        elif kind == "exam" and self._export is not None:
            export = self._export
            self._load(lambda: export.open_fre(int(value)), key)
        elif kind == "file":
            path = Path(value)
            self._load(lambda: FreFile(path, self._password), key)

    def _load(self, open_fre: Callable[[], FreFile], key: object) -> None:
        """Open (decrypt) the exam and decode it in the worker thread, then show it.

        Args:
            open_fre: Returns the open exam archive; called in the worker thread.
            key: Tree key of the exam; a result for an outdated key is discarded.
        """
        self._stop()
        self._loading_exam_id = key
        self.statusBar().showMessage(tr("Decrypting and decoding the exam…"))
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        def done(loaded: LoadedExam) -> None:
            QApplication.restoreOverrideCursor()
            if self._loading_exam_id != key:
                loaded.fre.close()
                return
            self._loading_exam_id = None
            if self._current is not None:
                self._current.fre.close()
            self._current = loaded
            self._current_key = key
            self._show_exam(loaded)
            self._update_tabs()

        def fail(exc: BaseException) -> None:
            self._loading_exam_id = None
            self._error(tr("Cannot decode exam"), exc)

        def work() -> LoadedExam:
            fre = open_fre()
            try:
                return load_exam(fre)
            except BaseException:
                fre.close()
                raise

        self._run(work, done, fail)

    def _show_exam(self, loaded: LoadedExam) -> None:
        """Show a loaded exam in all views, starting with the first frame of the first acquisition."""
        images = decode_images(loaded)
        self.images.set_images(images, tr("No images in this exam"))
        self.info.setHtml(info_html(loaded))
        if self._export is not None and loaded.exam.patient_id is not None:
            self._history_patient = loaded.exam.patient_id
        self._show_video(loaded)
        post = loaded.postura
        if post is None:
            self._show_image_exam(loaded, images)
            return
        self.main_view.setCurrentIndex(0)
        self.acquisition.blockSignals(True)
        self.acquisition.clear()
        self.acquisition.addItems([acquisition_label(a, gait=post.gait) for a in post.acquisitions])
        self.acquisition.setCurrentIndex(0)
        self.acquisition.blockSignals(False)
        self._set_exam_controls_enabled(True)
        self.acquisition.setEnabled(len(post.acquisitions) > 1)
        self._update_stabilometry()
        self._update_history()
        self._on_acquisition_changed()
        values = {"id": loaded.exam.id, "name": loaded.patient.name, "platform": post.header.platform}
        count = len(post.acquisitions)
        if post.gait:
            message = ntr(
                "Exam {id} — {name}: {n} pass, {platform}",
                "Exam {id} — {name}: {n} passes, {platform}",
                count,
                **values,
            )
        else:
            message = ntr(
                "Exam {id} — {name}: {n} acquisition, {platform}",
                "Exam {id} — {name}: {n} acquisitions, {platform}",
                count,
                **values,
            )
        self.statusBar().showMessage(message)

    def _show_image_exam(self, loaded: LoadedExam, images: list[tuple[str, QImage, float | None]]) -> None:
        """Show an exam without readable pressure data: its images instead of the pressure views."""
        self._stop()
        self.acquisition.clear()
        self._set_exam_controls_enabled(False)
        exam_type = exam_type_label(loaded.exam)
        self.exam_images.set_images(
            images,
            tr("{type}: the viewer cannot read the data of this exam type and the exam has no images.", type=exam_type),
        )
        self.main_view.setCurrentWidget(self.exam_images)
        self.heatmap.clear()
        self.cop_plot.clear()
        self.cop_plot.set_ellipse(None)
        self.stabilogram.clear()
        self.speed_curve.clear()
        self._stabilometry, self._romberg = [], None
        self.stabilometry_view.setHtml(
            f"<p>{html.escape(tr('{type}: the exam has no pressure data to analyse.', type=exam_type))}</p>"
        )
        self._update_history()
        self.statusBar().showMessage(
            tr(
                "Exam {id} — {name}: {type}, no pressure data",
                id=loaded.exam.id,
                name=loaded.patient.name,
                type=exam_type,
            )
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt API)
        """Accept dragged ``.zip`` and ``.fre`` files and folders (Qt event handler)."""
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile()) if url.isLocalFile() else None
            if path is not None and (path.is_dir() or path.suffix.lower() in (".zip", ".fre")):
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt API)
        """Open the dropped files and folders (Qt event handler)."""
        self.open_paths([Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()])
