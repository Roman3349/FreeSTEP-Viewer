"""Background work in the Qt thread pool with results delivered to the GUI thread."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class TaskSignals(QObject):
    """Signals of a :class:`Task` (a ``QRunnable`` cannot have signals itself).

    Signals are delivered in the GUI thread because this object lives there.
    """

    finished = Signal(object)
    failed = Signal(object)


class Task(QRunnable):
    """Run a function in a thread pool and report the result via :class:`TaskSignals`."""

    def __init__(self, func: Callable[[], object]) -> None:
        """Create the task.

        Args:
            func: Function to run in the worker thread; must not touch widgets.
        """
        super().__init__()
        self.func = func
        self.signals = TaskSignals()

    def run(self) -> None:
        """Call the function and emit ``finished(result)`` or ``failed(exception)``."""
        try:
            result = self.func()
        except BaseException as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


class Progress(QObject):
    """Progress reports of a background batch export (emitted in the worker thread, delivered in the GUI)."""

    changed = Signal(int, int, object)  # index, count, exam ID
