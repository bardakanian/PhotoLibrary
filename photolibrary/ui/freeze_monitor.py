"""Log what every thread was doing whenever the UI thread stops responding for a moment.

A timer on the UI thread records a heartbeat; a watchdog thread notices when it goes quiet
for longer than `threshold` and writes the Python stack of each busy thread to the log, then
how long the freeze lasted once it ends. Video playback stutters during such freezes, so the
log names the cause. It costs one tiny timer callback every 20 ms while nothing is wrong.
"""

import logging
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QTimer

LOGGER = logging.getLogger(__name__)
HEARTBEAT_MS = 20
THRESHOLD_SECONDS = 0.08
# A detailed report at most this often; freezes in between are only counted.
REPORT_INTERVAL_SECONDS = 10.0
# Longer gaps are the computer sleeping or the process being suspended, not a freeze.
IGNORE_LONGER_THAN_SECONDS = 30.0
# Frames that mean a thread is idle, waiting for work.
_IDLE_FUNCTIONS = {"wait", "_wait_for_tstate_lock", "get", "acquire", "sleep", "select", "_worker", "read_events",
                   "read_directory_changes", "accept", "recv"}


def _thread_dump(main_id: int, own_id: int) -> str:
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    sections = []
    for ident, frame in sys._current_frames().items():
        if ident == own_id:
            continue
        stack = traceback.extract_stack(frame)
        if ident != main_id and stack and stack[-1].name in _IDLE_FUNCTIONS:
            continue
        name = names.get(ident, f"thread {ident}")
        if ident == main_id:
            name += " (UI)"
            if stack and "exec(" in (stack[-1].line or ""):
                # No Python code of ours on top of the event loop: the UI thread is inside Qt
                # (C++), or waiting for another thread that holds the GIL.
                name += ", inside Qt or waiting for the GIL"
        lines = "".join(traceback.format_list(stack[-12:]))
        sections.append(f"--- {name}\n{lines}")
    return "\n".join(sections)


class FreezeMonitor(QObject):
    def __init__(self, parent: QObject | None = None, threshold: float = THRESHOLD_SECONDS,
                 report_interval: float = REPORT_INTERVAL_SECONDS):
        super().__init__(parent)
        self.threshold = threshold
        self.report_interval = report_interval
        self._beat = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._heartbeat)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._main_id = threading.main_thread().ident
        self.freezes = 0  # total seen, for tests and the log

    def start(self) -> None:
        self._beat = time.monotonic()
        self._timer.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True, name="freeze-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._timer.stop()
        self._stop.set()

    def _heartbeat(self) -> None:
        self._beat = time.monotonic()

    def _watch(self) -> None:
        own_id = threading.get_ident()
        frozen_since: float | None = None
        reported = False
        last_report = -self.report_interval
        unreported, unreported_worst = 0, 0.0
        while not self._stop.wait(HEARTBEAT_MS / 1000):
            beat = self._beat
            silent = time.monotonic() - beat
            if frozen_since is None:
                if silent > self.threshold:
                    frozen_since = beat
                    now = time.monotonic()
                    reported = now - last_report >= self.report_interval
                    if reported:
                        last_report = now
                        since = (f" ({unreported} more since the last report, worst {unreported_worst * 1000:.0f} ms)"
                                 if unreported else "")
                        LOGGER.warning("UI thread not responding for %.0f ms%s; threads:\n%s",
                                       silent * 1000, since, _thread_dump(self._main_id, own_id))
                        unreported, unreported_worst = 0, 0.0
            elif beat != frozen_since:
                # The UI thread ran again.
                lasted = beat - frozen_since
                frozen_since = None
                if lasted >= IGNORE_LONGER_THAN_SECONDS:
                    continue
                self.freezes += 1
                if reported:
                    LOGGER.warning("UI freeze lasted %.0f ms", lasted * 1000)
                else:
                    unreported += 1
                    unreported_worst = max(unreported_worst, lasted)
