import json
from datetime import datetime

from photolibrary.core.database import MediaRepository
from photolibrary.core.search import parse_query
from photolibrary.core.settings import SettingsStore

from .base import TempAppTestCase
from .test_library_roots import scan


class HiddenAlbumTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.home, self.drive = self.make_library("Home"), self.make_library("Drive")
        self.write_photo(self.home / "Trips" / "h_trip.jpg", datetime(2021, 5, 1))
        self.write_photo(self.home / "Private" / "h_private.jpg", datetime(2022, 2, 1))
        self.write_photo(self.home / "loose.jpg", datetime(2023, 1, 1))
        self.write_photo(self.drive / "Trips" / "d_trip.jpg", datetime(2020, 7, 1))
        scan([self.home, self.drive])
        self.repo = MediaRepository()
        self.folders = [self.home, self.drive]
        self.hidden = [(str(self.home.resolve()), "Private"), (str(self.home.resolve()), "Trips")]

    def names(self, folders=None, **kwargs):
        return [item.name for item in self.repo.query(folders or self.folders, **kwargs)]

    def test_hidden_albums_leave_all_photos_search_and_counts(self):
        self.assertEqual(self.names(hidden=self.hidden), ["loose.jpg", "d_trip.jpg"])
        self.assertEqual(self.names(hidden=self.hidden, search=parse_query("2022")), [])
        self.assertEqual(self.repo.counts(self.folders, self.hidden)["all"], 2)
        self.assertEqual(self.repo.library_analytics(self.folders, self.hidden).combined_total, 2)
        # Only that folder's "Trips" is hidden; the other folder's album of the same name stays.
        self.assertEqual(self.names(self.drive, hidden=self.hidden), ["d_trip.jpg"])

    def test_a_hidden_album_still_opens_on_its_own(self):
        self.assertEqual(self.names(self.home, album="Private", hidden=self.hidden), ["h_private.jpg"])

    def test_nothing_hidden_changes_nothing(self):
        self.assertEqual(len(self.names()), 4)
        self.assertEqual(len(self.names(hidden=[])), 4)
        self.assertEqual(self.repo.counts(self.folders)["all"], 4)

    def test_hidden_albums_round_trip_in_settings(self):
        store = SettingsStore()
        settings = store.load()
        settings.hidden_albums = self.hidden
        store.save(settings)
        self.assertEqual(store.load().hidden_albums, self.hidden)
        self.config_file.write_text(json.dumps({"hidden_albums": [["/a", "B"], ["/a"], "x", ["/a", ""], ["/a", "B"]]}))
        self.assertEqual(store.load().hidden_albums, [("/a", "B")])
