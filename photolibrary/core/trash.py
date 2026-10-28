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


def trash_items(folder: Path, items: Sequence[MediaItem], move_to_trash: Callable[[Path], bool],
                repository: MediaRepository | None = None) -> TrashResult:
    """Trash each file, then remove the trashed ones from the index and thumbnail cache.

    Files outside the library folder are refused. A file that is already gone is
    removed from the index too, since it no longer exists to show.
    """
    repository = repository or MediaRepository()
    result = TrashResult()
    for item in items:
        path = item.path
        if not _inside(path, folder):
            result.failed.append((item, "It is outside the library folder."))
            continue
        if not path.exists():
            result.trashed.append(item)
            continue
        try:
            moved = move_to_trash(path)
        except OSError as exc:
            result.failed.append((item, str(exc)))
            continue
        if moved:
            result.trashed.append(item)
        else:
            result.failed.append((item, "The system could not move it to the Trash. Check that you can change this folder."))

    if result.trashed:
        repository.remove_many(folder, [item.id for item in result.trashed])
        for item in result.trashed:
            for cached in thumbnail_cache_files(item.path, item.file_size, item.modified_ns):
                try:
                    cached.unlink(missing_ok=True)
                except OSError:
                    pass
    return result
