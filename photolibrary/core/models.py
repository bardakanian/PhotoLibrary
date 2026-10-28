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
