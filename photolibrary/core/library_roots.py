"""The library is one or more folders ("roots"). Helpers for adding and matching them."""

import os
from pathlib import Path
from typing import Iterable, Sequence

Folders = Path | str | Sequence[Path | str]


def normalize_roots(folders: Folders) -> list[str]:
    """Resolved folder paths, in order, without duplicates. Accepts one folder or several."""
    if isinstance(folders, (str, os.PathLike)):
        folders = [folders]
    roots: list[str] = []
    for folder in folders:
        root = str(Path(folder).resolve())
        if root not in roots:
            roots.append(root)
    return roots


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def root_conflict(existing: Iterable[Path], candidate: Path) -> str | None:
    """Why `candidate` can't be added to the library, or None if it can.

    Folders may not overlap: a folder inside another library folder would show its files twice.
    """
    new = Path(candidate).resolve()
    for folder in existing:
        current = Path(folder).resolve()
        if new == current:
            return f"“{current.name}” is already in your library."
        if _contains(current, new):
            return f"It is inside “{current.name}”, which is already in your library."
        if _contains(new, current):
            return (f"It contains “{current.name}”, which is already in your library. "
                    f"Remove “{current.name}” first, then add this folder.")
    return None


def root_for(path: Path | str, roots: Iterable[Path | str]) -> Path | None:
    """The library folder that holds `path`, if any."""
    target = Path(path).resolve()
    for root in roots:
        candidate = Path(root).resolve()
        if _contains(candidate, target):
            return candidate
    return None


def display_names(roots: Sequence[Path | str]) -> dict[str, str]:
    """Short names for the sidebar: the folder name, plus its parent when two folders share a name."""
    paths = [Path(root) for root in roots]
    names = {str(path): path.name or str(path) for path in paths}
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    for path in paths:
        if counts[names[str(path)]] > 1:
            names[str(path)] = f"{path.name} ({path.parent.name or path.parent})"
    return names
