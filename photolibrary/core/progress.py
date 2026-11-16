"""Time-left estimates for long background jobs (scans, thumbnails, finding people). Kept free of Qt so it can be tested.

Progress comes in bursts: cached files fly by, then a HEIC photo or a video takes a while. A rate
taken over a moment swings with every burst, so the estimate here uses the rate over the last
minute blended with the rate since the start, and the shown figure counts down steadily, only
jumping when the new estimate is clearly different.
"""

import time
from collections import deque

# Too early, the rate is mostly start-up cost; show no estimate until then.
MIN_SECONDS = 3.0
MIN_FRACTION = 0.01
# The recent rate is measured over this long, so a burst or a stall of a few seconds hardly moves it.
WINDOW_SECONDS = 60.0
# The shown estimate keeps counting down until a new one differs from it by more than this.
TOLERANCE = 0.25


class Eta:
    """Estimates the time left from how fast `done` has been growing, without jumping around."""

    def __init__(self, now: float | None = None, done: int = 0):
        """`done`: how much was already done when timing starts (say, resuming after a pause)."""
        self.started = time.monotonic() if now is None else now
        self._base = done
        self._samples: deque[tuple[float, int]] = deque([(self.started, done)])
        self._shown: float | None = None  # the last estimate given out...
        self._shown_at = self.started    # ...and when

    def elapsed(self, now: float | None = None) -> float:
        return (time.monotonic() if now is None else now) - self.started

    def remaining(self, done: int, total: int, now: float | None = None) -> float | None:
        """Seconds left, or None while there isn't enough to go on yet."""
        now = time.monotonic() if now is None else now
        if total <= 0:
            return None
        if done >= total:
            return 0.0
        self._record(now, done)
        elapsed = now - self.started
        progress = done - self._base
        if elapsed < MIN_SECONDS or progress < (total - self._base) * MIN_FRACTION or progress <= 0:
            return None
        oldest_time, oldest_done = self._samples[0]
        recent = (done - oldest_done) / (now - oldest_time) if now > oldest_time else 0.0
        overall = progress / elapsed
        rate = (recent + overall) / 2  # steady like the overall rate, but follows a lasting change of pace
        estimate = (total - done) / rate
        if self._shown is not None:
            counted_down = max(0.0, self._shown - (now - self._shown_at))
            if counted_down > 0 and abs(estimate - counted_down) <= TOLERANCE * counted_down:
                return counted_down
        self._shown, self._shown_at = estimate, now
        return estimate

    def _record(self, now: float, done: int) -> None:
        self._samples.append((now, done))
        while len(self._samples) > 2 and now - self._samples[1][0] >= WINDOW_SECONDS:
            self._samples.popleft()


def format_duration_words(seconds: float) -> str:
    """A rounded duration for people: "45 s", "3 min", "1 h 20 min"."""
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{max(1, seconds)} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def time_left_text(seconds: float | None) -> str:
    if seconds is None:
        return "estimating time left…"
    if seconds < 5:
        return "almost done"
    return f"about {format_duration_words(seconds)} left"
