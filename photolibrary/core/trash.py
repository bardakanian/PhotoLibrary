"""Move media to the system Trash and drop it from the index. Runs on a worker thread.

The actual "move to Trash" call is passed in (the UI uses QFile.moveToTrash), so this
module stays free of Qt and can be tested with a fake.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .database import MediaRepository
from .library_roots import Folders, normalize_roots, root_for
from .media import thumbnail_cache_files
from .models import MediaItem


@dataclass
class TrashResult:
    trashed: list[MediaItem] = field(default_factory=list)
    failed: list[tuple[MediaItem, str]] = field(default_factory=list)

    @property
    def trashed_ids(self) -> set[int]:
        return {item.id for item in self.trashed}


def _inside(path: Path, roots: list[str]) -> bool:
    try:
        return root_for(path, roots) is not None
    except OSError:
        return False


def _move(path: Path, roots: list[str], move_to_trash: Callable[[Path], bool]) -> str | None:
    """Trash one file. Returns None on success (or if it is already gone), else the reason."""
    if not _inside(path, roots):
        return "It is outside the library folders."
    if not path.exists():
        return None
    try:
        moved = move_to_trash(path)
    except OSError as exc:
        return str(exc)
    return None if moved else "The system could not move it to the Trash. Check that you can change this folder."


def trash_items(folders: Folders, items: Sequence[MediaItem], move_to_trash: Callable[[Path], bool],
                repository: MediaRepository | None = None) -> TrashResult:
    """Trash each file, then remove the trashed ones from the index and thumbnail cache.

    A Live Photo takes its motion clip along. Files outside the library folders are refused.
    A file that is already gone is removed from the index too, since it no longer exists to show.
    """
    repository = repository or MediaRepository()
    roots = normalize_roots(folders)
    result = TrashResult()
    removed_ids: list[int] = []
    for item in items:
        problem = _move(item.path, roots, move_to_trash)
        if problem:
            result.failed.append((item, problem))
            continue
        result.trashed.append(item)
        removed_ids.append(item.id)
        if item.live_video and item.live_video_id is not None:
            problem = _move(Path(item.live_video), roots, move_to_trash)
            if problem:
                result.failed.append((item, f"The photo was moved, but its Live Photo video was not: {problem}"))
            else:
                removed_ids.append(item.live_video_id)

    if removed_ids:
        repository.remove_many(roots, removed_ids)
        for item in result.trashed:
            for cached in thumbnail_cache_files(item.path, item.file_size, item.modified_ns):
                try:
                    cached.unlink(missing_ok=True)
                except OSError:
                    pass
    return result
