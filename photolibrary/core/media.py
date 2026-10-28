import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .constants import THUMBNAIL_DIR, THUMBNAIL_EDGE, VIDEO_EXTENSIONS
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


def media_type_for_path(path: Path) -> str:
    return "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image"


def parse_macos_metadata_date(path: Path) -> datetime | None:
    if sys.platform != "darwin":
        return None
    for field in ("kMDItemContentCreationDate", "kMDItemFSCreationDate"):
        try:
            result = subprocess.run(
                ["mdls", "-raw", "-name", field, str(path)],
                capture_output=True, text=True, timeout=5, check=False,
            )
            value = result.stdout.strip()
            if not value or value == "(null)":
                continue
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
            return parsed.astimezone().replace(tzinfo=None)
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return None


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
    return MediaMetadata(captured, width, height, _camera_name(make, model), duration)


def read_video_metadata(path: Path) -> MediaMetadata:
    if not FFMPEG_ENABLED:
        return MediaMetadata(None)
    try:
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=10, check=False, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return MediaMetadata(None)
    return parse_ffmpeg_probe(result.stderr)


def get_video_metadata_date(path: Path) -> datetime | None:
    return read_video_metadata(path).captured_at


def get_filesystem_creation_date(path: Path) -> datetime | None:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime))
    except OSError:
        return None


def _modified_date(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def read_metadata(path: Path, media_type: str) -> MediaMetadata:
    """Everything the index stores about one file. Date fallbacks match get_capture_date."""
    if media_type == "image":
        try:
            found = read_image_metadata(path)
        except (OSError, ValueError, TypeError, SyntaxError):
            found = MediaMetadata(None)
        captured = found.captured_at or parse_macos_metadata_date(path)
    else:
        found = read_video_metadata(path)
        captured = found.captured_at or parse_macos_metadata_date(path) or get_filesystem_creation_date(path)
    captured = captured or _modified_date(path)
    return MediaMetadata(captured, found.width, found.height, found.camera, found.duration)


def get_capture_date(path: Path, media_type: str) -> datetime | None:
    return read_metadata(path, media_type).captured_at


def thumbnail_cache_path(path: Path, file_size: int, modified_ns: int) -> Path:
    signature = f"{path}|{file_size}|{modified_ns}|{THUMBNAIL_EDGE}"
    digest = hashlib.sha256(signature.encode()).hexdigest()
    # Two-level fan-out keeps directories small for libraries with tens of thousands of files.
    return THUMBNAIL_DIR / digest[:2] / f"{digest}.jpg"


def _failure_marker(target: Path) -> Path:
    return target.with_suffix(".failed")


def thumbnail_cache_files(path: Path, file_size: int, modified_ns: int) -> list[Path]:
    """Every cache file that may exist for one media item."""
    target = thumbnail_cache_path(path, file_size, modified_ns)
    return [target, _failure_marker(target)]


def _flatten(image: Image.Image) -> Image.Image:
    """RGB for JPEG output; transparent areas become the dark placeholder colour."""
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, _THUMB_BACKGROUND)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _image_thumbnail(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        if getattr(image, "is_animated", False):
            try:
                image.seek(0)
            except EOFError:
                pass
        # Let JPEGs decode at a reduced scale. This must happen before exif_transpose
        # loads the full image; a square request covers both rotated orientations.
        image.draft("RGB", (THUMBNAIL_EDGE, THUMBNAIL_EDGE))
        image = ImageOps.exif_transpose(image)
        image = _flatten(image)
        image.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
        image.save(target, "JPEG", quality=85, optimize=True)


def _video_thumbnail(source: Path, target: Path) -> bool:
    edge = THUMBNAIL_EDGE
    scale = f"scale='if(gt(iw,ih),{edge},-2)':'if(gt(iw,ih),-2,{edge})'"
    # Prefer a frame one second in (skips black fade-ins); clips shorter than that
    # produce nothing, so fall back to the very first frame.
    for seek in (["-ss", "1"], []):
        try:
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", *seek, "-i", str(source),
                "-frames:v", "1", "-vf", scale, "-q:v", "3", str(target),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if target.exists() and target.stat().st_size > 0:
            return True
    return False


def get_cached_thumbnail(path: Path, media_type: str, file_size: int, modified_ns: int) -> Path | None:
    """Return the cached thumbnail, generating it if needed. Slow; call from a worker thread."""
    ensure_app_dirs()
    target = thumbnail_cache_path(path, file_size, modified_ns)
    if target.exists():
        return target
    failed = _failure_marker(target)
    if failed.exists():
        return None
    if media_type == "video" and not FFMPEG_ENABLED:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if media_type == "video":
            if not _video_thumbnail(path, target):
                raise OSError("ffmpeg produced no frame")
        else:
            # Write beside the target and rename, so a reader never sees a half-written JPEG.
            partial = target.with_suffix(".part")
            _image_thumbnail(path, partial)
            partial.replace(target)
        return target
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        try:
            target.unlink(missing_ok=True)
            target.with_suffix(".part").unlink(missing_ok=True)
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
