import hashlib
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

from .constants import COVER_EDGE, THUMBNAIL_DIR, THUMBNAIL_EDGE, VIDEO_EXTENSIONS
from .database import ensure_app_dirs
from .models import MediaMetadata

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_ENABLED = True
except ImportError:
    HEIC_ENABLED = False

try:
    import imageio_ffmpeg
    FFMPEG_ENABLED = True
except ImportError:
    imageio_ffmpeg = None
    FFMPEG_ENABLED = False

Image.MAX_IMAGE_PIXELS = None

_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED, _TAG_DATETIME = 36867, 36868, 306
_TAG_MAKE, _TAG_MODEL, _TAG_ORIENTATION = 271, 272, 274
_EXIF_DATE_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
# EXIF orientations 5-8 rotate by 90 degrees, so width and height swap when displayed.
_SWAPPED_ORIENTATIONS = {5, 6, 7, 8}
_THUMB_BACKGROUND = (24, 26, 30)
# A console program started from a windowless app (pythonw, the Windows launcher) gets a
# console window of its own; this keeps ffmpeg from flashing one up for every video.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def media_type_for_path(path: Path) -> str:
    return "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image"


# Clocks that were never set, and "zero" timestamps, show up as these dates. They are never real
# capture times; treating them as missing lets the next source of a date be used instead.
_PLACEHOLDER_DAYS = {(1904, 1, 1), (1970, 1, 1), (1980, 1, 1)}


def is_placeholder_date(value: datetime) -> bool:
    """True for epoch and unset-clock dates (checked a day either side to allow for time zones)."""
    if value.year < 1900 or value.year > datetime.now().year + 1:
        return True
    for offset in (-1, 0, 1):
        day = value + timedelta(days=offset)
        if (day.year, day.month, day.day) in _PLACEHOLDER_DAYS:
            return True
    return False


def _usable(value: datetime | None) -> datetime | None:
    return value if value is not None and not is_placeholder_date(value) else None


_SPOTLIGHT_FIELDS = ("kMDItemContentCreationDate", "kMDItemFSCreationDate")
# Spotlight dates within this many seconds of the file's own creation time carry no extra information.
_SAME_MOMENT_SECONDS = 2


def _parse_spotlight_value(value: str) -> datetime | None:
    value = value.strip()
    if not value or value == "(null)":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z").astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def choose_spotlight_date(content_created: datetime | None, file_created: datetime | None) -> datetime | None:
    """Spotlight's content date, but only when it says more than the file system does.

    For files without embedded dates, Spotlight reports the file's own creation time as the
    content date. That is when the file was copied to this disk (a backup, an export, a
    download), not when the photo was taken, so it must not be used as a capture date.
    """
    if content_created is None:
        return None
    if file_created is not None and abs((content_created - file_created).total_seconds()) <= _SAME_MOMENT_SECONDS:
        return None
    return content_created


def parse_macos_metadata_date(path: Path) -> datetime | None:
    """A capture date Spotlight found inside the file, or None."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["mdls", "-raw", *(arg for field in _SPOTLIGHT_FIELDS for arg in ("-name", field)), str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # With -raw, values are separated by NUL characters, in the order requested.
    values = result.stdout.split("\0")
    if len(values) < 2:
        return None
    return choose_spotlight_date(_parse_spotlight_value(values[0]), _parse_spotlight_value(values[1]))


def _parse_exif_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = str(value).strip().strip("\x00")
    for date_format in _EXIF_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None


def _camera_name(make, model) -> str | None:
    make = str(make or "").strip().strip("\x00")
    model = str(model or "").strip().strip("\x00")
    if not model:
        return make or None
    # Many cameras repeat the maker in the model ("Canon Canon EOS R5"); keep it once.
    if make and not model.lower().startswith(make.split()[0].lower()):
        return f"{make} {model}"
    return model


def read_image_metadata(path: Path) -> MediaMetadata:
    """Date, oriented size and camera from a single open of the file."""
    with Image.open(path) as image:
        width, height = image.size
        exif = image.getexif()
        captured = camera = None
        if exif:
            # DateTimeOriginal lives in the Exif sub-IFD; DateTime (306) is in IFD0.
            try:
                sub_ifd = exif.get_ifd(_EXIF_IFD)
            except (KeyError, ValueError, TypeError):
                sub_ifd = {}
            for tag in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED):
                captured = _parse_exif_date(sub_ifd.get(tag) or exif.get(tag))
                if captured:
                    break
            if captured is None:
                captured = _parse_exif_date(exif.get(_TAG_DATETIME))
            camera = _camera_name(exif.get(_TAG_MAKE), exif.get(_TAG_MODEL))
            if exif.get(_TAG_ORIENTATION) in _SWAPPED_ORIENTATIONS:
                width, height = height, width
        return MediaMetadata(captured, width, height, camera)


def get_image_exif_date(path: Path) -> datetime | None:
    try:
        return read_image_metadata(path).captured_at
    except (OSError, ValueError, TypeError, SyntaxError):
        return None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SIZE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
_ROTATION = re.compile(r"(?:rotate\s*:\s*|rotation of\s*)(-?\d+(?:\.\d+)?)")


def _parse_ffmpeg_date(value: str) -> datetime | None:
    normalized = value.strip().replace("Z", "+00:00")
    for parser in (
        datetime.fromisoformat,
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
        lambda item: datetime.strptime(item, "%Y/%m/%d %H:%M:%S"),
    ):
        try:
            parsed = parser(normalized)
            return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            continue
    return None


def parse_ffmpeg_probe(stderr: str) -> MediaMetadata:
    """Read date, size, duration and camera from `ffmpeg -i` output."""
    dates: list[str] = []
    make = model = None
    live_motion = False
    width = height = None
    duration = None
    rotation = 0.0
    for line in stderr.splitlines():
        stripped = line.strip()
        key, _, value = stripped.partition(":")
        key_lower = key.strip().lower()
        if key_lower == "com.apple.quicktime.creationdate":
            # The camera's own local capture time; container times may be the export time.
            dates.insert(0, value.strip())
        elif "creation_time" in key_lower or key_lower in {"date", "date-eng", "creation date"}:
            dates.append(value.strip())
        elif key_lower in {"com.apple.quicktime.model", "model"}:
            model = model or value.strip()
        elif key_lower in {"com.apple.quicktime.make", "make"}:
            make = make or value.strip()
        elif key_lower.startswith("com.apple.quicktime.live-photo"):
            live_motion = True
        if duration is None and (match := _DURATION.search(stripped)):
            hours, minutes, seconds = match.groups()
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if width is None and (match := _VIDEO_SIZE.search(stripped)):
            width, height = int(match.group(1)), int(match.group(2))
        if match := _ROTATION.search(stripped):
            rotation = float(match.group(1))
    if width and height and round(abs(rotation)) % 180 == 90:
        width, height = height, width
    captured = next((parsed for parsed in map(_parse_ffmpeg_date, filter(None, dates)) if parsed), None)
    return MediaMetadata(captured, width, height, _camera_name(make, model), duration, live_motion)


def read_video_metadata(path: Path) -> MediaMetadata:
    if not FFMPEG_ENABLED:
        return MediaMetadata(None)
    try:
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=10, check=False, errors="replace", **_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return MediaMetadata(None)
    return parse_ffmpeg_probe(result.stderr)


def get_video_metadata_date(path: Path) -> datetime | None:
    return read_video_metadata(path).captured_at


def get_filesystem_date(path: Path) -> datetime | None:
    """The earlier of the file's creation and modification times.

    Copying a file (to a backup drive, a new Mac, a cloud export) resets its creation time
    to the copy time but keeps its modification time, so the earlier one is closer to when
    the photo or video was actually made.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    candidates = [datetime.fromtimestamp(stamp) for stamp in
                  (stat.st_mtime, getattr(stat, "st_birthtime", stat.st_mtime)) if stamp > 0]
    # Ignore zero and unset-clock timestamps some filesystems and memory cards report.
    valid = [value for value in candidates if not is_placeholder_date(value)]
    return min(valid or candidates, default=None)


def read_metadata(path: Path, media_type: str) -> MediaMetadata:
    """Everything the index stores about one file. Date fallbacks match get_capture_date."""
    if media_type == "image":
        try:
            found = read_image_metadata(path)
        except (OSError, ValueError, TypeError, SyntaxError):
            found = MediaMetadata(None)
    else:
        found = read_video_metadata(path)
    # Embedded date first, then a date Spotlight found in the content, then the file's own times.
    captured = _usable(found.captured_at) or _usable(parse_macos_metadata_date(path)) or get_filesystem_date(path)
    return MediaMetadata(captured, found.width, found.height, found.camera, found.duration, found.live_motion)


def get_capture_date(path: Path, media_type: str) -> datetime | None:
    return read_metadata(path, media_type).captured_at


def thumbnail_cache_path(path: Path, file_size: int, modified_ns: int, edge: int = THUMBNAIL_EDGE) -> Path:
    signature = f"{path}|{file_size}|{modified_ns}|{edge}"
    digest = hashlib.sha256(signature.encode()).hexdigest()
    # Two-level fan-out keeps directories small for libraries with tens of thousands of files.
    return THUMBNAIL_DIR / digest[:2] / f"{digest}.jpg"


def _failure_marker(target: Path) -> Path:
    return target.with_suffix(".failed")


def thumbnail_cache_files(path: Path, file_size: int, modified_ns: int) -> list[Path]:
    """Every cache file that may exist for one media item (grid thumbnail and cover)."""
    files = []
    for edge in (THUMBNAIL_EDGE, COVER_EDGE):
        target = thumbnail_cache_path(path, file_size, modified_ns, edge)
        files += [target, _failure_marker(target)]
    return files


def _flatten(image: Image.Image) -> Image.Image:
    """RGB for JPEG output; transparent areas become the dark placeholder colour."""
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, _THUMB_BACKGROUND)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _image_thumbnail(source: Path, target: Path, edge: int = THUMBNAIL_EDGE) -> None:
    with Image.open(source) as image:
        if getattr(image, "is_animated", False):
            try:
                image.seek(0)
            except EOFError:
                pass
        # Let JPEGs decode at a reduced scale. This must happen before exif_transpose
        # loads the full image; a square request covers both rotated orientations.
        image.draft("RGB", (edge, edge))
        image = ImageOps.exif_transpose(image)
        image = _flatten(image)
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        image.save(target, "JPEG", quality=85, optimize=True)


def _video_thumbnail(source: Path, target: Path, edge: int = THUMBNAIL_EDGE) -> bool:
    scale = f"scale='if(gt(iw,ih),{edge},-2)':'if(gt(iw,ih),-2,{edge})'"
    # Prefer a frame one second in (skips black fade-ins); clips shorter than that
    # produce nothing, so fall back to the very first frame.
    for seek in (["-ss", "1"], []):
        try:
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", *seek, "-i", str(source),
                "-frames:v", "1", "-vf", scale, "-q:v", "3", str(target),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False, **_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if target.exists() and target.stat().st_size > 0:
            return True
    return False


def get_cached_thumbnail(path: Path, media_type: str, file_size: int, modified_ns: int,
                         edge: int = THUMBNAIL_EDGE) -> Path | None:
    """Return the cached thumbnail (longest side `edge`), generating it if needed.

    Slow; call from a worker thread.
    """
    ensure_app_dirs()
    target = thumbnail_cache_path(path, file_size, modified_ns, edge)
    if target.exists():
        return target
    failed = _failure_marker(target)
    if failed.exists():
        return None
    if media_type == "video" and not FFMPEG_ENABLED:
        return None
    # Each attempt writes its own temporary file and renames it into place, so readers never
    # see a half-written JPEG and two threads creating the same thumbnail can't clash.
    partial = target.with_name(f"{target.stem}.{os.getpid()}-{threading.get_ident()}.part.jpg")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if media_type == "video":
            if not _video_thumbnail(path, partial, edge):
                raise OSError("ffmpeg produced no frame")
        else:
            _image_thumbnail(path, partial, edge)
        partial.replace(target)
        return target
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        try:
            partial.unlink(missing_ok=True)
            if target.exists():
                return target  # another thread finished it meanwhile
            # Remember the failure so the file isn't re-decoded every time it is shown.
            # Skip this when the source is missing (e.g. an unmounted drive) so it retries later.
            # The cache key includes size and mtime, so editing the file also retries.
            if path.exists():
                failed.touch()
        except OSError:
            pass
        return None


def load_display_image(path: Path, max_edge: int) -> Image.Image:
    """Decode a photo for the viewer: EXIF-rotated, at most `max_edge` on its longest side."""
    with Image.open(path) as image:
        if getattr(image, "is_animated", False):
            try:
                image.seek(0)
            except EOFError:
                pass
        image.draft("RGB", (max_edge, max_edge))
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGBA") if image.mode in {"RGBA", "LA", "P"} else image.convert("RGB")
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return image


def reveal_command(path: Path, platform: str = sys.platform) -> list[str]:
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform.startswith("win"):
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path.parent)]


def reveal_in_file_manager(path: Path) -> tuple[bool, str | None]:
    try:
        subprocess.Popen(reveal_command(path))
        return True, None
    except OSError as exc:
        return False, str(exc)


def open_in_default_app(path: Path) -> tuple[bool, str | None]:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True, None
    except OSError as exc:
        return False, str(exc)
