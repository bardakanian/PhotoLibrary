import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch

from PIL import Image

from photolibrary.core import media, thumbnail_warmer
from photolibrary.core.database import MediaRepository
from photolibrary.core.indexer import MediaIndexer
from photolibrary.core.thumbnail_warmer import missing_thumbnails, warm_thumbnails

from .base import TempAppTestCase


class ThumbnailWarmerTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.make_library()
        for index in range(12):
            self.write_photo(self.library / f"{index}.jpg", datetime(2020, 1, index + 1), size=(64, 48))
        (self.library / "broken.jpg").write_bytes(b"not an image")
        MediaIndexer(self.library, threading.Event(), 1, lambda event: None).run()
        self.items = MediaRepository().query(self.library)

    def test_creates_every_missing_thumbnail_and_reports_progress(self):
        progress = []
        result = warm_thumbnails(self.items, threading.Event(), lambda done, total: progress.append((done, total)),
                                 workers=3)
        self.assertEqual((result.total, result.missing, result.created, result.failed), (13, 13, 12, 1))
        self.assertFalse(result.cancelled)
        self.assertEqual(progress[0], (0, 13))
        self.assertEqual(progress[-1], (13, 13))
        self.assertEqual(missing_thumbnails(self.items), [])  # the failure is remembered too

    def test_second_run_has_nothing_to_do(self):
        warm_thumbnails(self.items, threading.Event(), workers=2)
        with patch.object(thumbnail_warmer, "get_cached_thumbnail") as create:
            result = warm_thumbnails(self.items, threading.Event(), workers=2)
            create.assert_not_called()
        self.assertEqual((result.missing, result.created), (0, 0))

    def test_cancel_stops_early(self):
        stop = threading.Event()
        calls = []

        def slow(*args):
            calls.append(args)
            stop.set()
            return None

        with patch.object(thumbnail_warmer, "get_cached_thumbnail", side_effect=slow):
            result = warm_thumbnails(self.items, stop, workers=1)
        self.assertTrue(result.cancelled)
        self.assertLess(len(calls), len(self.items))

    def test_parallel_creation_of_the_same_thumbnail_is_safe(self):
        """Grid loader and warmer may create the same file at once; the result must be a valid JPEG."""
        item = next(entry for entry in self.items if entry.name == "0.jpg")
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: media.get_cached_thumbnail(
                item.path, item.media_type, item.file_size, item.modified_ns), range(16)))
        self.assertIsNotNone(results[0])
        self.assertTrue(all(result == results[0] for result in results))
        with Image.open(results[0]) as image:
            image.load()
        leftovers = [path for path in results[0].parent.iterdir() if ".part" in path.name]
        self.assertEqual(leftovers, [])
