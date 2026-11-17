import json
import os
import unittest
import unittest.mock
from pathlib import Path

from photolibrary.core import faces, settings as settings_module
from photolibrary.core.settings import SettingsStore

from .base import TempAppTestCase

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


@unittest.skipUnless(faces.FACES_ENABLED, "OpenCV is not installed")
class YuNetDecodingTests(unittest.TestCase):
    def outputs(self, width, height, hits):
        """Raw YuNet outputs that are empty except for `hits`: (stride, col, row, values)."""
        outputs = {}
        for stride in (8, 16, 32):
            cells = (width // stride) * (height // stride)
            outputs[f"cls_{stride}"] = np.zeros((1, cells, 1), np.float32)
            outputs[f"obj_{stride}"] = np.zeros((1, cells, 1), np.float32)
            outputs[f"bbox_{stride}"] = np.zeros((1, cells, 4), np.float32)
            outputs[f"kps_{stride}"] = np.zeros((1, cells, 10), np.float32)
        for stride, col, row, (cls, obj, bbox, kps) in hits:
            index = row * (width // stride) + col
            outputs[f"cls_{stride}"][0, index, 0] = cls
            outputs[f"obj_{stride}"][0, index, 0] = obj
            outputs[f"bbox_{stride}"][0, index] = bbox
            outputs[f"kps_{stride}"][0, index] = kps
        return outputs

    def test_a_cell_becomes_a_box_with_landmarks_and_a_score(self):
        kps = [0.5, 0.25] * 5
        outputs = self.outputs(64, 96, [(16, 2, 3, (0.81, 1.0, (0.5, 0.5, np.log(2), np.log(3)), kps))])
        rows = faces.decode_yunet(outputs, 64, 96, 0.5)
        self.assertEqual(rows.shape, (1, 15))
        x, y, w, h = rows[0, :4]
        # Centre (2.5, 3.5) cells of 16 px; 2 x 3 cells big.
        self.assertAlmostEqual(float(x), 40 - 16, places=4)
        self.assertAlmostEqual(float(y), 56 - 24, places=4)
        self.assertAlmostEqual(float(w), 32, places=4)
        self.assertAlmostEqual(float(h), 48, places=4)
        self.assertAlmostEqual(float(rows[0, 4]), 40, places=4)
        self.assertAlmostEqual(float(rows[0, 5]), 52, places=4)
        self.assertAlmostEqual(float(rows[0, 14]), 0.9, places=4)  # sqrt(cls * obj)

    def test_low_scores_are_dropped_and_values_are_clipped(self):
        outputs = self.outputs(32, 32, [(8, 0, 0, (0.3, 0.3, (0, 0, 0, 0), [0] * 10)),
                                        (32, 0, 0, (1.7, 1.0, (0, 0, 0, 0), [0] * 10))])
        rows = faces.decode_yunet(outputs, 32, 32, 0.75)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0, 14]), 1.0)

    def test_nothing_found_gives_an_empty_table(self):
        self.assertEqual(faces.decode_yunet(self.outputs(32, 32, []), 32, 32, 0.5).shape, (0, 15))


@unittest.skipUnless(faces.FACES_ENABLED, "OpenCV is not installed")
class SimilarityTransformTests(unittest.TestCase):
    def test_recovers_a_rotation_scale_and_shift(self):
        angle, scale, shift = 0.4, 1.7, np.array([12.0, -5.0])
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        source = np.array(faces._SFACE_LANDMARKS) * 3 + 20
        target = (scale * rotation @ source.T).T + shift
        matrix = faces.similarity_transform(source, target)
        np.testing.assert_allclose(matrix[:, :2], scale * rotation, atol=1e-9)
        np.testing.assert_allclose(matrix[:, 2], shift, atol=1e-9)


class EngineChoiceTests(unittest.TestCase):
    def setUp(self):
        self.cpu = unittest.mock.patch.object(faces, "OpenCVFaceEngine", side_effect=lambda models: "cpu").start()
        self.gpu = unittest.mock.patch.object(faces, "CudaFaceEngine", side_effect=lambda models: "gpu").start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_the_card_is_used_only_when_asked_for_and_installed(self):
        with unittest.mock.patch.object(faces, "gpu_runtime_installed", return_value=True):
            self.assertEqual(faces.make_face_engine(Path("m"), use_gpu=True), "gpu")
            self.assertEqual(faces.make_face_engine(Path("m"), use_gpu=False), "cpu")
        with unittest.mock.patch.object(faces, "gpu_runtime_installed", return_value=False):
            self.assertEqual(faces.make_face_engine(Path("m"), use_gpu=True), "cpu")

    def test_falls_back_to_the_processor_when_the_card_fails(self):
        for error in (faces.ModelError("no card"), RuntimeError("driver crashed")):
            self.gpu.side_effect = error
            with unittest.mock.patch.object(faces, "gpu_runtime_installed", return_value=True), \
                    self.assertLogs(faces.LOGGER, "WARNING"):
                self.assertEqual(faces.make_face_engine(Path("m"), use_gpu=True), "cpu")

    def test_never_offered_off_windows(self):
        with unittest.mock.patch.object(faces, "GPU_SUPPORTED", False):
            self.assertFalse(faces.gpu_runtime_installed())

    def test_the_card_engine_refuses_to_start_off_windows(self):
        unittest.mock.patch.stopall()
        with unittest.mock.patch.object(faces, "GPU_SUPPORTED", False), self.assertRaises(faces.ModelError):
            faces.CudaFaceEngine(Path("m"))


@unittest.skipUnless(faces.FACES_ENABLED, "OpenCV is not installed")
class GpuFailureTests(unittest.TestCase):
    def test_a_photo_the_card_fails_on_is_done_on_the_processor(self):
        engine = faces.CudaFaceEngine.__new__(faces.CudaFaceEngine)  # no card needed
        engine._models_dir, engine._fallback, engine._warned = Path("m"), None, False
        engine._fallback_lock = __import__("threading").Lock()
        engine._detect = unittest.mock.Mock(side_effect=RuntimeError("CUDA out of memory"))
        cpu = unittest.mock.Mock()
        cpu.detect.return_value = ["face"]
        with unittest.mock.patch.object(faces, "OpenCVFaceEngine", return_value=cpu) as make, \
                self.assertLogs(faces.LOGGER, "WARNING"):
            self.assertEqual(engine.detect("image"), ["face"])
            self.assertEqual(engine.detect("image"), ["face"])
        make.assert_called_once()  # made once, then reused

    def test_an_engine_error_is_not_saved_as_no_faces(self):
        import threading
        from PIL import Image
        from photolibrary.core.models import MediaItem

        class Broken:
            version = 1

            def detect(self, image):
                raise RuntimeError("driver reset")

        item = unittest.mock.Mock(spec=MediaItem)
        item.path = Path(__file__)
        saved = []
        with self.assertLogs(faces.LOGGER, "ERROR"):
            result = faces.analyze_photos([item], Broken(), threading.Event(), lambda *args: saved.append(args),
                                          load_image=lambda path, edge: Image.new("RGB", (8, 8)))
        self.assertEqual(saved, [])  # tried again next time instead
        self.assertEqual((result.errors, result.analyzed, result.failed), (1, 0, 0))


class GpuSettingTests(TempAppTestCase):
    def test_on_by_default_and_saved(self):
        store = SettingsStore()
        self.assertTrue(store.load().use_gpu)
        loaded = store.load()
        loaded.use_gpu = False
        store.save(loaded)
        self.assertFalse(store.load().use_gpu)
        self.assertIs(json.loads(settings_module.CONFIG_FILE.read_text(encoding="utf-8"))["use_gpu"], False)


def _cuda_engine_or_none():
    models = os.environ.get("PHOTO_LIBRARY_FACE_MODELS")
    if not (models and faces.gpu_runtime_installed()):
        return None
    try:
        return faces.CudaFaceEngine(Path(models))
    except faces.ModelError:
        return None


@unittest.skipUnless(faces.FACES_ENABLED and os.environ.get("PHOTO_LIBRARY_FACE_MODELS"),
                     "set PHOTO_LIBRARY_FACE_MODELS to a folder holding the downloaded face models")
class RealCudaModelTests(unittest.TestCase):
    def test_the_card_finds_no_face_in_a_landscape(self):
        from PIL import Image, ImageDraw
        engine = _cuda_engine_or_none()
        if engine is None:
            self.skipTest("no usable NVIDIA graphics card")
        image = Image.new("RGB", (1280, 853), (90, 140, 200))
        ImageDraw.Draw(image).polygon([(0, 853), (400, 300), (800, 853)], fill=(40, 90, 50))
        self.assertEqual(engine.detect(image), [])
