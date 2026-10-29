"""Pair the two files of an iPhone Live Photo: a still (HEIC/JPG) and a short motion clip (MOV).

Exports and backups keep them as separate files with the same name in the same folder,
for example IMG_4330.HEIC and IMG_4330.MOV. Shown separately, every Live Photo would appear
twice: once as a photo and once as a 1-3 second "video".
"""

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

# A motion clip without Apple's Live Photo tag still pairs when it is this short and this
# close in time to its photo.
MAX_UNTAGGED_SECONDS = 4.0
MAX_TIME_GAP_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class PairingRow:
    id: int
    file_path: str
    media_type: str
    captured_at: datetime | None
    duration: float | None
    live_motion: bool  # the video carries Apple's Live Photo metadata


def _belong_together(photo: PairingRow, video: PairingRow) -> bool:
    if video.live_motion:
        return True
    if video.duration is None or video.duration > MAX_UNTAGGED_SECONDS:
        return False
    if photo.captured_at and video.captured_at:
        return abs((photo.captured_at - video.captured_at).total_seconds()) <= MAX_TIME_GAP_SECONDS
    return True


def pair_live_photos(rows: list[PairingRow]) -> list[tuple[int, int]]:
    """(photo id, video id) for every Live Photo: one photo and one video sharing a folder and name."""
    groups: dict[tuple[str, str], list[PairingRow]] = defaultdict(list)
    for row in rows:
        folder, name = os.path.split(row.file_path)
        groups[(folder, os.path.splitext(name)[0].lower())].append(row)
    pairs = []
    for members in groups.values():
        photos = [row for row in members if row.media_type == "image"]
        videos = [row for row in members if row.media_type == "video"]
        # Ambiguous groups (two photos or two videos with one name) are left alone.
        if len(photos) == 1 and len(videos) == 1 and _belong_together(photos[0], videos[0]):
            pairs.append((photos[0].id, videos[0].id))
    return pairs
