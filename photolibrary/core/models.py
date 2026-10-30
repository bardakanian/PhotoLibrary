import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Everything read from one file during indexing."""

    captured_at: datetime | None
    width: int | None = None
    height: int | None = None
    camera: str | None = None
    duration: float | None = None
    live_motion: bool = False  # a video carrying Apple's Live Photo metadata


@dataclass(frozen=True, slots=True)
class MediaItem:
    """One indexed photo or video. Kept small: the grid holds tens of thousands of these."""

    id: int
    file_path: str
    album: str
    file_size: int
    modified_ns: int
    captured_at: datetime | None
    media_type: str
    width: int | None = None
    height: int | None = None
    camera: str | None = None
    duration: float | None = None

    live_video_id: int | None = None   # for a Live Photo: its motion clip
    live_video: str | None = None

    @property
    def is_live(self) -> bool:
        return self.live_video is not None

    @property
    def path(self) -> Path:
        return Path(self.file_path)

    @property
    def name(self) -> str:
        return os.path.basename(self.file_path)

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def month_key(self) -> tuple[int, int] | None:
        return (self.captured_at.year, self.captured_at.month) if self.captured_at else None


@dataclass(frozen=True, slots=True)
class Album:
    name: str
    count: int
    cover: MediaItem | None
    root: str = ""  # the library folder it belongs to


@dataclass(frozen=True, slots=True)
class YearMediaCount:
    year: int
    photos: int
    videos: int

    @property
    def total(self) -> int:
        return self.photos + self.videos


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    """Folder-wide aggregates calculated from the index only (no file access)."""

    total_photos: int
    total_videos: int
    combined_total: int
    busiest_day: tuple[int, int] | None          # (month, day)
    busiest_day_count: int
    largest_year: int | None
    largest_year_count: int
    media_by_year: tuple[YearMediaCount, ...]
    most_active_month: int | None
    most_active_month_count: int
    total_bytes: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    undated_count: int = 0
    top_camera: str | None = None
    top_camera_count: int = 0
    camera_count: int = 0
    live_photos: int = 0
