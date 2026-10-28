import time
import unittest
from datetime import datetime

from photolibrary.core.cache import LRUCache
from photolibrary.core.grid_layout import GridLayout
from photolibrary.core.grouping import group_by_month, group_summaries_by_year, summarize
from photolibrary.core.models import MediaItem
from photolibrary.core.search import parse_query


def item(index, stamp, media_type="image"):
    taken = datetime.fromisoformat(stamp) if stamp else None
    return MediaItem(index, f"/lib/{index}.jpg", "", 1, 1, taken, media_type)


class CacheTests(unittest.TestCase):
    def test_evicts_least_recently_used_by_cost(self):
        cache = LRUCache(max_cost=10)
        cache.put("a", 1, cost=4)
        cache.put("b", 2, cost=4)
        cache.get("a")  # a is now most recent
        cache.put("c", 3, cost=4)
        self.assertIn("a", cache)
        self.assertNotIn("b", cache)
        self.assertEqual(cache.total_cost, 8)

    def test_replacing_updates_cost_and_oversized_item_is_kept(self):
        cache = LRUCache(max_cost=5)
        cache.put("a", 1, cost=2)
        cache.put("a", 1, cost=3)
        self.assertEqual(cache.total_cost, 3)
        cache.put("huge", 2, cost=50)
        self.assertEqual(list(cache._items), ["huge"])

    def test_discard_where(self):
        cache = LRUCache(max_cost=100)
        for key in [(1, 128), (1, 256), (2, 128)]:
            cache.put(key, key)
        cache.discard_where(lambda key: key[0] == 1)
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.total_cost, 1)


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            item(1, "2024-05-20T10:00:00"), item(2, "2024-05-01T10:00:00", "video"),
            item(3, "2024-04-30T10:00:00"), item(4, "2023-12-31T23:59:59"), item(5, None),
        ]

    def test_group_by_month(self):
        sections = group_by_month(self.items)
        self.assertEqual([(s.title, s.start, s.count) for s in sections], [
            ("May 2024", 0, 2), ("April 2024", 2, 1), ("December 2023", 3, 1), ("Undated", 4, 1),
        ])

    def test_summaries(self):
        years = summarize(self.items, "year")
        self.assertEqual([(s.title, s.count, s.first_index) for s in years],
                         [("2024", 3, 0), ("2023", 1, 3), ("Undated", 1, 4)])
        self.assertEqual(years[0].subtitle, "2 photos, 1 video")
        months = summarize(self.items, "month")
        self.assertEqual([s.title for s in months], ["May", "April", "December", "Undated"])
        self.assertEqual([(s.title, s.count) for s in group_summaries_by_year(months)],
                         [("2024", 2), ("2023", 1), ("Undated", 1)])

    def test_cover_prefers_photos(self):
        group = [item(1, "2024-05-20T10:00:00", "video"), item(2, "2024-05-01T10:00:00")]
        self.assertEqual(summarize(group, "month")[0].cover.id, 2)

    def test_empty(self):
        self.assertEqual(group_by_month([]), [])
        self.assertEqual(summarize([], "year"), [])


class GridLayoutTests(unittest.TestCase):
    def layout(self, counts=(5, 3), width=436, cell=99):
        # 436 - 2*16 margin = 404 available -> 4 columns of 99.5 px with 2 px spacing.
        return GridLayout(list(counts), width, cell, spacing=2, margin=16, header_height=40, section_gap=20)

    def test_columns_fill_width(self):
        layout = self.layout()
        self.assertEqual(layout.columns, 4)
        last = layout.rect_for(3)
        self.assertEqual(last.x + last.width, 436 - 16)

    def test_section_offsets(self):
        layout = self.layout()
        first, second = layout.sections
        self.assertEqual((first.rows, second.rows), (2, 1))
        self.assertEqual(second.top, first.bottom + 20)
        self.assertEqual(layout.rect_for(5).y, second.cells_top)
        self.assertEqual(layout.rect_for(5).x, 16)

    def test_index_at_round_trips(self):
        layout = self.layout()
        for index in range(8):
            rect = layout.rect_for(index)
            self.assertEqual(layout.index_at(rect.x + rect.width / 2, rect.y + rect.height / 2), index)
        header = layout.header_rect(1)
        self.assertIsNone(layout.index_at(20, header.y + 5))
        # Empty slot after the last item of the first section.
        row2 = layout.rect_for(4)
        self.assertIsNone(layout.index_at(row2.x + 150, row2.y + 10))

    def test_visible_range(self):
        layout = self.layout()
        first_row = layout.rect_for(0)
        self.assertEqual(list(layout.indexes_in(first_row.y, first_row.y + 10)), [0, 1, 2, 3])
        self.assertEqual(list(layout.indexes_in(0, layout.total_height)), list(range(8)))
        self.assertEqual(list(layout.indexes_in(layout.total_height + 10, layout.total_height + 50)), [])

    def test_keyboard_navigation_crosses_sections(self):
        layout = self.layout()
        self.assertEqual(layout.index_below(1), 4)  # row 2 of section 1 only has index 4
        self.assertEqual(layout.index_below(4), 5)
        self.assertEqual(layout.index_below(3), 4)
        self.assertEqual(layout.index_above(6), 4)
        self.assertEqual(layout.index_above(2), 2)
        self.assertEqual(layout.index_below(7), 7)

    def test_rubber_band(self):
        layout = self.layout()
        a, b = layout.rect_for(0), layout.rect_for(1)
        self.assertEqual(layout.indexes_in_rect(a.x + 5, a.y + 5, b.x + 5, b.y + 5), [0, 1])

    def test_empty_layout(self):
        layout = GridLayout([], 500, 100)
        self.assertEqual(layout.total_height, 0)
        self.assertIsNone(layout.index_at(10, 10))
        self.assertEqual(list(layout.indexes_in(0, 100)), [])

    def test_fifty_thousand_items_lay_out_quickly(self):
        counts = [170] * 300  # 51,000 items over 25 years of months
        started = time.perf_counter()
        layout = GridLayout(counts, 1400, 160)
        visible = layout.indexes_in(500_000, 501_000)
        elapsed = time.perf_counter() - started
        self.assertEqual(layout.item_count, 51_000)
        self.assertTrue(len(visible) > 0)
        self.assertLess(elapsed, 0.05)


class SearchParsingTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_query("2023").year, 2023)
        self.assertEqual((parse_query("May").month, parse_query("may").year), (5, None))
        query = parse_query("14 May 2023")
        self.assertEqual((query.year, query.month, query.day, query.name_terms), (2023, 5, 14, ()))
        query = parse_query("2021-07-04")
        self.assertEqual((query.year, query.month, query.day), (2021, 7, 4))
        self.assertEqual((parse_query("2020/02").year, parse_query("2020/02").month), (2020, 2))
        self.assertEqual(parse_query("Sept 3rd").day, 3)

    def test_names(self):
        query = parse_query("IMG_1234 beach")
        self.assertEqual(query.name_terms, ("img_1234", "beach"))
        self.assertIsNone(query.year)
        # A bare small number is part of a filename unless a month name is present.
        self.assertEqual(parse_query("12").name_terms, ("12",))
        self.assertEqual(parse_query("2023-13-40").name_terms, ("2023-13-40",))

    def test_empty(self):
        self.assertTrue(parse_query("   ").is_empty)
        self.assertEqual(parse_query("").sql(), ("", []))


class SnapshotTests(unittest.TestCase):
    def test_snapshot_has_every_grouping(self):
        from photolibrary.core.grouping import build_snapshot
        items = [item(1, "2024-05-20T10:00:00"), item(2, "2023-01-01T00:00:00")]
        snapshot = build_snapshot(items)
        self.assertEqual(len(snapshot.month_sections), 2)
        self.assertEqual([s.title for s in snapshot.years], ["2024", "2023"])
        self.assertEqual([s.title for s in snapshot.months_by_year], ["2024", "2023"])


class WorkQueueTests(unittest.TestCase):
    def test_jobs_run_deduplicated_and_retain_drops_unwanted(self):
        import threading
        from photolibrary.core.workqueue import WorkQueue
        gate = threading.Event()
        ran = []
        done = threading.Event()
        queue = WorkQueue(1, "test")
        try:
            queue.submit("block", gate.wait)  # occupies the only worker
            time.sleep(0.05)
            for key in "abc":
                self.assertTrue(queue.submit(key, lambda key=key: ran.append(key)))
            self.assertFalse(queue.submit("a", lambda: ran.append("dup")))
            self.assertEqual(queue.retain(["c", "a"]), ["b"])
            queue.submit("end", done.set)
            gate.set()
            self.assertTrue(done.wait(2))
            self.assertEqual(ran, ["c", "a"])
        finally:
            queue.shutdown()


class FormattingTests(unittest.TestCase):
    def test_labels(self):
        from photolibrary.core.formatting import format_dimensions, format_duration, format_size, format_date
        self.assertEqual(format_duration(65.4), "1:05")
        self.assertEqual(format_duration(3725), "1:02:05")
        self.assertEqual(format_duration(None), "")
        self.assertEqual(format_size(512), "512 bytes")
        self.assertEqual(format_size(2_500_000), "2.5 MB")
        self.assertEqual(format_dimensions(4032, 3024), "4,032 × 3,024  ·  12.2 MP")
        self.assertEqual(format_date(datetime(2024, 5, 3, 9, 7)), "Friday, May 3, 2024 at 09:07")
        self.assertEqual(format_date(None), "Unknown date")

    def test_twelve_and_twenty_four_hour_times(self):
        from photolibrary.core.formatting import format_date, format_time
        self.assertEqual(format_time(datetime(2024, 5, 3, 0, 5)), "00:05")
        self.assertEqual(format_time(datetime(2024, 5, 3, 0, 5), hour12=True), "12:05 AM")
        self.assertEqual(format_time(datetime(2024, 5, 3, 12, 0), hour12=True), "12:00 PM")
        self.assertEqual(format_time(datetime(2024, 5, 3, 23, 59), hour12=True), "11:59 PM")
        self.assertEqual(format_date(datetime(2024, 5, 3, 13, 30), hour12=True), "Friday, May 3, 2024 at 1:30 PM")
        self.assertEqual(format_date(datetime(2024, 5, 3, 13, 30)), "Friday, May 3, 2024 at 13:30")


class NearestIndexTests(unittest.TestCase):
    def test_nearest_index(self):
        layout = GridLayout([5, 3], 436, 99, spacing=2, margin=16, header_height=40, section_gap=20)
        self.assertEqual(layout.nearest_index(5, 5), 0)                          # above first cells
        row2 = layout.rect_for(4)
        self.assertEqual(layout.nearest_index(row2.x + 300, row2.y + 5), 4)      # empty slot -> last in row
        self.assertEqual(layout.nearest_index(10, layout.total_height + 100), 7)
