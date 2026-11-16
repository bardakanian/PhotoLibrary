"""Easing off when the Mac gets warm (core/thermal.py and analyze_photos)."""

import sys
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from photolibrary.core import faces, thermal
from photolibrary.core.faces import analyze_photos
from photolibrary.core.thermal import CRITICAL, FAIR, NOMINAL, SERIOUS, allowed_workers, thermal_state

from .base import TempAppTestCase
from .fake_faces import FakeFaceEngine, write_people_photo


class AllowedWorkersTests(unittest.TestCase):
    def test_gentle_then_one_then_pause(self):
        self.assertEqual(allowed_workers(3, None), 3)  # unknown (not a Mac): gentle
        self.assertEqual(allowed_workers(3, NOMINAL), 3)
        self.assertEqual(allowed_workers(3, FAIR), 1)
        self.assertEqual(allowed_workers(3, SERIOUS), 0)
        self.assertEqual(allowed_workers(3, CRITICAL), 0)

    @unittest.skipUnless(sys.platform == "darwin", "macOS only")
    def test_reads_the_real_state_on_a_mac(self):
        self.assertIn(thermal_state(), (NOMINAL, FAIR, SERIOUS, CRITICAL))

    def test_gentle_by_default(self):
        self.assertLessEqual(faces.DEFAULT_WORKERS, 3)


class CountingEngine(FakeFaceEngine):
    def __init__(self):
        super().__init__()
        self.lock, self.running, self.most = threading.Lock(), 0, 0

    def detect(self, image):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.02)
        try:
            return super().detect(image)
        finally:
            with self.lock:
                self.running -= 1


class HeatTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        from photolibrary.core.indexer import MediaIndexer
        from photolibrary.core.database import MediaRepository
        library = self.make_library()
        for index in range(12):
            write_people_photo(library / f"p{index:02d}.jpg", datetime(2024, 1, 1 + index), (1,))
        MediaIndexer(library, threading.Event(), 1, lambda event: None).run()
        self.items = MediaRepository().query(library)

    def test_one_photo_at_a_time_while_warm(self):
        engine = CountingEngine()
        result = analyze_photos(self.items, engine, threading.Event(), lambda item, found: None, workers=3,
                                heat=lambda: FAIR)
        self.assertEqual(result.analyzed, 12)
        self.assertEqual(engine.most, 1)

    def test_pauses_while_hot_and_carries_on_once_cool(self):
        states = iter([NOMINAL, SERIOUS, SERIOUS, NOMINAL])
        pauses = []
        with patch.object(faces, "COOL_DOWN_CHECK_SECONDS", 0.01):
            result = analyze_photos(self.items, FakeFaceEngine(), threading.Event(), lambda item, found: None,
                                    workers=3, heat=lambda: next(states, NOMINAL), on_pause=pauses.append)
        self.assertEqual(result.analyzed, 12)  # nothing skipped
        self.assertEqual(pauses, [True, False])

    def test_stopping_during_a_pause_ends_it(self):
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        with patch.object(faces, "COOL_DOWN_CHECK_SECONDS", 0.01):
            result = analyze_photos(self.items, FakeFaceEngine(), stop, lambda item, found: None,
                                    heat=lambda: CRITICAL)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.analyzed, 0)
