import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

from photolibrary.core.watcher import WATCHDOG_ENABLED, ChangeBatcher, LibraryWatcher


class ChangeBatcherTests(unittest.TestCase):
    def test_waits_for_quiet_before_reporting(self):
        batcher = ChangeBatcher(quiet_seconds=3, max_wait_seconds=60)
        sizes = {"/a.jpg": 10}
        self.assertFalse(batcher.due(0, sizes.get))  # nothing yet
        batcher.note(0, "/a.jpg", 10)
        batcher.note(2, "/b.jpg", None)
        self.assertFalse(batcher.due(4, sizes.get))  # only 2 s since the last change
        self.assertTrue(batcher.due(5.1, lambda path: sizes.get(path)))
        self.assertFalse(batcher.pending)
        self.assertFalse(batcher.due(10, sizes.get))  # reported once

    def test_growing_file_delays_the_report(self):
        batcher = ChangeBatcher(quiet_seconds=1, max_wait_seconds=60)
        sizes = {"/big.mov": 100}
        batcher.note(0, "/big.mov", 100)
        sizes["/big.mov"] = 5_000  # still being copied
        self.assertFalse(batcher.due(2, sizes.get))
        self.assertFalse(batcher.due(2.5, sizes.get))  # the size check counted as new activity
        self.assertTrue(batcher.due(3.1, sizes.get))  # stable for a quiet period

    def test_never_holds_changes_longer_than_max_wait(self):
        batcher = ChangeBatcher(quiet_seconds=3, max_wait_seconds=10)
        for second in range(0, 12):
            batcher.note(second, f"/{second}.jpg", 1)
        self.assertTrue(batcher.due(11, lambda path: 1))

    def test_directory_changes_count(self):
        batcher = ChangeBatcher(quiet_seconds=1)
        batcher.note(0)  # e.g. a folder was renamed: no single file to track
        self.assertTrue(batcher.due(1.5, lambda path: None))


@unittest.skipUnless(WATCHDOG_ENABLED, "watchdog not installed")
class LibraryWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve() / "library"
        (self.folder / "2026-1").mkdir(parents=True)
        self.changes = threading.Event()
        self.watcher = LibraryWatcher(self.folder, self.changes.set, quiet_seconds=0.3)
        self.assertTrue(self.watcher.start())
        time.sleep(1.2)  # past the start-up grace period, when FSEvents replays recent history

    def tearDown(self):
        self.watcher.stop()
        self.temp.cleanup()

    def test_new_photo_is_reported(self):
        Image.new("RGB", (8, 8)).save(self.folder / "2026-1" / "new.jpg")
        self.assertTrue(self.changes.wait(8), "no change reported")

    def test_moved_photo_is_reported(self):
        photo = self.folder / "2026-1" / "a.jpg"
        Image.new("RGB", (8, 8)).save(photo)
        self.assertTrue(self.changes.wait(8))
        self.changes.clear()
        (self.folder / "2026-2").mkdir()
        os.rename(photo, self.folder / "2026-2" / "a.jpg")
        self.assertTrue(self.changes.wait(8), "move not reported")

    def test_unrelated_and_hidden_files_are_ignored(self):
        (self.folder / "notes.txt").write_text("hello")
        (self.folder / ".DS_Store").write_bytes(b"x")
        self.assertFalse(self.changes.wait(2.5))

    def test_ignored_paths_are_not_reported(self):
        photo = self.folder / "2026-1" / "b.jpg"
        Image.new("RGB", (8, 8)).save(photo)
        self.assertTrue(self.changes.wait(8))
        self.changes.clear()
        time.sleep(0.5)
        self.watcher.ignore([photo])
        photo.unlink()  # what Move to Trash does
        self.assertFalse(self.changes.wait(2.5))

    def test_missing_folder_cannot_be_watched(self):
        self.assertFalse(LibraryWatcher(self.folder / "unplugged", lambda: None).start())
