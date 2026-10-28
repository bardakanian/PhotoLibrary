import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photolibrary.core import database, media, settings


class TempAppTestCase(unittest.TestCase):
    """Points the app-data directory at a temporary folder for each test."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app_dir = self.root / "app-data"
        self.db_file = self.app_dir / "library.sqlite3"
        self.thumb_dir = self.app_dir / "thumbnails"
        self.config_file = self.app_dir / "config.json"
        self.patches = [
            patch.object(database, "APP_DIR", self.app_dir),
            patch.object(database, "DB_FILE", self.db_file),
            patch.object(database, "THUMBNAIL_DIR", self.thumb_dir),
            patch.object(media, "THUMBNAIL_DIR", self.thumb_dir),
            patch.object(settings, "APP_DIR", self.app_dir),
            patch.object(settings, "CONFIG_FILE", self.config_file),
            # Spotlight lookups are slow and depend on the machine; tests use EXIF or mtime.
            patch.object(media, "parse_macos_metadata_date", return_value=None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def make_library(self, name: str = "library") -> Path:
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def write_photo(path: Path, taken: datetime | None = None, size=(16, 12), *,
                    model: str | None = None, make: str | None = None, orientation: int | None = None,
                    original_tag: bool = True) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        exif = Image.Exif()
        if taken is not None:
            stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
            if original_tag:
                exif.get_ifd(0x8769)[36867] = stamp
            else:
                exif[306] = stamp
        if make:
            exif[271] = make
        if model:
            exif[272] = model
        if orientation:
            exif[0x0112] = orientation
        Image.new("RGB", size, "navy").save(path, exif=exif)
        return path
