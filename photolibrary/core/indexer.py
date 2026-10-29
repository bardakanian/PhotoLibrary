import os
import sqlite3
import threading
import time
from datetime import datetime
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Iterator

from .constants import BATCH_COMMIT_SIZE, METADATA_VERSION, SUPPORTED_EXTENSIONS
from .database import VISIBLE, open_database
from .live_photos import PairingRow, pair_live_photos
from .media import HEIC_ENABLED, media_type_for_path, read_metadata

METADATA_WORKERS = min(8, os.cpu_count() or 4)
PROGRESS_INTERVAL = 0.2  # seconds between progress events


def album_for(root: str, directory: str) -> str:
    """The top-level subfolder a file lives under, or '' for files directly in the root."""
    relative = os.path.relpath(directory, root)
    if relative in {".", ""} or relative.startswith(".."):
        return ""
    return relative.split(os.sep, 1)[0]


def walk_media(root: str, stop_event: threading.Event) -> Iterator[tuple[str, str, os.stat_result]]:
    """Yield (path, directory, stat) for supported files. Skips hidden files and folders."""
    stack = [root]
    while stack:
        if stop_event.is_set():
            return
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            continue
        subfolders = []
        for entry in children:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    subfolders.append(entry.path)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            suffix = os.path.splitext(entry.name)[1].lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                continue
            if suffix in {".heic", ".heif"} and not HEIC_ENABLED:
                continue
            try:
                yield entry.path, directory, entry.stat()
            except OSError:
                continue
        # Reverse so folders are visited in alphabetical order off the stack.
        stack.extend(reversed(subfolders))


class MediaIndexer:
    """Framework-neutral scanner. Events are delivered through a callback."""

    def __init__(self, folder: Path, stop_event: threading.Event, scan_id: int, emit: Callable[[dict], None]):
        self.folder = Path(folder)
        self.folder_root = str(self.folder.resolve())
        self.stop_event = stop_event
        self.scan_id = scan_id
        self.emit = emit

    def send(self, event_type: str, **payload: object) -> None:
        self.emit({"type": event_type, "scan_id": self.scan_id, **payload})

    def pair_live_photos(self, connection: sqlite3.Connection) -> int:
        """Re-pair every Live Photo in the folder (cheap: one query and a dictionary pass)."""
        rows = [
            PairingRow(row_id, path, media_type, datetime.fromisoformat(stamp) if stamp else None,
                       duration, bool(live_motion))
            for row_id, path, media_type, stamp, duration, live_motion in connection.execute("""
                SELECT id, file_path, media_type, capture_ts, duration, live_motion FROM media WHERE folder_root = ?
            """, (self.folder_root,))
        ]
        pairs = pair_live_photos(rows)
        connection.execute("UPDATE media SET live_pair_id = NULL WHERE folder_root = ? AND live_pair_id IS NOT NULL",
                           (self.folder_root,))
        connection.executemany("UPDATE media SET live_pair_id = ? WHERE id = ?",
                               [link for photo, video in pairs for link in ((video, photo), (photo, video))])
        connection.commit()
        return len(pairs)

    def run(self) -> None:
        connection: sqlite3.Connection | None = None
        # Reading metadata (EXIF, mdls, ffmpeg) is the slow part and mostly waits on
        # disk or subprocesses, so it runs on worker threads. SQLite writes stay on this thread.
        executor = ThreadPoolExecutor(max_workers=METADATA_WORKERS, thread_name_prefix="media-metadata")
        try:
            connection = open_database()
            cached = {
                row[0]: (row[1], row[2], row[3], row[4])
                for row in connection.execute("""
                    SELECT file_path, file_size, modified_ns, media_type, metadata_version
                    FROM media WHERE folder_root = ?
                """, (self.folder_root,))
            }
            self.send("scan_started")

            # One quick walk first, so progress can be reported against a real total.
            files = []
            for found in walk_media(self.folder_root, self.stop_event):
                files.append(found)
            if self.stop_event.is_set():
                self.send("scan_cancelled")
                return
            total = len(files)
            self.send("scan_total", total=total)

            seen: set[str] = set()
            processed = updated = unchanged = errors = pending = 0
            in_flight: dict[Future, tuple[str, str, int, int, str]] = {}
            last_progress = 0.0

            def store(future: Future) -> None:
                nonlocal updated, errors, pending
                file_path, directory, file_size, modified_ns, media_type = in_flight.pop(future)
                try:
                    meta = future.result()
                    captured = meta.captured_at
                    date_values = (None, None, None, None) if captured is None else (
                        captured.isoformat(timespec="seconds"), captured.year, captured.month, captured.day,
                    )
                    connection.execute("""
                        INSERT INTO media (
                            folder_root, file_path, file_name, album, file_size, modified_ns,
                            capture_ts, capture_year, capture_month, capture_day, media_type,
                            width, height, camera, duration, metadata_version, live_motion
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(folder_root, file_path) DO UPDATE SET
                            file_name=excluded.file_name, album=excluded.album,
                            file_size=excluded.file_size, modified_ns=excluded.modified_ns,
                            capture_ts=excluded.capture_ts, capture_year=excluded.capture_year,
                            capture_month=excluded.capture_month, capture_day=excluded.capture_day,
                            media_type=excluded.media_type, width=excluded.width, height=excluded.height,
                            camera=excluded.camera, duration=excluded.duration,
                            metadata_version=excluded.metadata_version,
                            live_motion=excluded.live_motion
                    """, (
                        self.folder_root, file_path, os.path.basename(file_path),
                        album_for(self.folder_root, directory), file_size, modified_ns, *date_values,
                        media_type, meta.width, meta.height, meta.camera, meta.duration, METADATA_VERSION,
                        int(meta.live_motion),
                    ))
                except Exception:
                    errors += 1
                    return
                updated += 1
                pending += 1
                if pending >= BATCH_COMMIT_SIZE:
                    connection.commit()
                    pending = 0
                    self.send("batch_committed", updated=updated)

            def drain(return_when: str) -> None:
                done, _ = wait(list(in_flight), return_when=return_when)
                for future in done:
                    store(future)

            def progress(force: bool = False) -> None:
                nonlocal last_progress
                now = time.monotonic()
                if force or now - last_progress >= PROGRESS_INTERVAL:
                    last_progress = now
                    self.send("scan_progress", processed=processed, total=total, updated=updated,
                              unchanged=unchanged, errors=errors)

            for file_path, directory, stat in files:
                if self.stop_event.is_set():
                    self.send("scan_cancelled")
                    return
                processed += 1
                seen.add(file_path)
                media_type = media_type_for_path(Path(file_path))
                old = cached.get(file_path)
                if (old and old[0] == stat.st_size and old[1] == stat.st_mtime_ns
                        and old[2] == media_type and old[3] >= METADATA_VERSION):
                    unchanged += 1
                else:
                    future = executor.submit(read_metadata, Path(file_path), media_type)
                    in_flight[future] = (file_path, directory, stat.st_size, stat.st_mtime_ns, media_type)
                    # Bound the backlog so memory stays flat on huge libraries.
                    if len(in_flight) >= METADATA_WORKERS * 4:
                        drain(FIRST_COMPLETED)
                progress()
            while in_flight:
                if self.stop_event.is_set():
                    self.send("scan_cancelled")
                    return
                drain(FIRST_COMPLETED)
            if pending:
                connection.commit()
                pending = 0
            progress(force=True)

            deleted = 0
            for file_path in set(cached) - seen:
                if self.stop_event.is_set():
                    self.send("scan_cancelled")
                    return
                connection.execute("DELETE FROM media WHERE folder_root=? AND file_path=?",
                                    (self.folder_root, file_path))
                deleted += 1
            connection.commit()
            live_photos = self.pair_live_photos(connection)
            indexed, videos = connection.execute(f"""
                SELECT COUNT(*), COALESCE(SUM(media_type = 'video'), 0) FROM media
                WHERE folder_root = ? AND {VISIBLE}
            """, (self.folder_root,)).fetchone()
            self.send("scan_complete", scanned=processed, updated=updated, unchanged=unchanged,
                      deleted=deleted, errors=errors, total_indexed=indexed, total_videos=videos,
                      live_photos=live_photos)
        except Exception as exc:
            self.send("scan_error", message=str(exc))
        finally:
            # Don't wait for in-progress lookups on cancel; they only read files and finish on their own.
            executor.shutdown(wait=False, cancel_futures=True)
            if connection is not None:
                # Keep rows already indexed before a cancel, so the next scan doesn't redo them.
                try:
                    if connection.in_transaction:
                        connection.commit()
                except sqlite3.Error:
                    pass
                connection.close()
