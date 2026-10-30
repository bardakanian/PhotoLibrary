import json
import threading
from datetime import datetime

from photolibrary.core.database import MediaRepository
from photolibrary.core.indexer import MediaIndexer, index_folders
from photolibrary.core.library_roots import display_names, normalize_roots, root_conflict, root_for
from photolibrary.core.settings import SettingsStore
from photolibrary.core.trash import trash_items

from .base import TempAppTestCase


def scan(folders):
    events = []
    index_folders(folders, threading.Event(), 1, events.append)
    return events


class RootRulesTests(TempAppTestCase):
    def test_overlapping_folders_are_refused(self):
        photos = self.make_library("Photos")
        (photos / "Trips").mkdir()
        other = self.make_library("Other")
        self.assertIsNone(root_conflict([photos], other))
        self.assertIn("already in your library", root_conflict([photos], photos))
        self.assertIn("inside", root_conflict([photos], photos / "Trips"))
        self.assertIn("contains", root_conflict([photos / "Trips"], photos))
        # A folder whose name merely starts the same way is not inside it.
        self.assertIsNone(root_conflict([photos], self.make_library("Photos 2")))

    def test_root_for_and_normalize(self):
        photos, other = self.make_library("Photos"), self.make_library("Other")
        self.assertEqual(root_for(photos / "a" / "b.jpg", [other, photos]), photos.resolve())
        self.assertIsNone(root_for(self.root / "stray.jpg", [other, photos]))
        self.assertEqual(normalize_roots([photos, str(photos), other]), [str(photos.resolve()), str(other.resolve())])
        self.assertEqual(normalize_roots(photos), [str(photos.resolve())])

    def test_display_names_tell_same_named_folders_apart(self):
        names = display_names(["/Volumes/Backup/Photos", "/Users/me/Photos", "/Users/me/Videos"])
        self.assertEqual(names, {"/Volumes/Backup/Photos": "Photos (Backup)", "/Users/me/Photos": "Photos (me)",
                                 "/Users/me/Videos": "Videos"})


class MultiFolderLibraryTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.make_library("Home")
        self.drive = self.make_library("Drive")
        self.write_photo(self.home / "Trips" / "h1.jpg", datetime(2021, 5, 1))
        self.write_photo(self.home / "h2.jpg", datetime(2023, 1, 1))
        self.write_photo(self.drive / "Trips" / "d1.jpg", datetime(2022, 7, 1))
        self.write_photo(self.drive / "Family" / "d2.jpg", datetime(2020, 3, 1))
        self.events = scan([self.home, self.drive])
        self.repo = MediaRepository()

    def names(self, folders, **kwargs):
        return [item.name for item in self.repo.query(folders, **kwargs)]

    def test_scan_reports_all_folders_as_one(self):
        types = [event["type"] for event in self.events]
        self.assertEqual((types[0], types[-1], types.count("scan_started")), ("scan_started", "scan_complete", 1))
        done = self.events[-1]
        self.assertEqual((done["total_indexed"], done["updated"], done["missing_folders"]), (4, 4, []))
        progress = [event for event in self.events if event["type"] == "scan_progress"]
        self.assertEqual({event["folder_number"] for event in progress}, {1, 2})
        self.assertTrue(all(event["folder_count"] == 2 for event in progress))

    def test_queries_merge_folders_by_date(self):
        self.assertEqual(self.names([self.home, self.drive]), ["h2.jpg", "d1.jpg", "h1.jpg", "d2.jpg"])
        self.assertEqual(self.names([self.home, self.drive], oldest_first=True), ["d2.jpg", "h1.jpg", "d1.jpg", "h2.jpg"])
        self.assertEqual(self.names(self.drive), ["d1.jpg", "d2.jpg"])
        self.assertEqual(self.names([]), [])
        self.assertEqual(self.repo.counts([self.home, self.drive])["all"], 4)
        self.assertEqual(self.repo.counts(self.home)["all"], 2)
        self.assertEqual(self.repo.library_analytics([self.home, self.drive]).combined_total, 4)

    def test_same_album_name_in_two_folders_stays_separate(self):
        albums = self.repo.albums([self.home, self.drive])
        self.assertEqual([(album.name, album.root, album.count) for album in albums], [
            ("Family", str(self.drive.resolve()), 1),
            ("Trips", str(self.home.resolve()), 1),
            ("Trips", str(self.drive.resolve()), 1),
        ])
        self.assertEqual(self.names(self.drive, album="Trips"), ["d1.jpg"])

    def test_one_missing_folder_does_not_stop_the_others(self):
        self.drive.rename(self.root / "unplugged")
        self.write_photo(self.home / "h3.jpg", datetime(2024, 1, 1))
        done = scan([self.home, self.drive])[-1]
        self.assertEqual(done["type"], "scan_complete")
        self.assertEqual(done["missing_folders"], [str(self.drive)])
        self.assertEqual(done["updated"], 1)
        # The missing folder's photos are still indexed, ready for when the drive is back.
        self.assertEqual(len(self.repo.query(self.drive)), 2)

    def test_every_folder_missing_is_an_error_that_keeps_the_index(self):
        self.home.rename(self.root / "gone1")
        self.drive.rename(self.root / "gone2")
        done = scan([self.home, self.drive])[-1]
        self.assertEqual(done["type"], "scan_error")
        self.assertEqual(len(done["missing_folders"]), 2)
        self.assertEqual(len(self.repo.query([self.home, self.drive])), 4)

    def test_cancel_stops_the_whole_scan(self):
        stop = threading.Event()
        stop.set()
        events = []
        index_folders([self.home, self.drive], stop, 1, events.append)
        self.assertEqual(events[-1]["type"], "scan_cancelled")

    def test_remove_folder_forgets_only_that_folder(self):
        self.assertEqual(self.repo.remove_folder(self.drive), 2)
        self.assertEqual(self.names([self.home, self.drive]), ["h2.jpg", "h1.jpg"])
        self.assertTrue((self.drive / "Trips" / "d1.jpg").exists())  # files are never touched

    def test_trash_works_in_every_folder_and_refuses_outside(self):
        items = {item.name: item for item in self.repo.query([self.home, self.drive])}
        moved = []
        result = trash_items([self.home, self.drive], [items["h1.jpg"], items["d1.jpg"]],
                             lambda path: moved.append(path.name) or True)
        self.assertEqual((sorted(moved), result.failed), (["d1.jpg", "h1.jpg"], []))
        self.assertEqual(self.names([self.home, self.drive]), ["h2.jpg", "d2.jpg"])
        # Trashing with only one folder in the library refuses the other folder's files.
        result = trash_items([self.home], [items["d2.jpg"]], lambda path: True)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("outside the library", result.failed[0][1])

    def test_rescanning_one_folder_leaves_the_other_alone(self):
        (self.home / "h2.jpg").unlink()
        MediaIndexer(self.home, threading.Event(), 2, lambda event: None).run()
        self.assertEqual(self.names([self.home, self.drive]), ["d1.jpg", "h1.jpg", "d2.jpg"])


class MultiFolderSettingsTests(TempAppTestCase):
    def test_folders_round_trip_and_stay_readable_by_older_versions(self):
        store = SettingsStore()
        settings = store.load()
        first, second = self.make_library("A"), self.root / "Unplugged"
        settings.library_folders = [first, second]
        store.save(settings)
        saved = json.loads(self.config_file.read_text())
        self.assertEqual(saved["library_folders"], [str(first), str(second)])
        self.assertEqual(saved["library_folder"], str(first))
        # A missing folder (say, an unplugged drive) is remembered, not dropped.
        self.assertEqual(store.load().library_folders, [first, second])

    def test_bad_folder_entries_are_ignored(self):
        self.app_dir.mkdir(parents=True, exist_ok=True)
        folder = self.make_library("A")
        self.config_file.write_text(json.dumps({"library_folders": [str(folder), 7, "", str(folder)]}))
        self.assertEqual(SettingsStore().load().library_folders, [folder])
