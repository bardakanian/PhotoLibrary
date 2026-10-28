import os
import subprocess
import unittest
from datetime import datetime
from unittest.mock import patch

from PIL import Image

from photolibrary.core import media
from photolibrary.core.constants import THUMBNAIL_EDGE

from .base import TempAppTestCase


class DateDetectionTests(TempAppTestCase):
    def test_exif_original_date_is_read_from_sub_ifd(self):
        path = self.write_photo(self.root / "a.jpg", datetime(2019, 3, 6, 10, 0, 0))
        self.assertEqual(media.get_capture_date(path, "image"), datetime(2019, 3, 6, 10, 0, 0))

    def test_ifd0_datetime_is_used_when_original_is_missing(self):
        path = self.write_photo(self.root / "b.jpg", datetime(2020, 1, 2, 3, 4, 5), original_tag=False)
        self.assertEqual(media.get_capture_date(path, "image"), datetime(2020, 1, 2, 3, 4, 5))

    def test_image_without_metadata_falls_back_to_modification_time(self):
        path = self.root / "plain.png"
        Image.new("RGB", (4, 4)).save(path)
        stamp = datetime(2015, 6, 7, 8, 9, 10).timestamp()
        os.utime(path, (stamp, stamp))
        self.assertEqual(media.get_capture_date(path, "image"), datetime(2015, 6, 7, 8, 9, 10))

    def test_macos_metadata_is_used_before_modification_time(self):
        path = self.root / "plain.png"
        Image.new("RGB", (4, 4)).save(path)
        with patch.object(media, "parse_macos_metadata_date", return_value=datetime(2011, 1, 1)):
            self.assertEqual(media.get_capture_date(path, "image"), datetime(2011, 1, 1))

    def test_unreadable_image_still_gets_a_date(self):
        path = self.root / "broken.jpg"
        path.write_bytes(b"not an image")
        self.assertIsNotNone(media.read_metadata(path, "image").captured_at)

    def test_image_metadata_reads_camera_and_oriented_size(self):
        path = self.write_photo(self.root / "c.jpg", datetime(2021, 5, 5), size=(40, 30),
                                make="Canon", model="Canon EOS R5", orientation=6)
        meta = media.read_metadata(path, "image")
        self.assertEqual((meta.width, meta.height), (30, 40))
        self.assertEqual(meta.camera, "Canon EOS R5")

    def test_camera_name_adds_make_when_model_lacks_it(self):
        self.assertEqual(media._camera_name("Apple", "iPhone 15 Pro"), "Apple iPhone 15 Pro")
        self.assertEqual(media._camera_name("NIKON CORPORATION", "NIKON D850"), "NIKON D850")
        self.assertIsNone(media._camera_name(None, None))

    def test_ffmpeg_probe_parsing(self):
        stderr = """
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mov':
  Metadata:
    creation_time   : 2022-07-04T18:30:00.000000Z
    com.apple.quicktime.make: Apple
    com.apple.quicktime.model: iPhone 12
    com.apple.quicktime.creationdate: 2022-07-04T11:30:00-0700
  Duration: 00:01:05.50, start: 0.000000, bitrate: 1000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main) (hvc1 / 0x31637668), yuv420p(tv), 1920x1080, 30 fps
    Side data:
      displaymatrix: rotation of -90.00 degrees
"""
        meta = media.parse_ffmpeg_probe(stderr)
        self.assertEqual((meta.width, meta.height), (1080, 1920))
        self.assertAlmostEqual(meta.duration, 65.5)
        self.assertEqual(meta.camera, "Apple iPhone 12")
        expected = datetime.fromisoformat("2022-07-04T11:30:00-07:00").astimezone().replace(tzinfo=None)
        self.assertEqual(meta.captured_at, expected)

    @unittest.skipUnless(media.FFMPEG_ENABLED, "ffmpeg not installed")
    def test_real_video_metadata(self):
        source = self.root / "clip.mp4"
        subprocess.run([
            media.imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1.5",
            "-pix_fmt", "yuv420p", "-metadata", "creation_time=2018-02-03T04:05:06Z", str(source),
        ], capture_output=True, check=True)
        meta = media.read_metadata(source, "video")
        self.assertEqual((meta.width, meta.height), (320, 240))
        self.assertAlmostEqual(meta.duration, 1.5, delta=0.1)
        expected = datetime.fromisoformat("2018-02-03T04:05:06+00:00").astimezone().replace(tzinfo=None)
        self.assertEqual(meta.captured_at, expected)


class ThumbnailTests(TempAppTestCase):
    def test_image_thumbnail_respects_exif_rotation_and_keeps_aspect(self):
        source = self.write_photo(self.root / "rotated.jpg", size=(4000, 3000), orientation=6)
        thumbnail = media.get_cached_thumbnail(source, "image", 1, 1)
        with Image.open(thumbnail) as result:
            # Portrait after rotation, no padding.
            self.assertEqual(result.size, (THUMBNAIL_EDGE * 3 // 4, THUMBNAIL_EDGE))

    def test_small_images_are_not_upscaled(self):
        source = self.write_photo(self.root / "tiny.jpg", size=(20, 10))
        with Image.open(media.get_cached_thumbnail(source, "image", 1, 1)) as result:
            self.assertEqual(result.size, (20, 10))

    def test_transparent_png_thumbnail(self):
        source = self.root / "alpha.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(source)
        with Image.open(media.get_cached_thumbnail(source, "image", 1, 1)) as result:
            self.assertEqual(result.mode, "RGB")

    def test_cached_thumbnail_is_reused(self):
        source = self.write_photo(self.root / "a.jpg")
        first = media.get_cached_thumbnail(source, "image", 1, 1)
        with patch.object(media, "_image_thumbnail") as generate:
            self.assertEqual(media.get_cached_thumbnail(source, "image", 1, 1), first)
            generate.assert_not_called()

    def test_failed_thumbnail_is_not_retried(self):
        source = self.root / "broken.jpg"
        source.write_bytes(b"not an image")
        self.assertIsNone(media.get_cached_thumbnail(source, "image", 1, 1))
        with patch.object(media, "_image_thumbnail") as generate:
            self.assertIsNone(media.get_cached_thumbnail(source, "image", 1, 1))
            generate.assert_not_called()
        # A changed file (new size/mtime) gets a fresh attempt.
        with patch.object(media, "_image_thumbnail") as generate:
            media.get_cached_thumbnail(source, "image", 2, 2)
            generate.assert_called_once()

    def test_missing_source_is_retried_later(self):
        source = self.root / "unmounted" / "photo.jpg"
        self.assertIsNone(media.get_cached_thumbnail(source, "image", 1, 1))
        self.assertEqual([path for path in media.thumbnail_cache_files(source, 1, 1) if path.exists()], [])

    @unittest.skipUnless(media.FFMPEG_ENABLED, "ffmpeg not installed")
    def test_video_shorter_than_one_second_gets_thumbnail(self):
        source = self.root / "short.mp4"
        subprocess.run([
            media.imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=0.4",
            "-pix_fmt", "yuv420p", str(source),
        ], capture_output=True, check=True)
        thumbnail = media.get_cached_thumbnail(source, "video", 1, 1)
        self.assertIsNotNone(thumbnail)
        with Image.open(thumbnail) as result:
            self.assertEqual(result.size, (THUMBNAIL_EDGE, THUMBNAIL_EDGE * 3 // 4))


class DisplayImageTests(TempAppTestCase):
    def test_display_image_is_rotated_and_capped(self):
        source = self.write_photo(self.root / "big.jpg", size=(4000, 2000), orientation=6)
        image = media.load_display_image(source, 1000)
        self.assertEqual(image.size, (500, 1000))

    def test_display_image_keeps_small_images(self):
        source = self.write_photo(self.root / "small.jpg", size=(300, 200))
        self.assertEqual(media.load_display_image(source, 1000).size, (300, 200))


class FileManagerTests(unittest.TestCase):
    def test_reveal_commands(self):
        from pathlib import Path
        path = Path("/library/a/photo.jpg")
        self.assertEqual(media.reveal_command(path, "darwin"), ["open", "-R", str(path)])
        self.assertEqual(media.reveal_command(path, "win32")[0], "explorer")
        self.assertEqual(media.reveal_command(path, "linux"), ["xdg-open", str(path.parent)])

    def test_reveal_reports_failure(self):
        from pathlib import Path
        with patch.object(media.subprocess, "Popen", side_effect=OSError("nope")):
            self.assertEqual(media.reveal_in_file_manager(Path("/x.jpg")), (False, "nope"))
