import os
from pathlib import Path

APP_NAME = "Photo Library"
# PHOTO_LIBRARY_HOME points the app at a separate data folder (demos, smoke tests).
APP_DIR = Path(os.environ.get("PHOTO_LIBRARY_HOME") or Path.home() / ".photo_library").expanduser()
CONFIG_FILE = APP_DIR / "config.json"
DB_FILE = APP_DIR / "library.sqlite3"
THUMBNAIL_DIR = APP_DIR / "thumbnails"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif",
    ".webp", ".heic", ".heif",
}
VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".wmv", ".mpeg",
    ".mpg", ".3gp",
}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
# Longest side of a cached thumbnail. Aspect ratio is kept (no padding) so the grid can
# crop to square cells; 512 px stays sharp for a 256-point cell on a Retina display.
THUMBNAIL_EDGE = 512
# Larger thumbnails used only as Years/Months tile covers (a few hundred per library), which
# are drawn up to about 1,000 device pixels wide.
COVER_EDGE = 1600
BATCH_COMMIT_SIZE = 250
# Bump when read_metadata learns something new, so the next scan re-reads every file.
# 2: no copy-time dates for metadata-less files; 3: ignore epoch/unset-clock dates;
# 4: detect Live Photo motion clips
METADATA_VERSION = 4
