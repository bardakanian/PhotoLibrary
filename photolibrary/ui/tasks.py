"""Run blocking work (database, filesystem) off the UI thread and deliver results back to it."""

import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

LOGGER = logging.getLogger(__name__)
_active: set["_Task"] = set()  # keeps Python wrappers alive while queued


class _Relay(QObject):
    """Lives on the UI thread, so its slot runs there however the signal was emitted."""

    finished = Signal(object, object)  # result, error

    def __init__(self, callback: Callable[[object, object], None]):
        super().__init__()
        self.callback = callback
        self.finished.connect(self.deliver)

    @Slot(object, object)
    def deliver(self, result, error) -> None:
        self.callback(result, error)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], object], relay: _Relay):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.relay = relay

    def run(self) -> None:
        try:
            result, error = self.fn(), None
        except Exception as exc:
            result, error = None, exc
        try:
            self.relay.finished.emit(result, error)
        except RuntimeError:
            # The receiver was destroyed while the window closed.
            pass


def run_async(pool: QThreadPool, fn: Callable[[], object],
              on_done: Callable[[object], None], on_error: Callable[[Exception], None] | None = None) -> None:
    """Call fn() on `pool`, then on_done(result) or on_error(exc) on the UI thread.

    Must be called from the UI thread.
    """
    def finished(result, error):
        _active.discard(task)
        if error is not None:
            if on_error is not None:
                on_error(error)
            else:
                LOGGER.error("Background task failed", exc_info=error)
            return
        on_done(result)

    task = _Task(fn, _Relay(finished))
    _active.add(task)
    pool.start(task)
