"""Copy photos and videos out of the library into a folder the user picks.

The originals are copied exactly as they are (HEIC stays HEIC), with their dates kept, all into
that one folder. A name that is already taken there gets a number ("IMG_0001 (2).jpg"), so
nothing is ever overwritten. A Live Photo is exported as its photo only.
"""

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .library_roots import root_for

PROGRESS_INTERVAL = 0.25
CHUNK = 4 << 20  # copied this much at a time, so a stop request is noticed within a moment
NAME_ATTEMPTS = 20  # numbered names tried when another program takes a name first


class ExportStopped(Exception):
    pass


@dataclass
class ExportResult:
    total: int
    copied: int = 0
    renamed: int = 0  # copied under a numbered name, because the name was taken
    failed: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False
    refused: str | None = None  # why the folder can't take an export; nothing was copied


def export_conflict(destination: Path, roots: Sequence[Path | str]) -> str | None:
    """Why `destination` can't take an export, or None if it can. Touches the disk."""
    if root_for(destination, roots) is not None:
        # The copies would be picked up as new photos: every one would show twice.
        return "That folder is inside your library. Choose a folder outside it."
    return None


def free_name(folder: Path, name: str, taken: set[str]) -> str:
    """`name`, or "stem (2).ext", "stem (3).ext"… whichever is free in `folder` and not in `taken`."""
    stem, suffix = os.path.splitext(name)
    candidate, number = name, 1
    while candidate.casefold() in taken or (folder / candidate).exists():
        number += 1
        candidate = f"{stem} ({number}){suffix}"
    return candidate


def copy_file(source: Path, target: Path, stop_event: threading.Event) -> None:
    """Copy contents and dates, a chunk at a time; raises ExportStopped when asked to stop."""
    with open(source, "rb") as reader, open(target, "xb") as writer:
        while block := reader.read(CHUNK):
            if stop_event.is_set():
                raise ExportStopped
            writer.write(block)
    shutil.copystat(source, target)


def export_files(paths: Sequence[Path], destination: Path, stop_event: threading.Event,
                 on_progress: Callable[[int, int], None] | None = None,
                 copy: Callable[[Path, Path, threading.Event], object] = copy_file,
                 roots: Sequence[Path | str] = ()) -> ExportResult:
    """Copy each file into `destination` under a free name. Runs on a background thread.

    Each copy is written under a temporary name and renamed when complete, so a failed or
    stopped export never leaves a half-written file behind. With `roots` (the library folders),
    a destination inside the library is refused before anything is copied.
    """
    destination = Path(destination)
    result = ExportResult(total=len(paths))
    if roots and (problem := export_conflict(destination, roots)):
        result.refused = problem
        return result
    destination.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    last_report = time.monotonic()
    if on_progress:
        on_progress(0, len(paths))
    for done, source in enumerate(paths, 1):
        if stop_event.is_set():
            result.cancelled = True
            break
        source = Path(source)
        name = free_name(destination, source.name, taken)
        taken.add(name.casefold())
        partial = destination / f".{name}.export-part"
        try:
            _remove(partial)  # left by an export that was cut off (a crash, a power cut)
            copy(source, partial, stop_event)
            for _attempt in range(NAME_ATTEMPTS):
                try:
                    _place(partial, destination / name)
                    break
                except FileExistsError:  # another program took the name meanwhile: take the next one
                    name = free_name(destination, source.name, taken)
                    taken.add(name.casefold())
            else:
                raise FileExistsError(17, "No free name was found for it")
            result.copied += 1
            result.renamed += name != source.name
        except ExportStopped:
            result.cancelled = True
            _remove(partial)
            break
        except OSError as exc:
            result.failed.append((source, exc.strerror or str(exc)))
            _remove(partial)
        now = time.monotonic()
        if on_progress and (now - last_report >= PROGRESS_INTERVAL or done == len(paths)):
            last_report = now
            on_progress(done, len(paths))
    return result


def _place(partial: Path, target: Path) -> None:
    """Give the finished copy its name; raises FileExistsError rather than replace a file."""
    if os.name == "nt":
        os.rename(partial, target)  # Windows refuses to rename onto an existing file
        return
    try:
        os.link(partial, target)  # POSIX rename would silently replace; a hard link refuses
    except FileExistsError:
        raise
    except OSError:  # no hard links on this drive (exFAT, some network shares)
        if target.exists():
            raise FileExistsError(17, "File exists", str(target))
        os.rename(partial, target)
        return
    partial.unlink()


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
