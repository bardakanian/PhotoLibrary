"""Move media to the system Trash and drop it from the index. Runs on a worker thread.

The actual "move to Trash" call is passed in (the UI uses QFile.moveToTrash), so this
module stays free of Qt and can be tested with a fake.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .database import MediaRepository
from .media import thumbnail_cache_files
from .models import MediaItem


@dataclass
class TrashResult:
    trashed: list[MediaItem] = field(default_factory=list)
    failed: list[tuple[MediaItem, str]] = field(default_factory=list)

    @property
    def trashed_ids(self) -> set[int]:
        return {item.id for item in self.trashed}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _move(path: Path, folder: Path, move_to_trash: Callable[[Path], bool]) -> str | None:
    """Trash one file. Returns None on success (or if it is already gone), else the reason."""
    if not _inside(path, folder):
        return "It is outside the library folder."
    if not path.exists():
        return None
    try:
        moved = move_to_trash(path)
    except OSError as exc:
        return str(exc)
    return None if moved else "The system could not move it to the Trash. Check that you can change this folder."


def trash_items(folder: Path, items: Sequence[MediaItem], move_to_trash: Callable[[Path], bool],
                repository: MediaRepository | None = None) -> TrashResult:
    """Trash each file, then remove the trashed ones from the index and thumbnail cache.

    A Live Photo takes its motion clip along. Files outside the library folder are refused.
    A file that is already gone is removed from the index too, since it no longer exists to show.
    """
    repository = repository or MediaRepository()
    result = TrashResult()
    removed_ids: list[int] = []
    for item in items:
        problem = _move(item.path, folder, move_to_trash)
        if problem:
            result.failed.append((item, problem))
            continue
        result.trashed.append(item)
        removed_ids.append(item.id)
        if item.live_video and item.live_video_id is not None:
            problem = _move(Path(item.live_video), folder, move_to_trash)
            if problem:
                result.failed.append((item, f"The photo was moved, but its Live Photo video was not: {problem}"))
            else:
                removed_ids.append(item.live_video_id)

    if removed_ids:
        repository.remove_many(folder, removed_ids)
        for item in result.trashed:
            for cached in thumbnail_cache_files(item.path, item.file_size, item.modified_ns):
                try:
                    cached.unlink(missing_ok=True)
                except OSError:
                    pass
    return result
