import subprocess
import threading
import unittest
from datetime import datetime

from photolibrary.core import media
from photolibrary.core.database import MediaRepository
from photolibrary.core.indexer import MediaIndexer
from photolibrary.core.live_photos import PairingRow, pair_live_photos
from photolibrary.core.trash import trash_items

from .base import TempAppTestCase


def row(id_, path, media_type, when=None, duration=None, tagged=False):
    return PairingRow(id_, path, media_type, datetime.fromisoformat(when) if when else None, duration, tagged)


class PairingTests(unittest.TestCase):
    def test_tagged_motion_clip_pairs_with_its_photo(self):
        rows = [row(1, "/lib/2026-4/IMG_4330.HEIC", "image", "2026-04-29T09:11:00"),
                row(2, "/lib/2026-4/IMG_4330.MOV", "video", "2026-04-29T09:11:00", 1.04, tagged=True)]
        self.assertEqual(pair_live_photos(rows), [(1, 2)])

    def test_untagged_short_clip_close_in_time_pairs(self):
        rows = [row(1, "/lib/a/IMG_1.JPG", "image", "2026-03-07T00:06:18"),
                row(2, "/lib/a/img_1.mov", "video", "2026-03-07T00:06:16", 1.5)]
        self.assertEqual(pair_live_photos(rows), [(1, 2)])

    def test_real_videos_are_not_swallowed(self):
        long_video = [row(1, "/lib/a/IMG_2.JPG", "image", "2026-03-07T10:00:00"),
                      row(2, "/lib/a/IMG_2.MOV", "video", "2026-03-07T10:00:00", 42.0)]
        far_apart = [row(3, "/lib/a/IMG_3.JPG", "image", "2026-03-07T10:00:00"),
                     row(4, "/lib/a/IMG_3.MOV", "video", "2026-03-08T10:00:00", 2.0)]
        self.assertEqual(pair_live_photos(long_video + far_apart), [])

    def test_different_folders_or_names_do_not_pair(self):
        rows = [row(1, "/lib/a/IMG_5.HEIC", "image"), row(2, "/lib/b/IMG_5.MOV", "video", duration=1.0, tagged=True),
                row(3, "/lib/a/IMG_6.HEIC", "image"), row(4, "/lib/a/IMG_7.MOV", "video", duration=1.0, tagged=True)]
        self.assertEqual(pair_live_photos(rows), [])

    def test_ambiguous_groups_are_left_alone(self):
        rows = [row(1, "/lib/a/IMG_8.HEIC", "image"), row(2, "/lib/a/IMG_8.JPG", "image"),
                row(3, "/lib/a/IMG_8.MOV", "video", duration=1.0, tagged=True)]
        self.assertEqual(pair_live_photos(rows), [])



def make_clip(path, seconds, creation, live_tag=False):
    extra = ["-metadata", "com.apple.quicktime.live-photo.auto=1", "-movflags", "use_metadata_tags"] if live_tag else []
    subprocess.run([
        media.imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"color=c=green:s=160x120:d={seconds}", "-pix_fmt", "yuv420p",
        "-metadata", f"creation_time={creation}", *extra, str(path),
    ], check=True)


@unittest.skipUnless(media.FFMPEG_ENABLED, "ffmpeg not installed")
class LivePhotoLibraryTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.make_library()
        self.write_photo(self.library / "IMG_0001.HEIC.jpg", datetime(2026, 4, 29, 9, 11))  # unrelated name
        self.photo = self.write_photo(self.library / "IMG_4330.JPG", datetime(2026, 4, 29, 9, 11, 0))
        self.motion = self.library / "IMG_4330.MOV"
        make_clip(self.motion, 1.5, "2026-04-29T09:11:00Z", live_tag=True)
        self.video = self.library / "MOV_9999.MOV"
        make_clip(self.video, 3, "2026-04-28T10:00:00Z")
        self.events = []
        MediaIndexer(self.library, threading.Event(), 1, self.events.append).run()
        self.repo = MediaRepository()

    def test_tag_is_read_from_the_clip(self):
        self.assertTrue(media.read_metadata(self.motion, "video").live_motion)
        self.assertFalse(media.read_metadata(self.video, "video").live_motion)

    def test_motion_clip_is_folded_into_its_photo(self):
        items = {item.name: item for item in self.repo.query(self.library)}
        self.assertNotIn("IMG_4330.MOV", items)
        self.assertTrue(items["IMG_4330.JPG"].is_live)
        self.assertEqual(items["IMG_4330.JPG"].live_video, str(self.motion.resolve()))
        self.assertIn("MOV_9999.MOV", items)  # an ordinary video stays a video
        self.assertEqual(self.repo.counts(self.library), {"all": 3, "photos": 2, "videos": 1})
        self.assertEqual(self.events[-1]["live_photos"], 1)
        self.assertEqual(self.events[-1]["total_indexed"], 3)
        self.assertEqual(self.repo.library_analytics(self.library).live_photos, 1)

    def test_get_returns_the_live_link(self):
        photo = next(item for item in self.repo.query(self.library) if item.name == "IMG_4330.JPG")
        self.assertEqual(self.repo.get(photo.id).live_video_id, photo.live_video_id)

    def test_trashing_a_live_photo_takes_its_motion_clip(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()

        def to_bin(path):
            path.rename(bin_dir / path.name)
            return True

        photo = next(item for item in self.repo.query(self.library) if item.name == "IMG_4330.JPG")
        result = trash_items(self.library, [photo], to_bin, self.repo)
        self.assertEqual(result.failed, [])
        self.assertEqual(sorted(path.name for path in bin_dir.iterdir()), ["IMG_4330.JPG", "IMG_4330.MOV"])
        names = {item.name for item in self.repo.query(self.library)}
        self.assertEqual(names, {"IMG_0001.HEIC.jpg", "MOV_9999.MOV"})
        # Rescanning doesn't bring a stray clip back.
        MediaIndexer(self.library, threading.Event(), 2, lambda event: None).run()
        self.assertEqual(len(self.repo.query(self.library)), 2)
