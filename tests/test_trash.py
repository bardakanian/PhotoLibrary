import dataclasses
import shutil
import threading
from datetime import datetime

from photolibrary.core import media
from photolibrary.core.database import MediaRepository
from photolibrary.core.indexer import MediaIndexer
from photolibrary.core.trash import trash_items

from .base import TempAppTestCase


class TrashTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.make_library()
        self.bin = self.root / "fake-trash"
        self.bin.mkdir()
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            self.write_photo(self.library / name, datetime(2020, 1, 1))
        MediaIndexer(self.library, threading.Event(), 1, lambda event: None).run()
        self.repo = MediaRepository()
        self.items = {item.name: item for item in self.repo.query(self.library)}

    def fake_trash(self, path):
        shutil.move(str(path), self.bin / path.name)
        return True

    def test_trashes_files_and_cleans_index_and_thumbnails(self):
        target = self.items["a.jpg"]
        thumbnail = media.get_cached_thumbnail(target.path, "image", target.file_size, target.modified_ns)
        self.assertTrue(thumbnail.exists())
        result = trash_items(self.library, [target, self.items["b.jpg"]], self.fake_trash)
        self.assertEqual(result.trashed_ids, {target.id, self.items["b.jpg"].id})
        self.assertEqual(result.failed, [])
        self.assertTrue((self.bin / "a.jpg").exists())
        self.assertFalse(thumbnail.exists())
        self.assertEqual([item.name for item in self.repo.query(self.library)], ["c.jpg"])

    def test_failures_are_reported_and_kept_in_index(self):
        def refuse(path):
            return path.name != "b.jpg" and self.fake_trash(path)

        result = trash_items(self.library, [self.items["a.jpg"], self.items["b.jpg"]], refuse)
        self.assertEqual([item.name for item in result.trashed], ["a.jpg"])
        self.assertEqual([item.name for item, _reason in result.failed], ["b.jpg"])
        self.assertEqual(sorted(item.name for item in self.repo.query(self.library)), ["b.jpg", "c.jpg"])

    def test_os_errors_are_reported(self):
        def broken(path):
            raise PermissionError("read-only volume")

        result = trash_items(self.library, [self.items["a.jpg"]], broken)
        self.assertEqual(result.failed[0][1], "read-only volume")
        self.assertEqual(len(self.repo.query(self.library)), 3)

    def test_refuses_files_outside_library(self):
        outside = self.write_photo(self.root / "elsewhere" / "x.jpg")
        stray = dataclasses.replace(self.items["a.jpg"], file_path=str(outside))
        calls = []
        result = trash_items(self.library, [stray], lambda path: calls.append(path) or True)
        self.assertEqual(calls, [])
        self.assertIn("outside", result.failed[0][1])
        self.assertTrue(outside.exists())

    def test_already_missing_file_is_dropped_from_index(self):
        self.items["c.jpg"].path.unlink()
        calls = []
        result = trash_items(self.library, [self.items["c.jpg"]], lambda path: calls.append(path) or True)
        self.assertEqual(calls, [])
        self.assertEqual(result.trashed_ids, {self.items["c.jpg"].id})
        self.assertEqual(len(self.repo.query(self.library)), 2)
