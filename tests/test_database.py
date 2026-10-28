import json
import threading
from datetime import datetime

from photolibrary.core import database
from photolibrary.core.indexer import MediaIndexer
from photolibrary.core.search import parse_query
from photolibrary.core.settings import AppSettings, SettingsStore

from .base import TempAppTestCase


def insert(folder, rows):
    """rows: (file_name, album, media_type, capture_ts or None)."""
    connection = database.open_database()
    root = str(folder.resolve())
    for name, album, media_type, stamp in rows:
        taken = datetime.fromisoformat(stamp) if stamp else None
        connection.execute("""
            INSERT INTO media (folder_root, file_path, file_name, album, file_size, modified_ns, capture_ts,
                               capture_year, capture_month, capture_day, media_type)
            VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)
        """, (root, str(folder / album / name), name, album, stamp,
              taken.year if taken else None, taken.month if taken else None, taken.day if taken else None,
              media_type))
    connection.commit()
    connection.close()


class RepositoryTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.make_library()
        self.repo = database.MediaRepository()
        insert(self.library, [
            ("IMG_0001.jpg", "", "image", "2021-05-14T10:00:00"),
            ("beach.mov", "Trips", "video", "2023-08-01T09:00:00"),
            ("sunset.jpg", "Trips", "image", "2023-08-01T20:00:00"),
            ("scan.png", "Archive", "image", None),
            ("IMG_0002.heic", "Archive", "image", "2019-12-25T08:00:00"),
        ])

    def names(self, **kwargs):
        return [item.name for item in self.repo.query(self.library, **kwargs)]

    def test_newest_first_with_undated_last(self):
        self.assertEqual(self.names(), ["sunset.jpg", "beach.mov", "IMG_0001.jpg", "IMG_0002.heic", "scan.png"])

    def test_kind_filters(self):
        self.assertEqual(self.names(kind="videos"), ["beach.mov"])
        self.assertNotIn("beach.mov", self.names(kind="photos"))

    def test_album_filter(self):
        self.assertEqual(self.names(album="Archive"), ["IMG_0002.heic", "scan.png"])
        self.assertEqual(self.names(album=""), ["IMG_0001.jpg"])

    def test_search_by_filename_and_date(self):
        self.assertEqual(self.names(search=parse_query("img")), ["IMG_0001.jpg", "IMG_0002.heic"])
        self.assertEqual(self.names(search=parse_query("2023")), ["sunset.jpg", "beach.mov"])
        self.assertEqual(self.names(search=parse_query("December")), ["IMG_0002.heic"])
        self.assertEqual(self.names(search=parse_query("may 14 2021")), ["IMG_0001.jpg"])
        self.assertEqual(self.names(search=parse_query("sun 2023-08")), ["sunset.jpg"])
        self.assertEqual(self.names(search=parse_query("nothing-here")), [])

    def test_search_escapes_like_wildcards(self):
        insert(self.library, [("100%_done.jpg", "", "image", "2020-01-01T00:00:00")])
        self.assertEqual(self.names(search=parse_query("%_")), ["100%_done.jpg"])

    def test_filters_combine(self):
        self.assertEqual(self.names(kind="photos", album="Trips", search=parse_query("2023")), ["sunset.jpg"])

    def test_counts_and_albums(self):
        self.assertEqual(self.repo.counts(self.library), {"all": 5, "photos": 4, "videos": 1})
        albums = self.repo.albums(self.library)
        self.assertEqual([(album.name, album.count) for album in albums], [("Archive", 2), ("Trips", 2)])
        self.assertEqual(albums[1].cover.name, "sunset.jpg")

    def test_results_are_isolated_by_folder(self):
        other = self.make_library("other")
        insert(other, [("elsewhere.jpg", "", "image", "2024-01-01T00:00:00")])
        self.assertNotIn("elsewhere.jpg", self.names())
        self.assertEqual([item.name for item in self.repo.query(other)], ["elsewhere.jpg"])

    def test_remove_many_only_touches_requested_folder(self):
        other = self.make_library("other")
        insert(other, [("IMG_0001.jpg", "", "image", "2021-05-14T10:00:00")])
        items = self.repo.query(self.library, search=parse_query("img"))
        other_ids = [item.id for item in self.repo.query(other)]
        removed = self.repo.remove_many(self.library, [item.id for item in items] + other_ids)
        self.assertEqual(removed, 2)
        self.assertEqual(len(self.repo.query(other)), 1)
        self.assertEqual(self.repo.counts(self.library)["all"], 3)

    def test_get_returns_full_item(self):
        first = self.repo.query(self.library)[0]
        self.assertEqual(self.repo.get(first.id), first)
        self.assertIsNone(self.repo.get(999_999))

    def test_query_while_indexer_writes(self):
        """WAL mode: readers on other threads are not blocked by an in-progress scan."""
        library = self.make_library("live")
        for index in range(30):
            self.write_photo(library / f"{index}.jpg")
        errors = []

        def read_repeatedly():
            try:
                for _ in range(20):
                    self.repo.query(library)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        reader = threading.Thread(target=read_repeatedly)
        reader.start()
        MediaIndexer(library, threading.Event(), 1, lambda event: None).run()
        reader.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.repo.query(library)), 30)

    def test_large_query_is_fast(self):
        import time
        connection = database.open_database()
        root = str(self.library.resolve())
        connection.executemany("""
            INSERT INTO media (folder_root, file_path, file_name, file_size, modified_ns, capture_ts, media_type)
            VALUES (?, ?, ?, 1, 1, ?, 'image')
        """, [(root, f"/big/{index}.jpg", f"{index}.jpg", f"20{10 + index % 15}-01-01T00:00:{index % 60:02d}")
              for index in range(50_000)])
        connection.commit()
        connection.close()
        started = time.perf_counter()
        items = self.repo.query(self.library)
        self.assertEqual(len(items), 50_005)
        self.assertLess(time.perf_counter() - started, 2.0)
        # Filename and date searches scan the folder's rows; they must stay interactive.
        for text, expected in (("123", None), ("2015", None), ("May 2020", 0)):
            started = time.perf_counter()
            found = self.repo.query(self.library, search=parse_query(text))
            self.assertLess(time.perf_counter() - started, 0.5, text)
            if expected is not None:
                self.assertEqual(len(found), expected)


class OpenDatabaseTests(TempAppTestCase):
    def test_first_open_from_many_threads_does_not_lock(self):
        errors = []

        def open_and_query():
            try:
                connection = database.open_database()
                connection.execute("SELECT COUNT(*) FROM media").fetchone()
                connection.close()
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=open_and_query) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])


class SettingsTests(TempAppTestCase):
    def test_settings_round_trip_and_preserve_unknown_values(self):
        self.app_dir.mkdir()
        self.config_file.write_text(json.dumps({"library_folder": str(self.root), "legacy": 42}))
        store = SettingsStore()
        loaded = store.load()
        self.assertEqual(loaded.library_folder, self.root)
        loaded.theme, loaded.zoom, loaded.view_mode, loaded.time_format = "dark", 200, "months", "12"
        store.save(loaded)
        saved = json.loads(self.config_file.read_text())
        self.assertEqual((saved["legacy"], saved["theme"], saved["zoom"]), (42, "dark", 200))
        self.assertEqual((store.load().view_mode, store.load().time_format), ("months", "12"))

    def test_invalid_values_fall_back_to_defaults(self):
        self.app_dir.mkdir()
        self.config_file.write_text(json.dumps({
            "library_folder": str(self.root / "missing"), "theme": "neon", "zoom": "big", "view_mode": "decades",
            "time_format": "36",
        }))
        loaded = SettingsStore().load()
        self.assertEqual((loaded.library_folder, loaded.theme, loaded.view_mode), (None, "system", "all"))
        self.assertEqual(loaded.zoom, AppSettings().zoom)
        self.assertEqual(loaded.time_format, "system")

    def test_corrupt_config_is_ignored(self):
        self.app_dir.mkdir()
        self.config_file.write_text("{not json")
        self.assertEqual(SettingsStore().load(), AppSettings())
