import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photolibrary.core.media import load_display_image
from photolibrary.ui.viewer_images import decode_for_faces


@unittest.skipIf(os.environ.get("SKIP_UI_TESTS"), "SKIP_UI_TESTS is set")
class FaceDecodingTests(unittest.TestCase):
    def test_system_decoder_matches_pillow_in_size_and_orientation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rotated.jpg"
            exif = Image.Exif()
            exif[0x0112] = 6  # stored sideways: shown rotated by 90 degrees
            Image.new("RGB", (3000, 2000), (200, 30, 30)).save(path, exif=exif)
            fast, reference = decode_for_faces(path, 1280), load_display_image(path, 1280)
            self.assertEqual(fast.mode, "RGB")
            self.assertEqual(fast.size, reference.size)
            self.assertEqual(fast.size, (853, 1280))
            self.assertLess(abs(fast.getpixel((400, 600))[0] - 200), 6)
            self.assertIsNone(decode_for_faces(Path(folder) / "missing.jpg", 1280))
