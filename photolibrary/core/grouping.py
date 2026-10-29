"""Group a newest-first item list into the sections and summary tiles the grid shows."""

import calendar
from dataclasses import dataclass
from typing import Hashable, Sequence

from .models import MediaItem


@dataclass(frozen=True, slots=True)
class Section:
    key: Hashable
    title: str
    start: int
    count: int


@dataclass(frozen=True, slots=True)
class Summary:
    """One Years or Months tile: a group of items represented by a cover item."""

    key: Hashable  # (year,) or (year, month); None for undated items
    title: str
    subtitle: str
    count: int
    first_index: int
    cover: MediaItem


def month_title(key: tuple[int, int] | None) -> str:
    return "Undated" if key is None else f"{calendar.month_name[key[1]]} {key[0]}"


def group_consecutive(keys: Sequence[Hashable]) -> list[tuple[Hashable, int, int]]:
    """(key, start, count) for each run of equal keys."""
    groups: list[tuple[Hashable, int, int]] = []
    start = 0
    for index in range(1, len(keys) + 1):
        if index == len(keys) or keys[index] != keys[start]:
            groups.append((keys[start], start, index - start))
            start = index
    return groups


def group_by_month(items: Sequence[MediaItem]) -> list[Section]:
    return [Section(key, month_title(key), start, count)
            for key, start, count in group_consecutive([item.month_key for item in items])]


def _count_label(items: Sequence[MediaItem]) -> str:
    videos = sum(1 for item in items if item.is_video)
    photos = len(items) - videos
    parts = []
    if photos:
        parts.append(f"{photos:,} photo{'s' if photos != 1 else ''}")
    if videos:
        parts.append(f"{videos:,} video{'s' if videos != 1 else ''}")
    return ", ".join(parts)


def _cover(items: Sequence[MediaItem]) -> MediaItem:
    """The newest photo in the group (video stills make poorer covers), whatever the sort order.

    Keeping covers independent of the sort order means their large cover thumbnails are
    prepared once and stay valid.
    """
    photos = [item for item in items if not item.is_video] or list(items)
    return max(photos, key=lambda item: (item.captured_at is not None, item.captured_at or 0, item.id))


def summarize(items: Sequence[MediaItem], by: str) -> list[Summary]:
    """One Summary per year (by="year") or per month (by="month"), newest first."""
    if by == "year":
        keys = [(item.captured_at.year,) if item.captured_at else None for item in items]
    else:
        keys = [item.month_key for item in items]
    summaries = []
    for key, start, count in group_consecutive(keys):
        group = items[start:start + count]
        if key is None:
            title = "Undated"
        elif by == "year":
            title = str(key[0])
        else:
            title = calendar.month_name[key[1]]
        summaries.append(Summary(key, title, _count_label(group), count, start, _cover(group)))
    return summaries


def group_summaries_by_year(summaries: Sequence[Summary]) -> list[Section]:
    """Sections for the Months view: one header per year above its month tiles."""
    keys = [summary.key[0] if summary.key else None for summary in summaries]
    return [Section(key, "Undated" if key is None else str(key), start, count)
            for key, start, count in group_consecutive(keys)]


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    """A query result with every grouping the grid modes need, built off the UI thread."""

    items: list[MediaItem]
    month_sections: list[Section]
    months: list[Summary]
    months_by_year: list[Section]
    years: list[Summary]


def build_snapshot(items: list[MediaItem]) -> LibrarySnapshot:
    months = summarize(items, "month")
    return LibrarySnapshot(items, group_by_month(items), months, group_summaries_by_year(months),
                           summarize(items, "year"))
