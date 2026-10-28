import os
import threading
from datetime import datetime
from unittest.mock import patch

from photolibrary.core import database, indexer
from photolibrary.core.indexer import MediaIndexer, album_for

from .base import TempAppTestCase


def run_scan(folder, scan_id=1, stop=None):
    events = []
    MediaIndexer(folder, stop or threading.Event(), scan_id, events.append).run()
    return events


def rows(columns="file_name, capture_ts"):
    connection = database.open_database()
    try:
        return connection.execute(f"SELECT {columns} FROM media ORDER BY file_name").fetchall()
    finally:
        connection.close()


class IndexerTests(TempAppTestCase):
    def test_scan_emits_progress_and_completion(self):
        library = self.make_library()
        self.write_photo(library / "sample.jpg", datetime(2020, 1, 1))
        events = run_scan(library, scan_id=7)
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "scan_started")
        self.assertIn("scan_total", types)
        self.assertEqual(events[-1]["type"], "scan_complete")
        self.assertEqual(events[-1]["total_indexed"], 1)
        self.assertTrue(all(event["scan_id"] == 7 for event in events))

    def test_stores_dates_albums_and_metadata(self):
        library = self.make_library()
        self.write_photo(library / "root.jpg", datetime(2020, 1, 1, 9, 0, 0), size=(30, 20), model="Pixel 8")
        self.write_photo(library / "Trips" / "Rome" / "nested.jpg", datetime(2021, 2, 2))
        (library / "notes.txt").write_text("ignored")
        self.write_photo(library / ".hidden" / "secret.jpg")
        self.write_photo(library / "._appledouble.jpg")
        run_scan(library)
        stored = rows("file_name, album, capture_ts, width, height, camera, capture_year")
        self.assertEqual(stored, [
            ("nested.jpg", "Trips", "2021-02-02T00:00:00", 16, 12, None, 2021),
            ("root.jpg", "", "2020-01-01T09:00:00", 30, 20, "Pixel 8", 2020),
        ])

    def test_album_for(self):
        root = os.path.join(os.sep, "lib")
        self.assertEqual(album_for(root, root), "")
        self.assertEqual(album_for(root, os.path.join(root, "A", "B")), "A")

    def test_picks_up_added_changed_and_deleted_files(self):
        library = self.make_library()
        keep = self.write_photo(library / "keep.jpg", datetime(2020, 1, 1))
        change = self.write_photo(library / "change.jpg", datetime(2020, 1, 1))
        remove = self.write_photo(library / "remove.jpg", datetime(2020, 1, 1))
        run_scan(library)

        self.write_photo(change, datetime(2022, 6, 6), size=(20, 20))
        # Make sure the modification time differs even on coarse filesystems.
        stamp = keep.stat().st_mtime + 5
        os.utime(change, (stamp, stamp))
        remove.unlink()
        self.write_photo(library / "added.jpg", datetime(2023, 3, 3))
        events = run_scan(library, scan_id=2)
        done = events[-1]
        self.assertEqual((done["updated"], done["unchanged"], done["deleted"]), (2, 1, 1))
        self.assertEqual(rows(), [
            ("added.jpg", "2023-03-03T00:00:00"),
            ("change.jpg", "2022-06-06T00:00:00"),
            ("keep.jpg", "2020-01-01T00:00:00"),
        ])

    def test_unchanged_rescan_reads_no_metadata(self):
        library = self.make_library()
        for day in range(1, 41):
            self.write_photo(library / f"photo-{day}.jpg", datetime(2019, 3, day % 28 + 1, 10))
        events = run_scan(library)
        self.assertEqual((events[-1]["total_indexed"], events[-1]["updated"], events[-1]["errors"]), (40, 40, 0))
        with patch.object(indexer, "read_metadata") as read:
            events = run_scan(library, scan_id=2)
            read.assert_not_called()
        self.assertEqual(events[-1]["unchanged"], 40)

    def test_metadata_reads_run_in_parallel(self):
        library = self.make_library()
        for index in range(8):
            self.write_photo(library / f"{index}.jpg")
        threads = set()
        real = indexer.read_metadata

        def record(path, media_type):
            threads.add(threading.get_ident())
            return real(path, media_type)

        with patch.object(indexer, "METADATA_WORKERS", 4), patch.object(indexer, "read_metadata", side_effect=record):
            run_scan(library)
        self.assertNotIn(threading.get_ident(), threads)

    def test_counts_metadata_errors_per_file(self):
        library = self.make_library()
        for name in ("good.jpg", "bad.jpg"):
            self.write_photo(library / name)
        real = indexer.read_metadata

        def flaky(path, media_type):
            if path.name == "bad.jpg":
                raise RuntimeError("unreadable")
            return real(path, media_type)

        with patch.object(indexer, "read_metadata", side_effect=flaky):
            events = run_scan(library)
        self.assertEqual((events[-1]["errors"], events[-1]["total_indexed"]), (1, 1))

    def test_cancel_stops_scan_and_keeps_stored_rows(self):
        library = self.make_library()
        for index in range(20):
            self.write_photo(library / f"{index}.jpg")
        stop = threading.Event()
        real = indexer.read_metadata

        def stop_on_first(path, media_type):
            stop.set()
            return real(path, media_type)

        with patch.object(indexer, "read_metadata", side_effect=stop_on_first):
            events = run_scan(library, stop=stop)
        self.assertEqual(events[-1]["type"], "scan_cancelled")

    def test_missing_folder_reports_empty_scan(self):
        events = run_scan(self.root / "does-not-exist")
        self.assertEqual(events[-1]["type"], "scan_complete")
        self.assertEqual(events[-1]["total_indexed"], 0)

    def test_batches_are_committed_during_large_scans(self):
        library = self.make_library()
        for index in range(12):
            self.write_photo(library / f"{index}.jpg")
        with patch.object(indexer, "BATCH_COMMIT_SIZE", 5):
            events = run_scan(library)
        self.assertGreaterEqual(sum(event["type"] == "batch_committed" for event in events), 2)
