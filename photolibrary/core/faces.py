"""Find the faces in photos and describe each one with a fingerprint, entirely on this computer.

Two small open models from the OpenCV model zoo do the work: YuNet finds faces (MIT licence)
and SFace turns each face into 128 numbers (Apache 2.0). Faces of the same person get similar
numbers, which is what `core/people.py` groups on. Photos, faces and fingerprints never leave
the machine; the only network access is the one-time model download, checked by SHA-256.

OpenCV is optional: without it `FACES_ENABLED` is False and the People feature stays off.

On Windows the same two models can instead run on an NVIDIA graphics card (CUDA) through ONNX
Runtime (`CudaFaceEngine`, `pip install -r requirements-cuda.txt`). It is chosen by the "Use the
NVIDIA graphics card" setting and falls back to OpenCV on the processor when the card or its
libraries can't be used. The Mac build never offers or loads it (`GPU_SUPPORTED`).
"""

import hashlib
import importlib.metadata
import importlib.util
import logging
import os
import sys
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
# NVIDIA CUDA is offered on Windows only; macOS has no CUDA, and the Mac build stays as it was.
GPU_SUPPORTED = sys.platform == "win32"

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


def _gpu_runtime_installed() -> bool:
    if not (GPU_SUPPORTED and FACES_ENABLED and importlib.util.find_spec("onnxruntime") is not None):
        return False
    # The CPU-only "onnxruntime" package installs the same module but can never use CUDA.
    try:
        importlib.metadata.distribution("onnxruntime-gpu")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


# Checked once, at start-up like FACES_ENABLED (never on the UI thread later), without importing it.
_GPU_RUNTIME = _gpu_runtime_installed()


def gpu_runtime_installed() -> bool:
    """True when ONNX Runtime for CUDA (onnxruntime-gpu) is installed, on Windows."""
    return GPU_SUPPORTED and _GPU_RUNTIME


class OpenCVFaceEngine:
    """YuNet + SFace. Safe to call from several threads: each thread gets its own networks."""

    version = ENGINE_VERSION
    device = "processor"

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


# Where SFace expects the eyes, nose tip and mouth corners in its 112 x 112 input (OpenCV's
# FaceRecognizerSF.alignCrop uses the same points).
_SFACE_LANDMARKS = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655),
                    (70.7299, 92.2041))
_YUNET_STRIDES = (8, 16, 32)
_NMS_THRESHOLD = 0.3
_NMS_TOP_K = 5000


def similarity_transform(source, target):
    """The 2 x 3 rotation + uniform scale + shift that best maps `source` points onto `target` (Umeyama)."""
    import numpy as np
    src, dst = np.asarray(source, dtype=np.float64), np.asarray(target, dtype=np.float64)
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_centered, dst_centered = src - src_mean, dst - dst_mean
    covariance = dst_centered.T @ src_centered / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    sign = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[1, 1] = -1
    rotation = u @ sign @ vt
    variance = (src_centered ** 2).sum() / len(src)
    scale = float(np.trace(np.diag(singular) @ sign)) / variance if variance else 1.0
    matrix = np.zeros((2, 3))
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = dst_mean - scale * rotation @ src_mean
    return matrix


def decode_yunet(outputs: dict, padded_width: int, padded_height: int, score_threshold: float):
    """Turn YuNet's raw outputs into rows like OpenCV's FaceDetectorYN.detect gives (before NMS).

    Each row: x, y, w, h, five (x, y) landmarks, score, in input pixels.
    """
    import numpy as np
    rows = []
    for stride in _YUNET_STRIDES:
        cols = padded_width // stride
        cls = np.clip(outputs[f"cls_{stride}"].reshape(-1), 0, 1)
        obj = np.clip(outputs[f"obj_{stride}"].reshape(-1), 0, 1)
        score = np.sqrt(cls * obj)
        keep = np.nonzero(score >= score_threshold)[0]
        if not len(keep):
            continue
        bbox = outputs[f"bbox_{stride}"].reshape(-1, 4)[keep]
        kps = outputs[f"kps_{stride}"].reshape(-1, 10)[keep]
        col = (keep % cols).astype(np.float32)
        line = (keep // cols).astype(np.float32)
        cx, cy = (col + bbox[:, 0]) * stride, (line + bbox[:, 1]) * stride
        w, h = np.exp(bbox[:, 2]) * stride, np.exp(bbox[:, 3]) * stride
        points = np.empty_like(kps)
        points[:, 0::2] = (kps[:, 0::2] + col[:, None]) * stride
        points[:, 1::2] = (kps[:, 1::2] + line[:, None]) * stride
        rows.append(np.column_stack([cx - w / 2, cy - h / 2, w, h, points, score[keep]]))
    return np.concatenate(rows).astype(np.float32) if rows else np.zeros((0, 15), np.float32)


def any_size_detector(path: Path) -> bytes:
    """The YuNet model with its declared 640 x 640 input relaxed to any size.

    OpenCV resizes the network for each photo by itself; ONNX Runtime holds models to their
    declared shapes. Every layer in YuNet works at any size (its reshapes use -1 and its upsampling
    uses scales), so only the declared input and output shapes change. Done in memory, never saved.
    """
    try:
        import onnx
    except ImportError as exc:
        raise ModelError("The onnx package is not installed (pip install -r requirements-cuda.txt).") from exc
    model = onnx.load(str(path))
    dims = model.graph.input[0].type.tensor_type.shape.dim
    dims[2].dim_param, dims[3].dim_param = "height", "width"
    for output in model.graph.output:
        output.type.tensor_type.shape.dim[1].dim_param = "cells"
    del model.graph.value_info[:]  # shapes inside the graph were worked out for 640 x 640
    return model.SerializeToString()


class CudaFaceEngine:
    """The same YuNet + SFace models as OpenCVFaceEngine, run on an NVIDIA graphics card (Windows).

    Uses ONNX Runtime's CUDA provider; OpenCV still does the cheap steps (overlap removal and
    the face crop). One session is shared by every worker thread (ONNX Runtime allows that).
    Raises ModelError when the card or the CUDA libraries can't be used.
    """

    version = ENGINE_VERSION  # same models and results, so switching doesn't re-analyse photos
    device = "NVIDIA graphics card"

    def __init__(self, models_dir: Path):
        if not GPU_SUPPORTED:
            raise ModelError("The NVIDIA graphics card is only used on Windows.")
        if not FACES_ENABLED:
            raise ModelError("OpenCV is not installed.")
        if not models_present(models_dir):
            raise ModelError("The face models have not been downloaded.")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ModelError("ONNX Runtime for CUDA is not installed.") from exc
        import cv2
        import numpy
        self._cv2, self._np = cv2, numpy
        self._models_dir = models_dir
        self._fallback: OpenCVFaceEngine | None = None  # made the first time the card fails
        self._fallback_lock = threading.Lock()
        self._warned = False
        if hasattr(ort, "preload_dlls"):
            # Loads the CUDA and cuDNN libraries installed by pip (nvidia-* packages), if present.
            try:
                ort.preload_dlls()
            except Exception:
                LOGGER.debug("Could not preload the CUDA libraries", exc_info=True)
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise ModelError("This ONNX Runtime can't use CUDA (install onnxruntime-gpu).")
        options = ort.SessionOptions()
        options.log_severity_level = 3  # errors only
        # Photos come in many sizes; an exhaustive cuDNN search for each new size would stall.
        providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})]
        try:
            detector_model = any_size_detector(models_dir / MODELS[0].name)
            self._detector = ort.InferenceSession(detector_model, options, providers=providers)
            self._recognizer = ort.InferenceSession(str(models_dir / MODELS[1].name), options, providers=providers)
        except Exception as exc:
            raise ModelError(f"Could not start the face models on the graphics card: {exc}") from exc
        if "CUDAExecutionProvider" not in self._detector.get_providers():
            raise ModelError("No NVIDIA graphics card could be used.")
        self._detector_input = self._detector.get_inputs()[0].name
        self._detector_outputs = [output.name for output in self._detector.get_outputs()]
        self._recognizer_input = self._recognizer.get_inputs()[0].name
        self._landmarks = numpy.array(_SFACE_LANDMARKS, dtype=numpy.float32)

    def _find(self, pixels):
        np, cv2 = self._np, self._cv2
        height, width = pixels.shape[:2]
        padded_width, padded_height = -(-width // 32) * 32, -(-height // 32) * 32
        padded = np.zeros((padded_height, padded_width, 3), dtype=np.float32)
        padded[:height, :width] = pixels
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None])
        outputs = dict(zip(self._detector_outputs, self._detector.run(self._detector_outputs,
                                                                       {self._detector_input: blob})))
        rows = decode_yunet(outputs, padded_width, padded_height, MIN_FACE_SCORE)
        if not len(rows):
            return rows
        keep = cv2.dnn.NMSBoxes(rows[:, :4].tolist(), rows[:, 14].tolist(), MIN_FACE_SCORE, _NMS_THRESHOLD,
                                top_k=_NMS_TOP_K)
        return rows[np.asarray(keep, dtype=np.int64).reshape(-1)]

    def _fingerprint(self, pixels, row):
        np, cv2 = self._np, self._cv2
        matrix = similarity_transform(row[4:14].reshape(5, 2), self._landmarks)
        crop = cv2.warpAffine(pixels, matrix, (112, 112))
        blob = np.ascontiguousarray(crop[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)  # SFace wants RGB
        return self._recognizer.run(None, {self._recognizer_input: blob})[0].astype(np.float32).ravel()

    def detect(self, image: Image.Image) -> list[Face]:
        try:
            return self._detect(image)
        except Exception:
            # The card can run out of memory (a game is using it) or its driver can reset: find this
            # photo's faces on the processor instead, with the same models and results.
            if not self._warned:
                self._warned = True
                LOGGER.warning("The graphics card failed on a photo; using the processor for it", exc_info=True)
            with self._fallback_lock:
                if self._fallback is None:
                    self._fallback = OpenCVFaceEngine(self._models_dir)
            return self._fallback.detect(image)

    def _detect(self, image: Image.Image) -> list[Face]:
        np = self._np
        pixels = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])  # BGR, as OpenCV uses
        height, width = pixels.shape[:2]
        faces = []
        for row in self._find(pixels):
            x, y, w, h, score = (float(value) for value in (row[0], row[1], row[2], row[3], row[14]))
            if min(w, h) < MIN_FACE_PIXELS or score < MIN_FACE_SCORE:
                continue
            vector = self._fingerprint(pixels, row)
            norm = float(np.linalg.norm(vector))
            if not norm:
                continue
            left, top = max(0.0, x), max(0.0, y)
            right, bottom = min(float(width), x + w), min(float(height), y + h)
            faces.append(Face(left / width, top / height, (right - left) / width, (bottom - top) / height,
                              score, (vector / norm).tobytes()))
        return faces


def make_face_engine(models_dir: Path, use_gpu: bool = False) -> FaceEngine:
    """The NVIDIA card engine when asked for and usable (Windows), otherwise OpenCV on the processor."""
    if use_gpu and gpu_runtime_installed():
        try:
            return CudaFaceEngine(models_dir)
        except ModelError as exc:
            LOGGER.warning("Not using the graphics card for faces: %s", exc)
        except Exception:
            LOGGER.exception("Not using the graphics card for faces")
    return OpenCVFaceEngine(models_dir)


@dataclass(frozen=True)
class AnalyzeResult:
    total: int
    analyzed: int
    faces: int
    failed: int
    cancelled: bool
    unreachable: int = 0  # couldn't be opened (an unplugged drive): not recorded, tried again next time
    errors: int = 0  # the face engine failed (say, the graphics card ran out of memory): not recorded either


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
    analyzed = found = failed = done = unreachable = errors = 0
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
            nonlocal analyzed, found, failed, done, unreachable, errors, last_report
            finished, _ = wait(list(in_flight), return_when=return_when)
            for future in finished:
                item = in_flight.pop(future)
                done += 1
                try:
                    faces = future.result()
                except Exception:
                    # Not "a photo without faces": recording that would never look at it again.
                    LOGGER.exception("Face analysis failed for one photo; it is tried again next time")
                    errors += 1
                    continue
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
    return AnalyzeResult(total, analyzed, found, failed, cancelled, unreachable, errors)
