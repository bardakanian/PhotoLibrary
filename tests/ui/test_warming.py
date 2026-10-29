import threading
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer

from .base import WindowTestCase

PLAYING = QMediaPlayer.PlaybackState.PlayingState
STOPPED = QMediaPlayer.PlaybackState.StoppedState
PAUSED = QMediaPlayer.PlaybackState.PausedState


def warmer_running() -> bool:
    return any(thread.name == "thumbnail-warmer" and thread.is_alive() for thread in threading.enumerate())


class WarmingDuringPlaybackTests(WindowTestCase):
    """Thumbnail warming waits while a video plays, then picks up where it left off."""

    def setUp(self):
        super().setUp()
        self.wait_until(lambda: not self.window.warm_pending and not warmer_running(), message="first warming run")

    def test_warming_requested_during_playback_waits_for_it_to_stop(self):
        self.window._playback_state_changed(PLAYING)
        self.window.start_warming()
        self.settle(0.2)
        self.assertTrue(self.window.warm_pending)
        self.assertFalse(warmer_running())
        self.window._playback_state_changed(PAUSED)
        self.wait_until(lambda: not self.window.warm_pending, message="warming after playback paused")

    def test_playback_stops_a_running_warm_and_resumes_it_after(self):
        released = threading.Event()

        def slow_query(*args, real=self.window.repository.query, **kwargs):
            released.wait(5)  # holds the run until "playback" has started
            return real(*args, **kwargs)

        with patch.object(self.window.repository, "query", side_effect=slow_query):
            self.window.start_warming()
            self.window._playback_state_changed(PLAYING)
            released.set()
            self.wait_until(lambda: not warmer_running(), message="warmer to stop")
        self.assertTrue(self.window.warm_pending)  # cancelled, not finished
        self.window._playback_state_changed(STOPPED)
        self.wait_until(lambda: not self.window.warm_pending and not warmer_running(), message="warming to finish")
