import os
import threading
import time
from datetime import datetime

from photolibrary.core import media
from photolibrary.core.cache_cleanup import STALE_PART_SECONDS, cache_size, clear_thumbnail_cache, prune_thumbnail_cache
from photolibrary.core.constants import COVER_EDGE, HIGH_THUMBNAIL_EDGE

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

    def test_stopping_early_deletes_nothing(self):
        """Stopped while still working out what to keep: a partial list must not delete good thumbnails."""
        stop = threading.Event()
        stop.set()
        result = prune_thumbnail_cache(self.thumb_dir, [self.keep_entry], stop_event=stop)
        self.assertEqual(result.removed, 0)
        self.assertTrue(self.keep_grid.exists() and self.keep_cover.exists() and self.orphan.exists())

    def test_empty_bucket_folders_are_removed(self):
        bucket = self.orphan.parent
        others = [path for path in bucket.iterdir() if path != self.orphan]
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertEqual(bucket.exists(), bool(others))

    def test_clearing_deletes_every_thumbnail(self):
        files, size = cache_size(self.thumb_dir)
        self.assertEqual(files, 3)
        result = clear_thumbnail_cache(self.thumb_dir)
        self.assertEqual((result.removed, result.freed_bytes), (3, size))
        self.assertEqual(cache_size(self.thumb_dir), (0, 0))
        self.assertEqual(clear_thumbnail_cache(self.root / "missing"), result.__class__(0, 0, 0))

    def test_switching_quality_keeps_only_the_size_in_use(self):
        from PIL import Image
        big = self.write_photo(self.root / "big.jpg", size=(2000, 1500))
        stat = big.stat()
        with Image.open(media.get_cached_thumbnail(big, "image", stat.st_size, stat.st_mtime_ns,
                                                   HIGH_THUMBNAIL_EDGE)) as image:
            self.assertEqual(image.size, (1024, 768))
        high = media.get_cached_thumbnail(self.keep, "image", *self.keep_entry[1:], HIGH_THUMBNAIL_EDGE)
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry], grid_edge=HIGH_THUMBNAIL_EDGE)
        self.assertTrue(high.exists() and self.keep_cover.exists())
        self.assertFalse(self.keep_grid.exists())  # the standard size went
        # Back to Standard: the larger ones go once the standard ones are made again.
        grid = media.get_cached_thumbnail(self.keep, "image", *self.keep_entry[1:])
        prune_thumbnail_cache(self.thumb_dir, [self.keep_entry])
        self.assertTrue(grid.exists())
        self.assertFalse(high.exists())
        # Moving a file to the Trash removes both sizes.
        self.assertIn(high, media.thumbnail_cache_files(self.keep, *self.keep_entry[1:]))
