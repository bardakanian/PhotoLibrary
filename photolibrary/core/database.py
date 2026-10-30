import sqlite3
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .constants import APP_DIR, DB_FILE, THUMBNAIL_DIR
from .library_roots import Folders, normalize_roots
from .models import Album, AnalyticsResult, MediaItem, YearMediaCount

SCHEMA_VERSION = 1

_ITEM_COLUMNS = """
    id, file_path, album, file_size, modified_ns, capture_ts, media_type,
    width, height, camera, duration
"""


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)


_schema_lock = threading.Lock()
_initialized: set[str] = set()


def open_database() -> sqlite3.Connection:
    """Open (and create if needed) the index. Each thread opens its own connection."""
    ensure_app_dirs()
    connection = sqlite3.connect(DB_FILE, timeout=30)
    connection.execute("PRAGMA synchronous=NORMAL")
    key = str(DB_FILE)
    if key not in _initialized:
        # Schema and WAL setup need an exclusive lock; do it once per process, not per connection.
        with _schema_lock:
            if key not in _initialized:
                _create_schema(connection)
                _initialized.add(key)
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    # WAL lets grid queries on worker threads read while the indexer is writing.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY,
            folder_root TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            album TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            capture_ts TEXT,
            capture_year INTEGER,
            capture_month INTEGER,
            capture_day INTEGER,
            media_type TEXT NOT NULL DEFAULT 'image',
            width INTEGER,
            height INTEGER,
            camera TEXT,
            duration REAL,
            metadata_version INTEGER NOT NULL DEFAULT 0,
            live_motion INTEGER NOT NULL DEFAULT 0,
            live_pair_id INTEGER,
            UNIQUE (folder_root, file_path)
        )
    """)
    # Columns added after the first release; older databases get them here.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(media)")}
    if "live_motion" not in columns:
        connection.execute("ALTER TABLE media ADD COLUMN live_motion INTEGER NOT NULL DEFAULT 0")
    if "live_pair_id" not in columns:
        connection.execute("ALTER TABLE media ADD COLUMN live_pair_id INTEGER")
    # SQLite sorts NULL lowest, so DESC order puts undated items last using this index.
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_media_order
        ON media (folder_root, capture_ts DESC, id DESC)
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_media_album ON media (folder_root, album)")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.commit()


# A Live Photo's motion clip is shown as part of its photo, never as a video of its own.
VISIBLE = "NOT (media_type = 'video' AND live_pair_id IS NOT NULL)"


def _in_roots(roots: list[str]) -> tuple[str, list[str]]:
    """SQL condition matching rows from any of these library folders (none matches nothing)."""
    if not roots:
        return "0", []
    return f"folder_root IN ({','.join('?' * len(roots))})", list(roots)


HiddenAlbums = Iterable[tuple[str, str]]  # (resolved library folder, album name)


def _visible_in(roots: list[str], hidden: HiddenAlbums = ()) -> tuple[str, list[str]]:
    """Like _in_roots, but also leaving out hidden albums."""
    where, params = _in_roots(roots)
    pairs = [(str(root), str(name)) for root, name in hidden or ()]
    if pairs:
        where += " AND NOT (" + " OR ".join("(folder_root = ? AND album = ?)" for _ in pairs) + ")"
        params += [value for pair in pairs for value in pair]
    return where, params


def _attach_live_videos(connection: sqlite3.Connection, roots: list[str],
                        items: list[MediaItem]) -> list[MediaItem]:
    where, params = _in_roots(roots)
    companions = {
        photo_id: (video_id, path) for photo_id, video_id, path in connection.execute(f"""
            SELECT live_pair_id, id, file_path FROM media
            WHERE {where} AND media_type = 'video' AND live_pair_id IS NOT NULL
        """, params)
    }
    if not companions:
        return items
    return [replace(item, live_video_id=companions[item.id][0], live_video=companions[item.id][1])
            if item.id in companions else item for item in items]


def _item_from_row(row: tuple) -> MediaItem:
    item_id, file_path, album, file_size, modified_ns, capture_ts, media_type, width, height, camera, duration = row
    captured_at = None
    if capture_ts:
        try:
            captured_at = datetime.fromisoformat(capture_ts)
        except ValueError:
            captured_at = None
    return MediaItem(item_id, file_path, album or "", file_size, modified_ns, captured_at,
                     media_type or "image", width, height, camera, duration)


class MediaRepository:
    """Read and delete operations on the index. Safe to call from any thread.

    Methods that take `folders` accept one library folder or a list of them.
    """

    def query(self, folders: Folders, kind: str = "all", album: str | None = None, search=None,
              oldest_first: bool = False, hidden: HiddenAlbums = ()) -> list[MediaItem]:
        """Items newest first (or oldest first); undated items always last.

        `search` is a core.search.SearchFilter. Albums in `hidden` are left out, unless that
        album itself is asked for.
        """
        roots = normalize_roots(folders)
        where, root_params = _in_roots(roots) if album is not None else _visible_in(roots, hidden)
        sql = f"SELECT {_ITEM_COLUMNS} FROM media WHERE {where} AND {VISIBLE}"
        params: list[object] = list(root_params)
        if kind == "photos":
            sql += " AND media_type = 'image'"
        elif kind == "videos":
            sql += " AND media_type = 'video'"
        if album is not None:
            sql += " AND album = ?"
            params.append(album)
        if search is not None:
            clause, search_params = search.sql()
            if clause:
                sql += " AND " + clause
                params.extend(search_params)
        if oldest_first:
            # NULLs sort first in ascending order, so push undated items to the end explicitly.
            sql += " ORDER BY capture_ts IS NULL, capture_ts ASC, id ASC"
        else:
            sql += " ORDER BY capture_ts DESC, id DESC"
        connection = open_database()
        try:
            items = [_item_from_row(row) for row in connection.execute(sql, params)]
            return _attach_live_videos(connection, roots, items)
        finally:
            connection.close()

    def get(self, item_id: int) -> MediaItem | None:
        connection = open_database()
        try:
            row = connection.execute(f"SELECT {_ITEM_COLUMNS}, folder_root FROM media WHERE id = ?",
                                     (item_id,)).fetchone()
            if not row:
                return None
            return _attach_live_videos(connection, [row[-1]], [_item_from_row(row[:-1])])[0]
        finally:
            connection.close()

    def cache_entries(self) -> list[tuple[str, int, int]]:
        """(file_path, file_size, modified_ns) for every indexed file in every folder."""
        connection = open_database()
        try:
            return connection.execute("SELECT file_path, file_size, modified_ns FROM media").fetchall()
        finally:
            connection.close()

    def counts(self, folders: Folders, hidden: HiddenAlbums = ()) -> dict[str, int]:
        where, params = _visible_in(normalize_roots(folders), hidden)
        connection = open_database()
        try:
            total, videos = connection.execute(f"""
                SELECT COUNT(*), COALESCE(SUM(media_type = 'video'), 0) FROM media WHERE {where} AND {VISIBLE}
            """, params).fetchone()
            return {"all": int(total), "photos": int(total - videos), "videos": int(videos)}
        finally:
            connection.close()

    def albums(self, folders: Folders) -> list[Album]:
        """Top-level subfolders that contain media, alphabetically, each with its newest item as cover.

        Albums belong to one library folder; two folders may each have an album of the same name.
        """
        roots = normalize_roots(folders)
        where, params = _in_roots(roots)
        connection = open_database()
        try:
            rows = connection.execute(f"""
                SELECT folder_root, album, COUNT(*) FROM media
                WHERE {where} AND album != '' AND {VISIBLE}
                GROUP BY folder_root, album
            """, params).fetchall()
            # Alphabetical; the same name in two folders keeps the folders' library order.
            rows.sort(key=lambda row: (row[1].casefold(), roots.index(row[0])))
            albums = []
            for root, name, count in rows:
                cover = connection.execute(f"""
                    SELECT {_ITEM_COLUMNS} FROM media WHERE folder_root = ? AND album = ? AND {VISIBLE}
                    ORDER BY capture_ts DESC, id DESC LIMIT 1
                """, (root, name)).fetchone()
                albums.append(Album(name, int(count), _item_from_row(cover) if cover else None, root))
            return albums
        finally:
            connection.close()

    def remove_many(self, folders: Folders, item_ids: list[int]) -> int:
        """Remove index rows that belong to these folders. Returns how many rows went away."""
        if not item_ids:
            return 0
        where, root_params = _in_roots(normalize_roots(folders))
        connection = open_database()
        try:
            removed = 0
            # Stay well under SQLite's bound-parameter limit.
            for start in range(0, len(item_ids), 500):
                chunk = list(item_ids[start:start + 500])
                placeholders = ",".join("?" * len(chunk))
                cursor = connection.execute(
                    f"DELETE FROM media WHERE {where} AND id IN ({placeholders})", [*root_params, *chunk]
                )
                removed += cursor.rowcount
            connection.commit()
            return removed
        finally:
            connection.close()

    def remove_folder(self, folder: Path) -> int:
        """Forget a library folder's index (its files are not touched). Returns how many rows went away."""
        connection = open_database()
        try:
            removed = connection.execute("DELETE FROM media WHERE folder_root = ?",
                                         (normalize_roots(folder)[0],)).rowcount
            connection.commit()
            return removed
        finally:
            connection.close()

    def library_analytics(self, folders: Folders, hidden: HiddenAlbums = ()) -> AnalyticsResult:
        """Library Insights for one or more folders, from the index only.

        Ties are deterministic: the earliest calendar day, year or month wins (and the
        alphabetically first camera). Each date-based figure ignores only the rows missing
        the parts it needs, so a row with a month but no year still counts by month.
        """
        where, params = _visible_in(normalize_roots(folders), hidden)
        connection = open_database()
        try:
            combined, videos, undated, oldest, newest, live = connection.execute(f"""
                SELECT COUNT(*), COALESCE(SUM(media_type = 'video'), 0),
                       COALESCE(SUM(capture_ts IS NULL), 0), MIN(capture_ts), MAX(capture_ts),
                       COALESCE(SUM(media_type = 'image' AND live_pair_id IS NOT NULL), 0)
                FROM media WHERE {where} AND {VISIBLE}
            """, params).fetchone()
            # Storage counts every file, Live Photo motion clips included.
            total_bytes = connection.execute(
                f"SELECT COALESCE(SUM(file_size), 0) FROM media WHERE {where}", params).fetchone()[0]

            busiest_row = connection.execute(f"""
                SELECT capture_month, capture_day, COUNT(*) AS media_count
                FROM media
                WHERE {where} AND capture_month IS NOT NULL AND capture_day IS NOT NULL AND {VISIBLE}
                GROUP BY capture_month, capture_day
                ORDER BY media_count DESC, capture_month ASC, capture_day ASC
                LIMIT 1
            """, params).fetchone()

            year_rows = connection.execute(f"""
                SELECT capture_year,
                       SUM(CASE WHEN media_type = 'video' THEN 0 ELSE 1 END),
                       SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END)
                FROM media
                WHERE {where} AND capture_year IS NOT NULL AND {VISIBLE}
                GROUP BY capture_year ORDER BY capture_year ASC
            """, params).fetchall()
            media_by_year = tuple(YearMediaCount(int(year), int(photos), int(year_videos))
                                  for year, photos, year_videos in year_rows)
            largest = min(media_by_year, key=lambda item: (-item.total, item.year), default=None)

            month_row = connection.execute(f"""
                SELECT capture_month, COUNT(*) AS media_count
                FROM media
                WHERE {where} AND capture_month IS NOT NULL AND {VISIBLE}
                GROUP BY capture_month ORDER BY media_count DESC, capture_month ASC
                LIMIT 1
            """, params).fetchone()

            camera_rows = connection.execute(f"""
                SELECT camera, COUNT(*) AS media_count
                FROM media
                WHERE {where} AND camera IS NOT NULL AND camera != '' AND {VISIBLE}
                GROUP BY camera ORDER BY media_count DESC, camera ASC
            """, params).fetchall()

            def parse(value):
                try:
                    return datetime.fromisoformat(value) if value else None
                except ValueError:
                    return None

            return AnalyticsResult(
                total_photos=int(combined - videos),
                total_videos=int(videos),
                combined_total=int(combined),
                busiest_day=(int(busiest_row[0]), int(busiest_row[1])) if busiest_row else None,
                busiest_day_count=int(busiest_row[2]) if busiest_row else 0,
                largest_year=largest.year if largest else None,
                largest_year_count=largest.total if largest else 0,
                media_by_year=media_by_year,
                most_active_month=int(month_row[0]) if month_row else None,
                most_active_month_count=int(month_row[1]) if month_row else 0,
                total_bytes=int(total_bytes),
                oldest=parse(oldest),
                newest=parse(newest),
                undated_count=int(undated),
                top_camera=camera_rows[0][0] if camera_rows else None,
                top_camera_count=int(camera_rows[0][1]) if camera_rows else 0,
                camera_count=len(camera_rows),
                live_photos=int(live),
            )
        finally:
            connection.close()
