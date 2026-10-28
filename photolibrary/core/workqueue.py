import logging
import threading
from collections import OrderedDict
from typing import Callable, Hashable, Iterable

LOGGER = logging.getLogger(__name__)


class WorkQueue:
    """A keyed FIFO served by a few daemon threads.

    Unlike a plain thread pool, queued jobs can be re-ordered or dropped by key, which
    is how the grid keeps thumbnail work focused on what is on screen right now.
    """

    def __init__(self, workers: int, name: str):
        self._jobs: OrderedDict[Hashable, Callable[[], None]] = OrderedDict()
        self._condition = threading.Condition()
        self._running: set[Hashable] = set()
        self._closed = False
        self._threads = [
            threading.Thread(target=self._work, name=f"{name}-{index}", daemon=True)
            for index in range(max(1, workers))
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, key: Hashable, job: Callable[[], None]) -> bool:
        """Queue a job unless one with the same key is already queued or running."""
        with self._condition:
            if self._closed or key in self._jobs or key in self._running:
                return False
            self._jobs[key] = job
            self._condition.notify()
            return True

    def retain(self, keys: Iterable[Hashable]) -> list[Hashable]:
        """Drop queued jobs whose key is not in `keys`; keep the rest in the given order first.

        Returns the dropped keys. Running jobs are never interrupted.
        """
        wanted = list(keys)
        wanted_set = set(wanted)
        with self._condition:
            dropped = [key for key in self._jobs if key not in wanted_set]
            for key in dropped:
                del self._jobs[key]
            # Move kept jobs into the order the caller asked for (top of screen first).
            for key in reversed(wanted):
                if key in self._jobs:
                    self._jobs.move_to_end(key, last=False)
            return dropped

    def clear(self) -> list[Hashable]:
        with self._condition:
            dropped = list(self._jobs)
            self._jobs.clear()
            return dropped

    def is_busy(self, key: Hashable) -> bool:
        with self._condition:
            return key in self._jobs or key in self._running

    def __len__(self) -> int:
        with self._condition:
            return len(self._jobs)

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            self._jobs.clear()
            self._condition.notify_all()

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._jobs and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                key, job = self._jobs.popitem(last=False)
                self._running.add(key)
            try:
                job()
            except Exception:
                LOGGER.exception("Background job %r failed", key)
            finally:
                with self._condition:
                    self._running.discard(key)
