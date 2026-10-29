import os
import time
from datetime import datetime

from photolibrary.core import media
from photolibrary.core.cache_cleanup import STALE_PART_SECONDS, prune_thumbnail_cache
from photolibrary.core.constants import COVER_EDGE

from .base import TempAppTestCase


class CacheCleanupTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.keep = self.write_photo(self.root / "keep.jpg", datetime(2020, 1, 1))
        self.gone = self.write_photo(self.root / "gone.jpg", datetime(2020, 1, 2))
        stat = self.keep.stat()
        self.keep_entry = (str(self.keep), stat.st_size, stat.st_mtime_ns)
        self.keep_grid = media.get_cached_thumbnail(self.keep, "image", *self.keep_entry[1:])
        self.keep_cover = media.get_cached_thumbnail(self.keep, "image", *self.keep_entry[1:], COVER_EDGE)
        gone_stat = self.gone.stat()
        self.orphan = media.get_cached_thumbnail(self.gone, "image", gone_stat.st_size, gone_stat.st_mtime_ns)

    def test_removes_only_orphans(self):
        result = prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertEqual((result.kept, result.removed), (2, 1))
        self.assertGreater(result.freed_bytes, 0)
        self.assertTrue(self.keep_grid.exists() and self.keep_cover.exists())
        self.assertFalse(self.orphan.exists())

    def test_edited_file_leaves_old_thumbnail_to_prune(self):
        old = self.keep_grid
        later = time.time() + 10
        os.utime(self.keep, (later, later))  # an edit: new modification time, new cache key
        stat = self.keep.stat()
        fresh = media.get_cached_thumbnail(self.keep, "image", stat.st_size, stat.st_mtime_ns)
        prune_thumbnail_cache(self.thumb_dir, [(str(self.keep), stat.st_size, stat.st_mtime_ns)])
        self.assertTrue(fresh.exists())
        self.assertFalse(old.exists())

    def test_failure_markers_follow_their_file(self):
        broken = self.root / "broken.jpg"
        broken.write_bytes(b"nope")
        media.get_cached_thumbnail(broken, "image", 1, 1)
        marker = media.thumbnail_cache_files(broken, 1, 1)[1]
        self.assertTrue(marker.exists())
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry, (str(broken), 1, 1)])
        self.assertTrue(marker.exists())
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertFalse(marker.exists())

    def test_old_partial_files_are_removed_recent_ones_kept(self):
        folder = self.keep_grid.parent
        old_part = folder / "abc.1-2.part.jpg"
        new_part = folder / "def.1-3.part.jpg"
        for part in (old_part, new_part):
            part.write_bytes(b"x")
        stale = time.time() - STALE_PART_SECONDS - 60
        os.utime(old_part, (stale, stale))
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertFalse(old_part.exists())
        self.assertTrue(new_part.exists())

    def test_empty_index_never_wipes_the_cache(self):
        result = prune_thumbnail_cache(self.thumb_dir, [])
        self.assertEqual(result.removed, 0)
        self.assertTrue(self.orphan.exists())

    def test_empty_bucket_folders_are_removed(self):
        bucket = self.orphan.parent
        others = [path for path in bucket.iterdir() if path != self.orphan]
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertEqual(bucket.exists(), bool(others))
