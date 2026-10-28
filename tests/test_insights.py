"""Library Insights queries (ported from On This Day, plus storage, date span and cameras)."""

from datetime import datetime

from photolibrary.core import database

from .base import TempAppTestCase


def insert(folder, rows):
    """rows: (media_type, year, month, day[, size, camera])."""
    connection = database.open_database()
    folder_root = str(folder.resolve())
    for index, row in enumerate(rows):
        media_type, year, month, day = row[:4]
        size = row[4] if len(row) > 4 else index + 1
        camera = row[5] if len(row) > 5 else None
        stamp = f"{year:04d}-{month:02d}-{day:02d}T12:00:00" if None not in (year, month, day) else None
        connection.execute("""
            INSERT INTO media (folder_root, file_path, file_name, file_size, modified_ns, capture_ts,
                               capture_year, capture_month, capture_day, media_type, camera)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
        """, (folder_root, str(folder / f"media-{index}"), f"media-{index}", size, stamp, year, month, day,
              media_type, camera))
    connection.commit()
    connection.close()


class InsightsTests(TempAppTestCase):
    def analytics(self, folder):
        return database.MediaRepository().library_analytics(folder)

    def test_photo_video_and_combined_totals(self):
        folder = self.root / "totals"
        insert(folder, [("image", 2020, 1, 1), ("image", 2021, 2, 2), ("video", 2022, 3, 3)])
        result = self.analytics(folder)
        self.assertEqual((result.total_photos, result.total_videos, result.combined_total), (2, 1, 3))

    def test_busiest_day_including_february_29(self):
        folder = self.root / "days"
        insert(folder, [("image", 2020, 2, 29), ("video", 2024, 2, 29), ("image", 2021, 7, 18)])
        result = self.analytics(folder)
        self.assertEqual((result.busiest_day, result.busiest_day_count), ((2, 29), 2))

    def test_largest_year_and_media_by_year(self):
        folder = self.root / "years"
        insert(folder, [
            ("image", 2019, 1, 1),
            ("video", 2021, 1, 1), ("image", 2021, 2, 2), ("video", 2021, 3, 3),
            ("image", 2020, 4, 4), ("image", 2020, 5, 5),
        ])
        result = self.analytics(folder)
        self.assertEqual((result.largest_year, result.largest_year_count), (2021, 3))
        self.assertEqual([(row.year, row.photos, row.videos) for row in result.media_by_year],
                         [(2019, 1, 0), (2020, 2, 0), (2021, 1, 2)])

    def test_most_active_month(self):
        folder = self.root / "months"
        insert(folder, [("image", 2019, 8, 1), ("video", 2020, 8, 2), ("image", 2021, 8, 3),
                        ("image", 2022, 12, 4), ("video", 2023, 12, 5)])
        result = self.analytics(folder)
        self.assertEqual((result.most_active_month, result.most_active_month_count), (8, 3))

    def test_empty_library(self):
        result = self.analytics(self.root / "empty")
        self.assertEqual((result.combined_total, result.total_bytes, result.camera_count), (0, 0, 0))
        self.assertIsNone(result.busiest_day)
        self.assertIsNone(result.largest_year)
        self.assertIsNone(result.oldest)
        self.assertIsNone(result.top_camera)
        self.assertEqual(result.media_by_year, ())

    def test_isolated_by_folder(self):
        first, second = self.root / "first", self.root / "second"
        insert(first, [("image", 2020, 4, 3), ("video", 2020, 4, 3)])
        insert(second, [("video", 2024, 9, 13)])
        self.assertEqual(self.analytics(first).busiest_day, (4, 3))
        self.assertEqual((self.analytics(second).total_photos, self.analytics(second).total_videos), (0, 1))

    def test_missing_date_parts_are_ignored_only_where_needed(self):
        folder = self.root / "partial"
        insert(folder, [("image", None, None, None), ("video", 2020, None, None),
                        ("image", None, 6, None), ("image", None, 6, 15)])
        result = self.analytics(folder)
        self.assertEqual(result.combined_total, 4)
        self.assertEqual((result.largest_year, result.largest_year_count), (2020, 1))
        self.assertEqual(result.busiest_day, (6, 15))
        self.assertEqual((result.most_active_month, result.most_active_month_count), (6, 2))
        self.assertEqual(result.undated_count, 4)  # none of them has a full capture timestamp

    def test_ties_choose_earliest_calendar_value(self):
        folder = self.root / "ties"
        insert(folder, [("image", 2022, 7, 18), ("video", 2022, 7, 18),
                        ("image", 2020, 3, 20), ("video", 2020, 3, 20)])
        result = self.analytics(folder)
        self.assertEqual((result.busiest_day, result.largest_year, result.most_active_month), ((3, 20), 2020, 3))

    def test_storage_date_span_and_cameras(self):
        folder = self.root / "extras"
        insert(folder, [
            ("image", 2018, 5, 1, 1_000, "Pixel 8"),
            ("image", 2023, 7, 9, 2_000, "Canon EOS R5"),
            ("video", 2021, 1, 1, 5_000, "Pixel 8"),
            ("image", None, None, None, 500, None),
            ("image", 2022, 2, 2, 250, "Canon EOS R5"),
        ])
        result = self.analytics(folder)
        self.assertEqual(result.total_bytes, 8_750)
        self.assertEqual((result.oldest, result.newest), (datetime(2018, 5, 1, 12), datetime(2023, 7, 9, 12)))
        self.assertEqual(result.undated_count, 1)
        # Tie on count: alphabetical order decides.
        self.assertEqual((result.top_camera, result.top_camera_count, result.camera_count), ("Canon EOS R5", 2, 2))
