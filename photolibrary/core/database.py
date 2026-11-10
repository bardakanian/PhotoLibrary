import base64
import json
import logging
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .constants import APP_DIR, DB_FILE, THUMBNAIL_DIR
from .library_roots import Folders, normalize_roots
from .models import (
    Album, AnalyticsResult, FaceSuggestion, MediaItem, PeopleGroup, Person, PhotoFace, YearMediaCount,
)
from .search import fold

LOGGER = logging.getLogger(__name__)
PEOPLE_BACKUP = "people.json"  # in APP_DIR: names and reference faces, kept apart from the database

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
    connection.create_function("fold", 1, fold, deterministic=True)  # used by search filters
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
    # People (core/faces.py, core/people.py). A face's box is stored as fractions of the photo.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY,
            media_id INTEGER NOT NULL,
            x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
            score REAL NOT NULL,
            embedding BLOB NOT NULL,
            person_id INTEGER,
            pinned INTEGER NOT NULL DEFAULT 0  -- set by the user; regrouping leaves it alone
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_faces_media ON faces (media_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_faces_person ON faces (person_id)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            hidden INTEGER NOT NULL DEFAULT 0,
            reference BLOB  -- a named person's average face, kept when their faces are gone
        )
    """)
    if "reference" not in {row[1] for row in connection.execute("PRAGMA table_info(people)")}:
        connection.execute("ALTER TABLE people ADD COLUMN reference BLOB")
    # Which photos were analysed, as of which file version and face engine.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS face_scans (
            media_id INTEGER PRIMARY KEY,
            modified_ns INTEGER NOT NULL,
            engine_version INTEGER NOT NULL
        )
    """)
    # Groups of people ("Family"): a group shows the photos in which all its members appear.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS person_groups (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS person_group_members (
            group_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            PRIMARY KEY (group_id, person_id)
        )
    """)
    # "This face is not that person", from the user: regrouping and suggestions respect it.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS face_rejections (
            face_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            PRIMARY KEY (face_id, person_id)
        )
    """)
    connection.execute("""
        CREATE TRIGGER IF NOT EXISTS faces_deleted_rejections AFTER DELETE ON faces BEGIN
            DELETE FROM face_rejections WHERE face_id = OLD.id;
        END
    """)
    # However a row leaves the index (trash, rescan, removing a folder), its faces go with it.
    connection.execute("""
        CREATE TRIGGER IF NOT EXISTS media_deleted_faces AFTER DELETE ON media BEGIN
            DELETE FROM faces WHERE media_id = OLD.id;
            DELETE FROM face_scans WHERE media_id = OLD.id;
        END
    """)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.commit()


# Photos in which every member of a group (bound twice: ?, ?) has a face. A group with no members has none.
_TOGETHER = """
    SELECT faces.media_id FROM faces
    WHERE faces.person_id IN (SELECT person_id FROM person_group_members WHERE group_id = ?)
    GROUP BY faces.media_id
    HAVING COUNT(DISTINCT faces.person_id) = (SELECT COUNT(*) FROM person_group_members WHERE group_id = ?)
"""

# People kept (with their reference face) while they have no faces: named and hidden people, and
# group members, so a regroup doesn't silently drop someone from a group or lose a hidden person.
_KEPT_PEOPLE = "(people.name != '' OR people.hidden = 1 OR people.id IN (SELECT person_id FROM person_group_members))"

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


def _same_face(box, old_faces):
    """The old face (row: id, x, y, w, h, …) at the same place as `box`, if they overlap enough."""
    best, best_overlap = None, 0.5  # intersection over union
    for row in old_faces:
        x, y, w, h = row[1:5]
        across = max(0.0, min(box[0] + box[2], x + w) - max(box[0], x))
        down = max(0.0, min(box[1] + box[3], y + h) - max(box[1], y))
        shared = across * down
        overlap = shared / (box[2] * box[3] + w * h - shared) if shared else 0.0
        if overlap >= best_overlap:
            best, best_overlap = row, overlap
    return best


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
              oldest_first: bool = False, hidden: HiddenAlbums = (), person: int | None = None,
              least_typical_first: bool = False, group: int | None = None) -> list[MediaItem]:
        """Items newest first (or oldest first); undated items always last.

        `search` is a core.search.SearchFilter. Albums in `hidden` are left out, unless that
        album itself is asked for. `person` keeps only photos with that person's face; with
        `least_typical_first`, those whose face is least like the person's other faces come first
        (where wrong matches gather).
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
        if group is not None:
            sql += f" AND id IN ({_TOGETHER})"
            params += [group, group]
        if person is not None:
            # "id IN (…)" builds the person's photo list once. An EXISTS per photo made SQLite walk all
            # of the person's faces for every photo: 29 s for someone with 6,000 faces.
            sql += " AND id IN (SELECT media_id FROM faces WHERE person_id = ?)"
            params.append(person)
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
            if person is not None and least_typical_first:
                typicality = self._typicality(connection, person)
                items.sort(key=lambda item: typicality.get(item.id, 1.0))  # stable: ties keep date order
            return _attach_live_videos(connection, roots, items)
        finally:
            connection.close()

    @staticmethod
    def _typicality(connection: sqlite3.Connection, person: int) -> dict[int, float]:
        """Photo id -> how like the person's average face their face in it is (the least alike, if several)."""
        import numpy as np

        rows = connection.execute("SELECT media_id, embedding FROM faces WHERE person_id = ?", (person,)).fetchall()
        if not rows:
            return {}
        vectors = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(len(rows), -1)
        centre = vectors.mean(axis=0)
        centre /= np.linalg.norm(centre) or 1.0
        found: dict[int, float] = {}
        for (media_id, _embedding), similarity in zip(rows, (vectors @ centre).tolist()):
            found[media_id] = min(similarity, found.get(media_id, similarity))
        return found

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

    def unpair_live(self, video_ids: list[int]) -> None:
        """Show these motion clips as ordinary videos (their photo is gone)."""
        if not video_ids:
            return
        connection = open_database()
        try:
            connection.executemany("UPDATE media SET live_pair_id = NULL WHERE id = ?",
                                   [(video_id,) for video_id in video_ids])
            connection.commit()
        finally:
            connection.close()

    # People ----------------------------------------------------------------

    def photos_to_analyze(self, folders: Folders, engine_version: int) -> list[MediaItem]:
        """Photos (newest first) not yet analysed for faces, or changed since, or analysed by an older engine."""
        where, params = _in_roots(normalize_roots(folders))
        connection = open_database()
        try:
            rows = connection.execute(f"""
                SELECT {_ITEM_COLUMNS} FROM media
                WHERE {where} AND {VISIBLE} AND media_type = 'image'
                  AND NOT EXISTS (SELECT 1 FROM face_scans WHERE face_scans.media_id = media.id
                                  AND face_scans.modified_ns = media.modified_ns AND face_scans.engine_version = ?)
                ORDER BY capture_ts DESC, id DESC
            """, [*params, engine_version]).fetchall()
            return [_item_from_row(row) for row in rows]
        finally:
            connection.close()

    def save_faces(self, results: Iterable[tuple[MediaItem, list]], engine_version: int) -> None:
        """Store each photo's faces (core.faces.Face), replacing any found in an older version of it."""
        connection = open_database()
        try:
            for item, faces in results:
                if connection.execute("SELECT 1 FROM media WHERE id = ?", (item.id,)).fetchone() is None:
                    continue  # trashed or rescanned away meanwhile
                # The photo may have been analysed before (edited, or new face models): faces in the
                # same place keep their person, whether the user confirmed it, and "not this person".
                old = connection.execute("SELECT id, x, y, w, h, person_id, pinned FROM faces WHERE media_id = ?",
                                         (item.id,)).fetchall()
                old_rejections = self._rejections(connection, [row[0] for row in old]) if old else {}
                connection.execute("DELETE FROM faces WHERE media_id = ?", (item.id,))
                for face in faces:
                    match = _same_face((face.x, face.y, face.w, face.h), old)
                    person, pinned = (match[5], match[6]) if match else (None, 0)
                    face_id = connection.execute(
                        "INSERT INTO faces (media_id, x, y, w, h, score, embedding, person_id, pinned) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (item.id, face.x, face.y, face.w, face.h, face.score, face.embedding, person, pinned)).lastrowid
                    if match:
                        connection.executemany("INSERT OR IGNORE INTO face_rejections (face_id, person_id) VALUES (?, ?)",
                                               [(face_id, rejected) for rejected in old_rejections.get(match[0], ())])
                connection.execute("INSERT OR REPLACE INTO face_scans (media_id, modified_ns, engine_version) "
                                   "VALUES (?, ?, ?)", (item.id, item.modified_ns, engine_version))
            connection.commit()
        finally:
            connection.close()

    def face_fingerprints(self) -> tuple[list[int], list[bytes], list[int | None]]:
        """(face ids, embeddings, current person ids) of every face regrouping may move (not pinned)."""
        connection = open_database()
        try:
            rows = connection.execute("SELECT id, embedding, person_id FROM faces WHERE pinned = 0 ORDER BY id").fetchall()
        finally:
            connection.close()
        return [row[0] for row in rows], [row[1] for row in rows], [row[2] for row in rows]

    def apply_grouping(self, face_ids: list[int], labels: list[int], assign, vectors=None) -> None:
        """Give each face its group's person (creating new people), and ungroup faces in no group.

        `assign(labels, current_people)` (core.people.assign_people) decides which existing person
        each group continues. It runs inside the same locked transaction as the writes, on the
        faces' people as they are now, so a merge or removal made while the groups were being
        computed is respected instead of overwritten. Unnamed people left without faces are
        deleted; named ones are kept for when faces return. With `vectors` (the faces' fingerprints,
        in `face_ids` order), a group no one continues rejoins a named person whose stored
        reference face it matches: names come back after files move or are analysed again.
        """
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current: dict[int, tuple[int | None, int]] = {}
            for start in range(0, len(face_ids), 500):
                chunk = face_ids[start:start + 500]
                current.update((face_id, (person, pinned)) for face_id, person, pinned in connection.execute(
                    f"SELECT id, person_id, pinned FROM faces WHERE id IN ({','.join('?' * len(chunk))})", chunk))
            # Faces deleted or pinned meanwhile are left out.
            kept = [(face_id, label) for face_id, label in zip(face_ids, labels)
                    if face_id in current and not current[face_id][1]]
            continues = assign([label for _, label in kept], [current[face_id][0] for face_id, _ in kept])
            if vectors is not None:
                continues.update(self._rejoin_named(connection, face_ids, vectors, kept, continues))
            rejected = self._rejections(connection, [face_id for face_id, _ in kept])
            person_for = {}
            for label, person in continues.items():
                if person is not None and connection.execute("SELECT 1 FROM people WHERE id = ?", (person,)).fetchone():
                    person_for[label] = person
                else:
                    person_for[label] = connection.execute("INSERT INTO people DEFAULT VALUES").lastrowid
            updates = []
            for face_id, label in kept:
                person = person_for.get(label)
                # A face the user said isn't this person stays out of it, whatever the grouping says.
                updates.append((None if person in rejected.get(face_id, ()) else person, face_id))
            connection.executemany("UPDATE faces SET person_id = ? WHERE id = ?", updates)
            connection.execute(f"""
                DELETE FROM people WHERE NOT {_KEPT_PEOPLE}
                  AND NOT EXISTS (SELECT 1 FROM faces WHERE faces.person_id = people.id)
            """)
            connection.execute("DELETE FROM face_rejections WHERE person_id NOT IN (SELECT id FROM people)")
            connection.execute("DELETE FROM person_group_members WHERE person_id NOT IN (SELECT id FROM people)")
            self._commit_people(connection)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _rejoin_named(connection: sqlite3.Connection, face_ids, vectors, kept, continues) -> dict:
        """{label: named person} for unclaimed groups that match a named person's reference face."""
        import numpy as np

        from .people import match_references

        taken = {person for person in continues.values() if person is not None}
        references = {person: np.frombuffer(blob, dtype=np.float32)
                      for person, blob in connection.execute(
                          f"SELECT id, reference FROM people WHERE {_KEPT_PEOPLE} AND reference IS NOT NULL")
                      if person not in taken}
        open_labels = {label for label, person in continues.items() if person is None}
        if not references or not open_labels:
            return {}
        position = {face_id: index for index, face_id in enumerate(face_ids)}
        members: dict[int, list[int]] = {}
        for face_id, label in kept:
            if label in open_labels:
                members.setdefault(label, []).append(position[face_id])
        groups = {}
        for label, rows in members.items():
            centre = np.asarray(vectors)[rows].mean(axis=0)
            groups[label] = centre / (np.linalg.norm(centre) or 1.0)
        return match_references(groups, references)

    # Keeping names safe ---------------------------------------------------------

    def _commit_people(self, connection: sqlite3.Connection) -> None:
        """Commit a change to people, after updating named people's reference faces; then back up names."""
        self._refresh_references(connection)
        connection.commit()
        self.write_people_backup()

    @staticmethod
    def _refresh_references(connection: sqlite3.Connection) -> None:
        """Store each named (or hidden) person's average face. Kept as it was while they have no faces."""
        try:
            import numpy as np
        except ImportError:  # People needs OpenCV, which brings numpy; without it there's nothing to do
            return
        found: dict[int, list[bytes]] = {}
        for person, blob in connection.execute(f"""
                SELECT faces.person_id, faces.embedding FROM faces JOIN people ON people.id = faces.person_id
                WHERE {_KEPT_PEOPLE}"""):
            found.setdefault(person, []).append(blob)
        for person, blobs in found.items():
            centre = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), -1).mean(axis=0)
            centre = (centre / (np.linalg.norm(centre) or 1.0)).astype(np.float32)
            connection.execute("UPDATE people SET reference = ? WHERE id = ?", (centre.tobytes(), person))

    def write_people_backup(self) -> None:
        """Write named and hidden people (with reference faces) to people.json, beside the database."""
        connection = open_database()
        try:
            rows = connection.execute(
                f"SELECT name, hidden, reference FROM people WHERE {_KEPT_PEOPLE} ORDER BY id").fetchall()
            groups = []
            for group_id, group_name in connection.execute("SELECT id, name FROM person_groups ORDER BY id").fetchall():
                found = connection.execute("""
                    SELECT people.name, people.reference FROM person_group_members
                    JOIN people ON people.id = person_group_members.person_id
                    WHERE person_group_members.group_id = ? ORDER BY people.name, people.id""", (group_id,)).fetchall()
                # By name (ids change after a rebuild); unnamed members by their reference face.
                groups.append({"name": group_name, "members": [name for name, _ in found if name],
                               "unnamed_members": [base64.b64encode(reference).decode()
                                                   for name, reference in found if not name and reference]})
        finally:
            connection.close()
        backup = {"version": 1, "people": [
            {"name": name, "hidden": bool(hidden),
             "reference": base64.b64encode(reference).decode() if reference else None}
            for name, hidden, reference in rows], "groups": groups}
        target = APP_DIR / PEOPLE_BACKUP
        try:
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(backup, indent=1), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            LOGGER.warning("Could not back up people's names to %s", target, exc_info=True)

    def restore_people_backup(self) -> int:
        """Bring back named people missing from the database (say, after it was rebuilt). Returns how many.

        They return without faces; regrouping gives them back their faces by reference face.
        """
        source = APP_DIR / PEOPLE_BACKUP
        try:
            backup = json.loads(source.read_text(encoding="utf-8"))
            entries = [entry for entry in backup.get("people", []) if isinstance(entry, dict)]
            group_entries = [entry for entry in backup.get("groups", []) if isinstance(entry, dict)]
            grouped_references = {text for entry in group_entries for text in entry.get("unnamed_members") or ()
                                  if isinstance(text, str)}
        except (OSError, ValueError, AttributeError):
            return 0
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            known = {row[0] for row in connection.execute("SELECT name FROM people WHERE name != ''")}
            references = {row[0] for row in connection.execute("SELECT reference FROM people WHERE reference IS NOT NULL")}
            restored = 0
            for entry in entries:
                name = entry.get("name")
                try:
                    reference = base64.b64decode(entry["reference"]) if entry.get("reference") else None
                except (ValueError, TypeError):
                    reference = None
                if not isinstance(name, str) or name in known:
                    continue
                if not name:
                    # A hidden person or group member without a name is recognised by their reference face.
                    wanted = entry.get("hidden") or entry.get("reference") in grouped_references
                    if not (wanted and reference) or reference in references:
                        continue
                connection.execute("INSERT INTO people (name, hidden, reference) VALUES (?, ?, ?)",
                                   (name, int(bool(entry.get("hidden"))), reference))
                if name:
                    known.add(name)
                if reference:
                    references.add(reference)
                restored += 1
            # Groups come back with whichever of their (named) members exist.
            groups = {row[0] for row in connection.execute("SELECT name FROM person_groups")}
            ids = {name: person for person, name in connection.execute("SELECT id, name FROM people WHERE name != ''")}
            by_reference = {base64.b64encode(reference).decode(): person for person, reference in connection.execute(
                "SELECT id, reference FROM people WHERE name = '' AND reference IS NOT NULL")}
            for entry in group_entries:
                name, members = entry.get("name"), entry.get("members")
                if not isinstance(name, str) or not name or name in groups or not isinstance(members, list):
                    continue
                group_id = connection.execute("INSERT INTO person_groups (name) VALUES (?)", (name,)).lastrowid
                chosen = [ids[member] for member in members if member in ids]
                chosen += [by_reference[text] for text in entry.get("unnamed_members") or () if text in by_reference]
                connection.executemany("INSERT OR IGNORE INTO person_group_members (group_id, person_id) VALUES (?, ?)",
                                       [(group_id, person) for person in chosen])
                groups.add(name)
                restored += 1
            connection.commit()
            if restored:
                LOGGER.info("Restored %d named people from %s", restored, source)
            return restored
        finally:
            connection.close()

    # Groups of people -------------------------------------------------------

    def person_groups(self, folders: Folders, hidden: HiddenAlbums = ()) -> list[PeopleGroup]:
        """Every group, alphabetically, with how many shown photos have all its members together."""
        where, params = _visible_in(normalize_roots(folders), hidden)
        connection = open_database()
        try:
            found = []
            for group_id, name in connection.execute("SELECT id, name FROM person_groups").fetchall():
                members = tuple(row[0] for row in connection.execute(
                    "SELECT person_id FROM person_group_members WHERE group_id = ? ORDER BY person_id", (group_id,)))
                together = f"FROM media WHERE {where} AND {VISIBLE} AND id IN ({_TOGETHER})"
                count = connection.execute(f"SELECT COUNT(*) {together}", [*params, group_id, group_id]).fetchone()[0]
                cover = connection.execute(f"SELECT {_ITEM_COLUMNS} {together} ORDER BY capture_ts DESC, id DESC LIMIT 1",
                                           [*params, group_id, group_id]).fetchone()
                found.append(PeopleGroup(group_id, name, members, int(count), _item_from_row(cover) if cover else None))
            found.sort(key=lambda group: (group.name.casefold(), group.id))
            return found
        finally:
            connection.close()

    def create_group(self, name: str, members: Iterable[int]) -> int:
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            group_id = connection.execute("INSERT INTO person_groups (name) VALUES (?)", (name.strip(),)).lastrowid
            self._set_members(connection, group_id, members)
            self._commit_people(connection)
            return group_id
        finally:
            connection.close()

    def update_group(self, group_id: int, name: str | None = None, members: Iterable[int] | None = None) -> None:
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if name is not None:
                connection.execute("UPDATE person_groups SET name = ? WHERE id = ?", (name.strip(), group_id))
            if members is not None:
                connection.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
                self._set_members(connection, group_id, members)
            self._commit_people(connection)
        finally:
            connection.close()

    def add_to_group(self, group_id: int, person_id: int) -> None:
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._set_members(connection, group_id, [person_id])
            self._commit_people(connection)
        finally:
            connection.close()

    def delete_group(self, group_id: int) -> None:
        """Forget the group. Its people and their photos are not touched."""
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
            connection.execute("DELETE FROM person_groups WHERE id = ?", (group_id,))
            self._commit_people(connection)
        finally:
            connection.close()

    @staticmethod
    def _set_members(connection: sqlite3.Connection, group_id: int, members: Iterable[int]) -> None:
        connection.executemany(
            "INSERT OR IGNORE INTO person_group_members (group_id, person_id) "
            "SELECT ?, id FROM people WHERE id = ?", [(group_id, person) for person in members])

    def people(self, folders: Folders, hidden: HiddenAlbums = ()) -> list[Person]:
        """Everyone with at least one shown photo in these folders, most photos first."""
        where, params = _visible_in(normalize_roots(folders), hidden)
        connection = open_database()
        try:
            rows = connection.execute(f"""
                SELECT people.id, people.name, people.hidden, COUNT(DISTINCT media.id)
                FROM people JOIN faces ON faces.person_id = people.id JOIN media ON media.id = faces.media_id
                WHERE {where} AND {VISIBLE}
                GROUP BY people.id
                ORDER BY people.name = '', COUNT(DISTINCT media.id) DESC, people.id
            """, params).fetchall()
            found = []
            for person_id, name, hidden_flag, count in rows:
                # The cover is their largest, most confident face.
                cover = connection.execute(f"""
                    SELECT faces.x, faces.y, faces.w, faces.h, {_ITEM_COLUMNS.replace("id,", "media.id,", 1)}
                    FROM faces JOIN media ON media.id = faces.media_id
                    WHERE faces.person_id = ? AND {where} AND {VISIBLE}
                    ORDER BY faces.w * faces.h * faces.score DESC LIMIT 1
                """, [person_id, *params]).fetchone()
                found.append(Person(person_id, name, int(count), _item_from_row(cover[4:]) if cover else None,
                                    tuple(cover[:4]) if cover else None, bool(hidden_flag)))
            return found
        finally:
            connection.close()

    def rename_person(self, person_id: int, name: str) -> None:
        self._change_people("UPDATE people SET name = ? WHERE id = ?", (name.strip(), person_id))

    def set_person_hidden(self, person_id: int, hidden: bool) -> None:
        self._change_people("UPDATE people SET hidden = ? WHERE id = ?", (int(hidden), person_id))

    def _change_people(self, sql: str, params: tuple) -> None:
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(sql, params)
            self._commit_people(connection)
        finally:
            connection.close()

    def merge_people(self, keep: int, other: int) -> None:
        """Move `other`'s faces to `keep`. If only `other` has a name, `keep` takes it."""
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")  # never interleaved with a regroup (apply_grouping)
            names = dict(connection.execute("SELECT id, name FROM people WHERE id IN (?, ?)", (keep, other)))
            if not names.get(keep) and names.get(other):
                connection.execute("UPDATE people SET name = ? WHERE id = ?", (names[other], keep))
            # Locked in place: regrouping would otherwise split them apart again.
            connection.execute("UPDATE faces SET person_id = ?, pinned = 1 WHERE person_id IN (?, ?)", (keep, keep, other))
            connection.execute("UPDATE OR IGNORE face_rejections SET person_id = ? WHERE person_id = ?", (keep, other))
            connection.execute("DELETE FROM face_rejections WHERE person_id = ?", (other,))
            connection.execute("UPDATE OR IGNORE person_group_members SET person_id = ? WHERE person_id = ?", (keep, other))
            connection.execute("DELETE FROM person_group_members WHERE person_id = ?", (other,))
            connection.execute("DELETE FROM people WHERE id = ?", (other,))
            self._commit_people(connection)
        finally:
            connection.close()

    def detach_faces(self, person_id: int, media_ids: list[int]) -> int:
        """Take these photos' faces away from a person, for good: regrouping won't put them back.

        The faces are free to join someone else; they only remember they aren't this person.
        """
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            face_ids = []
            for start in range(0, len(media_ids), 500):
                chunk = list(media_ids[start:start + 500])
                face_ids += [row[0] for row in connection.execute(
                    f"SELECT id FROM faces WHERE person_id = ? AND media_id IN ({','.join('?' * len(chunk))})",
                    [person_id, *chunk])]
            for face_id in face_ids:
                self._reject(connection, face_id, person_id)
            self._commit_people(connection)
            return len(face_ids)
        finally:
            connection.close()

    # Single faces (the viewer's face boxes and the Review page) --------------

    def faces_in_photo(self, media_id: int) -> list[PhotoFace]:
        connection = open_database()
        try:
            rows = connection.execute("""
                SELECT faces.id, faces.x, faces.y, faces.w, faces.h, faces.person_id, people.name, faces.pinned
                FROM faces LEFT JOIN people ON people.id = faces.person_id
                WHERE faces.media_id = ? ORDER BY faces.x
            """, (media_id,)).fetchall()
            return [PhotoFace(face_id, (x, y, w, h), person, name or "", bool(pinned) and person is not None)
                    for face_id, x, y, w, h, person, name, pinned in rows]
        finally:
            connection.close()

    def assign_face(self, face_id: int, person_id: int) -> None:
        """"This is Ana": the face belongs to this person for good (regrouping leaves it alone)."""
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
                connection.execute("UPDATE faces SET person_id = ?, pinned = 1 WHERE id = ?", (person_id, face_id))
                connection.execute("DELETE FROM face_rejections WHERE face_id = ? AND person_id = ?",
                                   (face_id, person_id))
            self._commit_people(connection)
        finally:
            connection.close()

    def new_person_for_face(self, face_id: int, name: str) -> int:
        """Start a new person from this one face."""
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            person_id = connection.execute("INSERT INTO people (name) VALUES (?)", (name.strip(),)).lastrowid
            connection.execute("UPDATE faces SET person_id = ?, pinned = 1 WHERE id = ?", (person_id, face_id))
            self._commit_people(connection)
            return person_id
        finally:
            connection.close()

    def reject_face(self, face_id: int, person_id: int) -> None:
        """"This isn't Ana": take the face out of Ana if it is there, and never suggest or regroup it into Ana."""
        connection = open_database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._reject(connection, face_id, person_id)
            self._commit_people(connection)
        finally:
            connection.close()

    @staticmethod
    def _reject(connection: sqlite3.Connection, face_id: int, person_id: int) -> None:
        connection.execute("INSERT OR IGNORE INTO face_rejections (face_id, person_id) VALUES (?, ?)",
                           (face_id, person_id))
        # Unpinned again, so regrouping may still find the person it really is.
        connection.execute("UPDATE faces SET person_id = NULL, pinned = 0 WHERE id = ? AND person_id = ?",
                           (face_id, person_id))

    @staticmethod
    def _rejections(connection: sqlite3.Connection, face_ids: list[int]) -> dict[int, set[int]]:
        found: dict[int, set[int]] = {}
        for start in range(0, len(face_ids), 500):
            chunk = face_ids[start:start + 500]
            for face_id, person_id in connection.execute(
                    f"SELECT face_id, person_id FROM face_rejections WHERE face_id IN ({','.join('?' * len(chunk))})",
                    chunk):
                found.setdefault(face_id, set()).add(person_id)
        return found

    def face_suggestions(self, folders: Folders, hidden: HiddenAlbums = ()) -> list[FaceSuggestion]:
        """Faces in no person that look like someone you named (not hidden, not ruled out), most alike first.

        Only named people are suggested: "Is this the same person?" for the many unnamed groups
        (mostly passers-by) was a long list of questions about strangers.
        """
        import numpy as np

        from .people import centroids_of, suggest_people

        where, params = _visible_in(normalize_roots(folders), hidden)
        connection = open_database()
        try:
            known = connection.execute("""
                SELECT faces.person_id, faces.embedding FROM faces JOIN people ON people.id = faces.person_id
                WHERE people.hidden = 0 AND people.name != ''
            """).fetchall()
            loose = connection.execute(f"""
                SELECT faces.id, faces.x, faces.y, faces.w, faces.h, faces.embedding,
                       {_ITEM_COLUMNS.replace("id,", "media.id,", 1)}
                FROM faces JOIN media ON media.id = faces.media_id
                WHERE faces.person_id IS NULL AND {where} AND {VISIBLE}
            """, params).fetchall()
            if not known or not loose:
                return []
            names = dict(connection.execute("SELECT id, name FROM people"))
            rejected = self._rejections(connection, [row[0] for row in loose])
        finally:
            connection.close()
        known_vectors = np.frombuffer(b"".join(row[1] for row in known), dtype=np.float32).reshape(len(known), -1)
        person_ids, centroids = centroids_of(known_vectors, [row[0] for row in known])
        loose_vectors = np.frombuffer(b"".join(row[5] for row in loose), dtype=np.float32).reshape(len(loose), -1)
        return [FaceSuggestion(loose[index][0], _item_from_row(loose[index][6:]), tuple(loose[index][1:5]),
                               person, names.get(person, ""), similarity)
                for index, person, similarity in suggest_people(
                    loose_vectors, centroids, person_ids, [rejected.get(row[0], set()) for row in loose])]

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
