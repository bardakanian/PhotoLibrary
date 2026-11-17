import os
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from photolibrary.core.export import export_conflict, export_files, free_name


class ExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.library = self.base / "Library"
        (self.library / "Trip").mkdir(parents=True)
        (self.library / "Home").mkdir()
        self.a = self.library / "Trip" / "IMG_0001.HEIC"
        self.b = self.library / "Home" / "IMG_0001.heic"  # same name in another album
        self.c = self.library / "Home" / "clip.mov"
        for index, path in enumerate((self.a, self.b, self.c)):
            path.write_bytes(bytes([index]) * (100 + index))
            os.utime(path, (1_600_000_000 + index, 1_600_000_000 + index))
        self.out = self.base / "Export"

    def test_copies_originals_into_one_folder_keeping_both_of_a_clashing_name(self):
        result = export_files([self.a, self.b, self.c], self.out, threading.Event())
        self.assertEqual((result.total, result.copied, result.renamed, result.failed), (3, 3, 1, []))
        self.assertEqual(sorted(path.name for path in self.out.iterdir()),
                         ["IMG_0001 (2).heic", "IMG_0001.HEIC", "clip.mov"])
        self.assertEqual((self.out / "IMG_0001.HEIC").read_bytes(), self.a.read_bytes())  # untouched
        self.assertEqual((self.out / "IMG_0001 (2).heic").read_bytes(), self.b.read_bytes())
        self.assertEqual(int((self.out / "clip.mov").stat().st_mtime), 1_600_000_002)  # dates kept

    def test_a_second_export_never_overwrites(self):
        export_files([self.a], self.out, threading.Event())
        (self.out / "IMG_0001 (2).HEIC").write_bytes(b"mine")
        result = export_files([self.a], self.out, threading.Event())
        self.assertEqual(result.renamed, 1)
        self.assertEqual((self.out / "IMG_0001 (2).HEIC").read_bytes(), b"mine")
        self.assertEqual((self.out / "IMG_0001 (3).HEIC").read_bytes(), self.a.read_bytes())

    def test_a_failed_copy_is_reported_and_leaves_nothing_behind(self):
        def copy(source, target, stop):
            Path(target).write_bytes(b"half")
            raise OSError(28, "No space left on device")

        result = export_files([self.a, self.base / "gone.jpg"], self.out, threading.Event(), copy=copy)
        self.assertEqual(result.copied, 0)
        self.assertEqual([reason for _path, reason in result.failed], ["No space left on device"] * 2)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_cancelling_stops_before_the_next_file(self):
        stop = threading.Event()
        progress = []

        def on_progress(done, total):
            progress.append(done)
            if done == 1:
                stop.set()

        with unittest.mock.patch("photolibrary.core.export.PROGRESS_INTERVAL", 0):
            result = export_files([self.a, self.b, self.c], self.out, stop, on_progress)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.copied, 1)
        self.assertEqual(len(list(self.out.iterdir())), 1)

    def test_stopping_mid_file_removes_the_partial_copy(self):
        big = self.library / "Home" / "big.mov"
        big.write_bytes(b"x" * (3 * 1024))

        class StopAfter:
            """Answers "stop" from the n+1th check on: here, in the middle of the big file."""

            def __init__(self, n):
                self.calls, self.n = 0, n

            def is_set(self):
                self.calls += 1
                return self.calls > self.n

        # Checks: before a (1), a's one chunk (2), before big (3), big's first chunk (4), its second (5).
        with unittest.mock.patch("photolibrary.core.export.CHUNK", 1024):
            result = export_files([self.a, big, self.c], self.out, StopAfter(4))
        self.assertTrue(result.cancelled)
        self.assertEqual(result.failed, [])
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["IMG_0001.HEIC"])  # no partial left

    def test_a_name_taken_meanwhile_gets_the_next_number(self):
        def copy(source, target, stop):
            Path(target).write_bytes(Path(source).read_bytes())
            (self.out / source.name).write_bytes(b"theirs")  # another program, just before the rename

        result = export_files([self.a], self.out, threading.Event(), copy=copy)
        self.assertEqual((result.copied, result.renamed, result.failed), (1, 1, []))
        self.assertEqual((self.out / "IMG_0001.HEIC").read_bytes(), b"theirs")
        self.assertEqual((self.out / "IMG_0001 (2).HEIC").read_bytes(), self.a.read_bytes())

    def test_a_partial_copy_left_by_a_crash_is_replaced(self):
        self.out.mkdir()
        (self.out / ".IMG_0001.HEIC.export-part").write_bytes(b"stale")
        result = export_files([self.a], self.out, threading.Event())
        self.assertEqual(result.copied, 1)
        self.assertEqual([path.name for path in self.out.iterdir()], ["IMG_0001.HEIC"])

    def test_a_folder_inside_the_library_is_refused_before_copying(self):
        before = sorted(path.name for path in (self.library / "Home").iterdir())
        result = export_files([self.a], self.library / "Home", threading.Event(), roots=[self.library])
        self.assertIsNotNone(result.refused)
        self.assertEqual(result.copied, 0)
        self.assertEqual(sorted(path.name for path in (self.library / "Home").iterdir()), before)

    def test_exporting_into_the_library_is_refused(self):
        self.assertIsNotNone(export_conflict(self.library / "Trip", [self.library]))
        self.assertIsNotNone(export_conflict(self.library, [self.library]))
        self.assertIsNone(export_conflict(self.out, [self.library]))

    def test_free_name_ignores_case_like_windows_and_macos(self):
        self.assertEqual(free_name(self.out, "a.jpg", {"a.jpg"}), "a (2).jpg")
        self.assertEqual(free_name(self.out, "A.JPG", {"a.jpg", "a (2).jpg"}), "A (3).JPG")
