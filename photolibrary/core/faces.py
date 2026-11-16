"""Find the faces in photos and describe each one with a fingerprint, entirely on this computer.

Two small open models from the OpenCV model zoo do the work: YuNet finds faces (MIT licence)
and SFace turns each face into 128 numbers (Apache 2.0). Faces of the same person get similar
numbers, which is what `core/people.py` groups on. Photos, faces and fingerprints never leave
the machine; the only network access is the one-time model download, checked by SHA-256.

OpenCV is optional: without it `FACES_ENABLED` is False and the People feature stays off.
"""

import hashlib
import importlib.util
import logging
import os
import threading
import time
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from PIL import Image

from .media import load_display_image
from .models import MediaItem

# Checked without importing: OpenCV adds about 60 ms to start-up, paid only once People runs.
# Without it the app works; People just isn't offered.
FACES_ENABLED = all(importlib.util.find_spec(name) is not None for name in ("cv2", "numpy"))

LOGGER = logging.getLogger(__name__)

# Bump when the models or the way faces are found change, so every photo is analysed again.
# 2: larger images and a more sensitive detector (below).
ENGINE_VERSION = 2
# Photos are decoded at most this large, and faces must be at least MIN_FACE_PIXELS there and
# found with at least MIN_FACE_SCORE confidence. Measured on 400 photos of a real library:
# 1,280 px / 0.85 / 40 px found 272 faces in 35% of photos; 1,920 px / 0.75 / 32 px found 366
# (+35%) in 40%, at about 20% more time per photo. 2,560 px added only 4% more faces.
ANALYZE_EDGE = 1920
MIN_FACE_PIXELS = 32
MIN_FACE_SCORE = 0.75
EMBEDDING_SIZE = 128
# Photos analysed at once. Measured on a 10-core MacBook Air, 2½ minutes per setting on 400 real
# photos: 3 workers 1,351 photos a minute and the Mac stayed cool; 8 workers 1,748 a minute at first
# but 1,540 once warm, and the Mac warmed up. So three: about 14% slower, cool and responsive, and fewer
# still when the Mac says it is warm (core/thermal.py). OpenCV's own threads help: limiting them to one
# per worker was slower with both 3 and 8 workers (1,098 and 1,238 a minute).
DEFAULT_WORKERS = min(3, max(1, (os.cpu_count() or 4) - 2))
# How often a pause for heat checks whether the Mac has cooled down.
COOL_DOWN_CHECK_SECONDS = 5.0
PROGRESS_INTERVAL = 0.5


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = (
    ModelFile("face_detection_yunet_2023mar.onnx", f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232_589),
    ModelFile("face_recognition_sface_2021dec.onnx",
              f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", 38_696_353),
)


class ModelError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def models_present(models_dir: Path) -> bool:
    return all((models_dir / model.name).is_file() and (models_dir / model.name).stat().st_size == model.size
               for model in MODELS)


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as handle:
        while block := response.read(1 << 16):
            handle.write(block)


def ensure_models(models_dir: Path, download: Callable[[str, Path], None] = _download) -> None:
    """Download any missing model file and check it. Raises ModelError if a file doesn't match."""
    models_dir.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        target = models_dir / model.name
        if target.is_file() and target.stat().st_size == model.size:
            continue
        partial = target.with_suffix(".part")
        try:
            download(model.url, partial)
            if _sha256(partial) != model.sha256:
                raise ModelError(f"{model.name} did not match its expected checksum; it was not used.")
            partial.replace(target)
        except OSError as exc:
            raise ModelError(f"Could not download {model.name}: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class Face:
    """One face: its box as fractions of the photo's size, the detector's confidence and its fingerprint."""

    x: float
    y: float
    w: float
    h: float
    score: float
    embedding: bytes  # EMBEDDING_SIZE float32 values, length 1


class FaceEngine(Protocol):
    version: int

    def detect(self, image: Image.Image) -> list[Face]: ...


class OpenCVFaceEngine:
    """YuNet + SFace. Safe to call from several threads: each thread gets its own networks."""

    version = ENGINE_VERSION

    def __init__(self, models_dir: Path):
        if not FACES_ENABLED:
            raise ModelError("OpenCV is not installed.")
        if not models_present(models_dir):
            raise ModelError("The face models have not been downloaded.")
        import cv2
        import numpy
        self._cv2, self._np = cv2, numpy
        self.detector_path = str(models_dir / MODELS[0].name)
        self.recognizer_path = str(models_dir / MODELS[1].name)
        self._local = threading.local()
        # Errors only: OpenCV 5 warns about unsupported targets every time a model loads.
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)

    def _networks(self):
        local, cv2 = self._local, self._cv2
        if not hasattr(local, "detector"):
            local.detector = cv2.FaceDetectorYN.create(self.detector_path, "", (320, 320), MIN_FACE_SCORE, 0.3, 5000)
            local.recognizer = cv2.FaceRecognizerSF.create(self.recognizer_path, "")
        return local.detector, local.recognizer

    def detect(self, image: Image.Image) -> list[Face]:
        detector, recognizer = self._networks()
        np = self._np
        pixels = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])  # OpenCV wants BGR
        height, width = pixels.shape[:2]
        detector.setInputSize((width, height))
        _count, rows = detector.detect(pixels)
        faces = []
        for row in rows if rows is not None else ():
            x, y, w, h, score = float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[14])
            if min(w, h) < MIN_FACE_PIXELS or score < MIN_FACE_SCORE:
                continue
            vector = recognizer.feature(recognizer.alignCrop(pixels, row)).astype(np.float32).ravel()
            norm = float(np.linalg.norm(vector))
            if not norm:
                continue
            left, top = max(0.0, x), max(0.0, y)
            right, bottom = min(float(width), x + w), min(float(height), y + h)
            faces.append(Face(left / width, top / height, (right - left) / width, (bottom - top) / height,
                              score, (vector / norm).tobytes()))
        return faces


@dataclass(frozen=True)
class AnalyzeResult:
    total: int
    analyzed: int
    faces: int
    failed: int
    cancelled: bool
    unreachable: int = 0  # couldn't be opened (an unplugged drive): not recorded, tried again next time


_UNREACHABLE = object()


def analyze_photos(items: Sequence[MediaItem], engine: FaceEngine, stop_event: threading.Event,
                   on_result: Callable[[MediaItem, list[Face]], None],
                   on_progress: Callable[[int, int], None] | None = None,
                   workers: int = DEFAULT_WORKERS,
                   load_image: Callable[[Path, int], Image.Image | None] = load_display_image,
                   heat: Callable[[], int | None] | None = None,
                   on_pause: Callable[[bool], None] | None = None) -> AnalyzeResult:
    """Find the faces in each photo (in order) on worker threads.

    `on_result(item, faces)` runs on the calling thread, so the caller can write to the database
    there. A photo that can't be decoded gets an empty result, so it isn't retried until it
    changes. A photo that can't be reached at all (an unplugged drive) gets no result and is
    tried again next time. `load_image(path, max_edge)` decodes an EXIF-rotated photo; the UI
    passes one that uses the system decoder, which reads HEIC about 9 times faster than Pillow.

    `heat()` returns the Mac's thermal state (core/thermal.py): when warm, one photo at a time;
    when hot, it pauses (telling `on_pause(True)`, then `on_pause(False)`) until it cools down.
    """
    from .thermal import allowed_workers
    analyzed = found = failed = done = unreachable = 0
    total = len(items)
    if not total:
        return AnalyzeResult(0, 0, 0, 0, stop_event.is_set())

    def analyze(item: MediaItem):
        if stop_event.is_set():
            return None
        try:
            image = load_image(item.path, ANALYZE_EDGE)
        except Exception:
            image = None
        if image is None:
            # Gone or not readable right now is not "a photo without faces".
            return _UNREACHABLE if not os.access(item.path, os.R_OK) else None
        return engine.detect(image)

    if on_progress:
        on_progress(0, total)
    last_report = time.monotonic()
    workers = max(1, min(workers, total))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="face-analyze")
    in_flight: dict[Future, MediaItem] = {}
    try:
        def collect(return_when: str) -> None:
            nonlocal analyzed, found, failed, done, unreachable, last_report
            finished, _ = wait(list(in_flight), return_when=return_when)
            for future in finished:
                item = in_flight.pop(future)
                done += 1
                try:
                    faces = future.result()
                except Exception:
                    LOGGER.exception("Face analysis failed for one photo")
                    faces = None
                if stop_event.is_set() and faces is None:
                    continue  # skipped because of the cancel: not a real result
                if faces is _UNREACHABLE:
                    unreachable += 1
                    continue
                if faces is None:
                    failed += 1
                    faces = []
                analyzed += 1
                found += len(faces)
                on_result(item, faces)
            now = time.monotonic()
            if on_progress and now - last_report >= PROGRESS_INTERVAL:
                last_report = now
                on_progress(done, total)

        paused = False
        for item in items:
            if stop_event.is_set():
                break
            limit = allowed_workers(workers, heat()) if heat else workers
            while limit == 0 and not stop_event.is_set():
                if in_flight:
                    collect(FIRST_COMPLETED)  # finish what's running, start nothing new
                    limit = allowed_workers(workers, heat())
                    continue
                if not paused and on_pause:
                    on_pause(True)
                paused = True
                stop_event.wait(COOL_DOWN_CHECK_SECONDS)
                limit = allowed_workers(workers, heat())
            if paused:
                paused = False
                if on_pause:
                    on_pause(False)
            if stop_event.is_set():
                break
            # A small backlog keeps the threads busy; while the Mac is warm, one photo at a time.
            while len(in_flight) >= (workers * 2 if limit >= workers else limit):
                collect(FIRST_COMPLETED)
            in_flight[executor.submit(analyze, item)] = item
        while in_flight and not stop_event.is_set():
            collect(FIRST_COMPLETED)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    cancelled = stop_event.is_set() and done < total
    if on_progress and not cancelled:
        on_progress(done, total)
    return AnalyzeResult(total, analyzed, found, failed, cancelled, unreachable)
