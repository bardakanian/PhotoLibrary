import os
import threading
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

from photolibrary.core import database, faces
from photolibrary.core.faces import ENGINE_VERSION, MODELS, ModelError, analyze_photos, ensure_models
from photolibrary.core.indexer import MediaIndexer
from photolibrary.core.people import assign_people, cluster_faces, regroup
from photolibrary.core.search import parse_query

from .base import TempAppTestCase
from .fake_faces import FakeFaceEngine, person_vector, write_people_photo

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def scan(folder):
    MediaIndexer(folder, threading.Event(), 1, lambda event: None).run()


@unittest.skipUnless(faces.FACES_ENABLED, "OpenCV is not installed")
class ClusterTests(unittest.TestCase):
    def faces_of(self, counts):
        rows, truth = [], []
        rng = np.random.default_rng(3)
        for person, count in counts.items():
            for _ in range(count):
                vector = person_vector(person) + rng.normal(scale=0.03, size=128)
                rows.append(vector / np.linalg.norm(vector))
                truth.append(person)
        return np.array(rows, dtype=np.float32), truth

    def test_each_person_is_one_group_and_small_groups_are_dropped(self):
        vectors, truth = self.faces_of({1: 12, 2: 8, 3: 4})
        labels = cluster_faces(vectors)
        groups = {person: set(labels[[i for i, t in enumerate(truth) if t == person]].tolist()) for person in (1, 2, 3)}
        self.assertEqual(groups[1], {0})  # largest first
        self.assertEqual(groups[2], {1})
        self.assertEqual(groups[3], {-1})  # 4 faces: fewer than MIN_FACES

    def test_no_faces(self):
        self.assertEqual(len(cluster_faces(np.zeros((0, 128), dtype=np.float32))), 0)

    def chained_people(self, seed: int = 5):
        """Three people, plus a few in-between faces that link them at the normal cutoff.

        Faces vary as much as in a real library (about 0.8 to their person's average).
        """
        rng = np.random.default_rng(seed)
        a, b, c = (person_vector(person) for person in (11, 12, 13))
        rows, truth = [], []
        for person, centre in enumerate((a, b, c)):
            for _ in range(30):
                vector = centre + rng.normal(scale=0.066, size=128)
                rows.append(vector / np.linalg.norm(vector))
                truth.append(person)
        for first, second in ((a, b), (b, c)):
            for _ in range(4):
                extra = rng.normal(size=128)
                extra -= (extra @ first) * first + (extra @ second) * second
                extra /= np.linalg.norm(extra)
                weight = np.sqrt(max(0.0, 1 - 2 * 0.62 ** 2 * (1 + first @ second)))
                vector = 0.62 * (first + second) + weight * extra + rng.normal(scale=0.01, size=128)
                rows.append(vector / np.linalg.norm(vector))
                truth.append(-1)
        return np.array(rows, dtype=np.float32), np.array(truth)

    def test_people_chained_together_are_split_apart(self):
        vectors, truth = self.chained_people()
        self.assertEqual(len(set(cluster_faces(vectors, split=False).tolist()) - {-1}), 1)  # chained into one
        labels = cluster_faces(vectors)
        groups = [set(labels[truth == person].tolist()) for person in range(3)]
        self.assertTrue(all(len(group) == 1 and -1 not in group for group in groups), groups)
        self.assertEqual(len(set.union(*groups)), 3)

    def test_threshold_edge(self):
        vectors, _ = self.faces_of({1: 6})
        self.assertEqual(set(cluster_faces(vectors, threshold=0.999).tolist()), {-1})


class AssignTests(unittest.TestCase):
    def test_groups_keep_their_person_and_strays_do_not_steal_a_name(self):
        self.assertEqual(assign_people([0, 0, 0, 0, 1, 1, 1, 1, 1, 1], [7, 7, 7, None, None, None, None, None, 7, 7]),
                         {0: 7, 1: None})  # two stray faces of person 7 don't make group 1 "person 7"
        self.assertEqual(assign_people([0] * 6, [7, 7, 7, None, None, None]), {0: 7})  # grew with new photos
        # A person whose group splits keeps their name on the part holding most of their faces.
        self.assertEqual(assign_people([0, 0, 0, 1, 1, 1, 1], [7] * 7), {0: None, 1: 7})
        self.assertEqual(assign_people([-1, -1], [7, 7]), {})


class ModelDownloadTests(TempAppTestCase):
    def test_a_file_that_does_not_match_its_checksum_is_refused(self):
        def bad_download(url, target):
            Path(target).write_bytes(b"not a model")
        with self.assertRaises(ModelError):
            ensure_models(self.root / "models", bad_download)
        self.assertEqual(list((self.root / "models").iterdir()), [])

    def test_present_models_are_not_downloaded_again(self):
        models = self.root / "models"
        models.mkdir()
        for model in MODELS:
            with open(models / model.name, "wb") as handle:
                handle.truncate(model.size)
        ensure_models(models, lambda url, target: self.fail("downloaded again"))


@unittest.skipUnless(faces.FACES_ENABLED, "OpenCV is not installed")
class PeopleLibraryTests(TempAppTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.make_library()
        start = datetime(2024, 1, 1)
        self.paths = []
        # Person 1 in 8 photos, person 2 in 6 (two of them with person 1), person 3 in 2.
        plan = [(1,)] * 6 + [(1, 2)] * 2 + [(2,)] * 4 + [(3,)] * 2 + [()] * 2
        for index, people in enumerate(plan):
            album = "Trips" if index < 3 else "Home"
            self.paths.append(write_people_photo(self.library / album / f"p{index:02d}.jpg",
                                                 start + timedelta(days=index), people))
        scan(self.library)
        self.repo = database.MediaRepository()
        self.engine = FakeFaceEngine()

    def analyze(self):
        todo = self.repo.photos_to_analyze(self.library, ENGINE_VERSION)
        results = []
        result = analyze_photos(todo, self.engine, threading.Event(), lambda item, found: results.append((item, found)))
        self.repo.save_faces(results, ENGINE_VERSION)
        regroup(self.repo)
        return result

    def people(self, **kwargs):
        return self.repo.people(self.library, **kwargs)

    def test_people_are_found_with_their_photos_and_a_cover(self):
        result = self.analyze()
        self.assertEqual((result.analyzed, result.faces), (16, 16))
        people = self.people()
        self.assertEqual([person.count for person in people], [8, 6])  # person 3 has too few photos
        self.assertTrue(all(person.cover is not None and person.box for person in people))
        first = self.repo.query(self.library, person=people[0].id)
        self.assertEqual(len(first), 8)
        self.assertEqual(len(self.repo.query(self.library, person=people[1].id, search=parse_query("p07"))), 1)

    def test_unchanged_photos_are_not_analysed_again_and_edited_ones_are(self):
        self.analyze()
        calls = self.engine.calls
        self.assertEqual(self.repo.photos_to_analyze(self.library, ENGINE_VERSION), [])
        write_people_photo(self.paths[13], datetime(2024, 1, 14), (2,))  # person 3 -> person 2
        scan(self.library)
        self.assertEqual([item.name for item in self.repo.photos_to_analyze(self.library, ENGINE_VERSION)], ["p13.jpg"])
        self.analyze()
        self.assertEqual(self.engine.calls, calls + 1)
        self.assertEqual([person.count for person in self.people()], [8, 7])

    def test_names_and_merges_survive_regrouping(self):
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(first.id, "Ana")
        regroup(self.repo)
        self.assertEqual([(person.id, person.name) for person in self.people()][0], (first.id, "Ana"))
        self.repo.merge_people(second.id, first.id)  # keep the second; it takes the name
        regroup(self.repo)  # the two groups are still different to the grouping; the merge stays
        people = self.people()
        self.assertEqual([(person.id, person.name, person.count) for person in people], [(second.id, "Ana", 12)])

    def test_removed_photos_stay_out_of_the_person(self):
        self.analyze()
        first = self.people()[0]
        photo = self.repo.query(self.library, person=first.id)[0]
        self.assertEqual(self.repo.detach_faces(first.id, [photo.id]), 1)
        regroup(self.repo)
        self.assertNotIn(photo.id, [item.id for item in self.repo.query(self.library, person=first.id)])

    def test_hidden_people_and_hidden_albums(self):
        self.analyze()
        first = self.people()[0]
        self.repo.set_person_hidden(first.id, True)
        self.assertTrue(self.people()[0].hidden)
        trips = (str(self.library.resolve()), "Trips")
        counts = {person.id: person.count for person in self.people(hidden=[trips])}
        self.assertEqual(counts[first.id], 5)  # three of their photos are in Trips
        self.assertEqual(len(self.repo.query(self.library, person=first.id, hidden=[trips])), 5)

    def test_photos_on_a_missing_drive_are_not_recorded_and_are_tried_again(self):
        hidden_away = self.paths[0].with_suffix(".moved")
        self.paths[0].rename(hidden_away)  # as if its drive were unplugged; the index keeps the row
        result = self.analyze()
        self.assertEqual(result.unreachable, 1)
        self.assertEqual([item.name for item in self.repo.photos_to_analyze(self.library, ENGINE_VERSION)],
                         ["p00.jpg"])
        hidden_away.rename(self.paths[0])
        self.assertEqual(self.analyze().analyzed, 1)

    def test_a_custom_image_loader_is_used(self):
        from photolibrary.core.media import load_display_image
        from photolibrary.core.people import run_people_pass
        loaded = []

        def loader(path, edge):
            loaded.append(path.name)
            return load_display_image(path, edge)

        run_people_pass(self.repo, self.library, lambda: self.engine, threading.Event(), load_image=loader)
        self.assertEqual(len(loaded), 16)

    def test_a_merge_made_while_groups_are_computed_is_kept(self):
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(first.id, "Ana")
        face_ids, embeddings, _people = self.repo.face_fingerprints()  # a regroup starts...
        self.repo.merge_people(first.id, second.id)                   # ...the user merges meanwhile...
        vectors = np.frombuffer(b"".join(embeddings), dtype=np.float32).reshape(len(face_ids), -1)
        self.repo.apply_grouping(face_ids, cluster_faces(vectors).tolist(), assign_people)  # ...and it finishes
        self.assertEqual([(person.id, person.name, person.count) for person in self.people()], [(first.id, "Ana", 12)])

    def loosen(self, person_id: int, count: int) -> list[int]:
        """Take `count` faces out of a person without telling the app why, as grouping might leave them."""
        connection = database.open_database()
        try:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM faces WHERE person_id = ? ORDER BY id LIMIT ?", (person_id, count))]
            connection.executemany("UPDATE faces SET person_id = NULL WHERE id = ?", [(face_id,) for face_id in ids])
            connection.commit()
            return ids
        finally:
            connection.close()

    def test_loose_faces_are_suggested_for_the_person_they_look_like(self):
        self.analyze()
        first, second = self.people()
        loose = self.loosen(first.id, 2)
        self.assertEqual(self.repo.face_suggestions(self.library), [])  # only people you named are suggested
        self.repo.rename_person(first.id, "Ana")
        self.repo.rename_person(second.id, "Ben")
        suggestions = self.repo.face_suggestions(self.library)
        self.assertEqual({(entry.face_id, entry.person_id) for entry in suggestions}, {(face, first.id) for face in loose})
        self.assertTrue(all(entry.similarity > 0.9 and entry.item.name.startswith("p") for entry in suggestions))
        # Person 3 (two photos, never grouped) looks like no one: never suggested.
        self.assertTrue(all(entry.person_id in (first.id, second.id) for entry in suggestions))
        # Hidden people aren't suggested.
        self.repo.set_person_hidden(first.id, True)
        self.assertEqual(self.repo.face_suggestions(self.library), [])

    def test_yes_locks_the_face_in_and_no_keeps_it_out_through_regrouping(self):
        self.analyze()
        first, _second = self.people()
        self.repo.rename_person(first.id, "Ana")
        yes, no = self.loosen(first.id, 2)
        self.repo.assign_face(yes, first.id)
        self.repo.reject_face(no, first.id)
        self.assertEqual(self.repo.face_suggestions(self.library), [])
        regroup(self.repo)  # would normally put both straight back into the person
        faces = {face.id: face for item in self.repo.query(self.library) for face in self.repo.faces_in_photo(item.id)}
        self.assertEqual((faces[yes].person_id, faces[yes].confirmed), (first.id, True))
        self.assertIsNone(faces[no].person_id)

    def test_a_face_can_start_a_new_person_and_photos_list_their_faces(self):
        self.analyze()
        photo = next(item for item in self.repo.query(self.library) if item.name == "p06.jpg")  # persons 1 and 2
        faces = self.repo.faces_in_photo(photo.id)
        self.assertEqual(len(faces), 2)
        self.assertLess(faces[0].box[0], faces[1].box[0])  # left to right
        new_id = self.repo.new_person_for_face(faces[0].id, "  Cleo ")
        regroup(self.repo)
        named = {person.id: person for person in self.people()}
        self.assertEqual((named[new_id].name, named[new_id].count), ("Cleo", 1))
        self.assertEqual(self.repo.faces_in_photo(photo.id)[0].name, "Cleo")

    def test_a_merge_carries_rejections_over(self):
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(second.id, "Ben")
        (face,) = self.loosen(first.id, 1)
        self.repo.reject_face(face, first.id)        # "not person 1"...
        self.repo.merge_people(second.id, first.id)  # ...and person 1 is merged into person 2
        self.assertNotIn(face, [entry.face_id for entry in self.repo.face_suggestions(self.library)])

    def test_a_persons_photos_are_found_without_checking_every_face_per_photo(self):
        """A per-photo EXISTS once took 29 s for someone with 6,000 faces."""
        self.analyze()
        person = self.people()[0].id
        statements = []
        real_open = database.open_database

        def traced():
            connection = real_open()
            connection.set_trace_callback(statements.append)
            return connection

        with unittest.mock.patch.object(database, "open_database", traced):
            self.repo.query(self.library, person=person)
        sql = next(statement for statement in statements if "FROM faces WHERE person_id" in statement)
        connection = real_open()
        try:
            plan = " ".join(row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql))
        finally:
            connection.close()
        self.assertIn("LIST SUBQUERY", plan)
        self.assertNotIn("CORRELATED", plan)

    def test_least_typical_faces_come_first(self):
        self.analyze()
        first = self.people()[0]
        odd = self.repo.query(self.library, person=first.id)[3]
        connection = database.open_database()
        try:  # make one of their faces look a little different
            face_id, blob = connection.execute("SELECT id, embedding FROM faces WHERE media_id = ? AND person_id = ?",
                                               (odd.id, first.id)).fetchone()
            vector = np.frombuffer(blob, dtype=np.float32) + person_vector(40) * 0.8
            connection.execute("UPDATE faces SET embedding = ? WHERE id = ?",
                               ((vector / np.linalg.norm(vector)).astype(np.float32).tobytes(), face_id))
            connection.commit()
        finally:
            connection.close()
        by_date = self.repo.query(self.library, person=first.id)
        checked = self.repo.query(self.library, person=first.id, least_typical_first=True)
        self.assertEqual(checked[0].id, odd.id)
        self.assertEqual(sorted(item.id for item in checked), sorted(item.id for item in by_date))
        self.assertEqual(self.repo.query(self.library, least_typical_first=True)[0].id,
                         self.repo.query(self.library)[0].id)  # no person: ignored

    def named(self):
        return {person.name: person.count for person in self.people() if person.name}

    def test_names_come_back_after_photos_move(self):
        """A moved photo is deleted plus new to the index, so its faces return without a person."""
        self.analyze()
        self.repo.rename_person(self.people()[0].id, "Ana")
        for album in ("Trips", "Home"):  # every photo moves: no face keeps its person
            (self.library / album).rename(self.library / f"{album} moved")
        scan(self.library)
        self.analyze()
        self.assertEqual(self.named(), {"Ana": 8})

    def test_answers_survive_a_photo_being_analysed_again(self):
        self.analyze()
        first, second = self.people()
        photo = next(item for item in self.repo.query(self.library) if item.name == "p06.jpg")  # persons 1 and 2
        left, right = self.repo.faces_in_photo(photo.id)
        self.repo.assign_face(left.id, second.id)   # confirmed: deliberately the other person
        self.repo.reject_face(right.id, second.id)
        results = []
        analyze_photos([photo], self.engine, threading.Event(), lambda item, found: results.append((item, found)))
        self.repo.save_faces(results, ENGINE_VERSION + 1)  # as if the face models changed
        regroup(self.repo)
        left, right = self.repo.faces_in_photo(photo.id)
        self.assertEqual((left.person_id, left.confirmed), (second.id, True))
        self.assertNotEqual(right.person_id, second.id)

    def test_names_are_backed_up_and_restored_if_the_database_is_lost(self):
        import json
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(first.id, "Ana")
        self.repo.rename_person(second.id, "Ben")
        self.repo.set_person_hidden(second.id, True)
        backup = json.loads((self.app_dir / database.PEOPLE_BACKUP).read_text())
        self.assertEqual(sorted((entry["name"], entry["hidden"], bool(entry["reference"])) for entry in backup["people"]),
                         [("Ana", False, True), ("Ben", True, True)])
        # The database is lost: everything is indexed and analysed again from scratch.
        connection = database.open_database()
        try:
            for table in ("media", "faces", "people", "face_scans", "face_rejections"):
                connection.execute(f"DELETE FROM {table}")
            connection.commit()
        finally:
            connection.close()
        scan(self.library)
        from photolibrary.core.people import run_people_pass
        run_people_pass(self.repo, self.library, lambda: self.engine, threading.Event())
        self.assertEqual(self.named(), {"Ana": 8, "Ben": 6})
        self.assertTrue(next(person for person in self.people() if person.name == "Ben").hidden)
        self.assertEqual(self.repo.restore_people_backup(), 0)  # nothing missing now: no duplicates

    def test_a_damaged_backup_is_ignored(self):
        self.app_dir.mkdir(parents=True, exist_ok=True)
        (self.app_dir / database.PEOPLE_BACKUP).write_text("{not json")
        self.assertEqual(self.repo.restore_people_backup(), 0)

    def test_a_group_shows_photos_with_all_its_members_together(self):
        self.analyze()
        first, second = self.people()  # together only in p06 and p07
        group = self.repo.create_group(" Family ", [first.id, second.id])
        (found,) = self.repo.person_groups(self.library)
        self.assertEqual((found.name, found.members, found.count), ("Family", tuple(sorted((first.id, second.id))), 2))
        self.assertEqual(found.cover.name, "p07.jpg")  # the newest photo of them together
        self.assertEqual(sorted(item.name for item in self.repo.query(self.library, group=group)), ["p06.jpg", "p07.jpg"])
        self.assertEqual(len(self.repo.query(self.library, group=group, search=parse_query("p07"))), 1)
        # One member: all their photos. No members: nothing.
        self.repo.update_group(group, name="Just one", members=[first.id])
        self.assertEqual(len(self.repo.query(self.library, group=group)), 8)
        self.repo.update_group(group, members=[])
        self.assertEqual(self.repo.query(self.library, group=group), [])
        self.repo.add_to_group(group, second.id)
        self.assertEqual(self.repo.person_groups(self.library)[0].count, 6)
        self.repo.delete_group(group)
        self.assertEqual(self.repo.person_groups(self.library), [])
        self.assertEqual(len(self.people()), 2)  # people are untouched

    def test_groups_follow_merges_and_survive_a_lost_database(self):
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(first.id, "Ana")
        self.repo.rename_person(second.id, "Ben")
        group = self.repo.create_group("Duo", [first.id, second.id])
        self.repo.merge_people(first.id, second.id)  # Ben is merged into Ana
        self.assertEqual(self.repo.person_groups(self.library)[0].members, (first.id,))
        self.repo.update_group(group, members=[first.id])
        connection = database.open_database()
        try:
            for table in ("media", "faces", "people", "face_scans", "face_rejections", "person_groups",
                          "person_group_members"):
                connection.execute(f"DELETE FROM {table}")
            connection.commit()
        finally:
            connection.close()
        scan(self.library)
        from photolibrary.core.people import run_people_pass
        run_people_pass(self.repo, self.library, lambda: self.engine, threading.Event())
        (restored,) = self.repo.person_groups(self.library)
        ana = next(person for person in self.people() if person.name == "Ana")
        self.assertEqual((restored.name, restored.members), ("Duo", (ana.id,)))

    def wipe_database(self):
        connection = database.open_database()
        try:
            for table in ("media", "faces", "people", "face_scans", "face_rejections", "person_groups",
                          "person_group_members"):
                connection.execute(f"DELETE FROM {table}")
            connection.commit()
        finally:
            connection.close()

    def test_a_group_member_who_loses_every_face_stays_in_the_group(self):
        self.analyze()
        first, second = self.people()
        group = self.repo.create_group("Duo", [first.id, second.id])
        photos = [item.id for item in self.repo.query(self.library, person=second.id)]
        self.repo.detach_faces(second.id, photos)
        regroup(self.repo)
        (found,) = self.repo.person_groups(self.library)
        # Without the unnamed member, the group would quietly show all of the other one's photos.
        self.assertEqual((found.id, found.members, found.count), (group, tuple(sorted((first.id, second.id))), 0))

    def test_unnamed_group_members_and_hidden_people_survive_a_lost_database(self):
        from photolibrary.core.people import run_people_pass
        self.analyze()
        first, second = self.people()
        self.repo.create_group("Duo", [first.id, second.id])
        self.repo.set_person_hidden(first.id, True)
        self.wipe_database()
        scan(self.library)
        run_people_pass(self.repo, self.library, lambda: self.engine, threading.Event())
        people = sorted(self.people(), key=lambda person: -person.count)
        self.assertEqual([(person.name, person.count, person.hidden) for person in people],
                         [("", 8, True), ("", 6, False)])
        (restored,) = self.repo.person_groups(self.library)
        self.assertEqual((restored.name, restored.members, restored.count),
                         ("Duo", tuple(sorted(person.id for person in people)), 2))
        self.assertEqual(self.repo.restore_people_backup(), 0)  # nothing missing now: no duplicates

    def test_a_full_re_analysis_keeps_everything_the_user_set_up(self):
        """New detector settings re-analyse every photo: names, answers and groups must survive."""
        from photolibrary.core.people import run_people_pass
        self.analyze()
        first, second = self.people()
        self.repo.rename_person(first.id, "Ana")
        self.repo.rename_person(second.id, "Ben")
        photo = next(item for item in self.repo.query(self.library) if item.name == "p06.jpg")  # Ana and Ben
        left, right = self.repo.faces_in_photo(photo.id)
        self.repo.assign_face(left.id, first.id)
        self.repo.reject_face(right.id, second.id)
        group = self.repo.create_group("Duo", [first.id, second.id])
        before = self.named()
        result, _groups = run_people_pass(self.repo, self.library, lambda: self.engine, threading.Event(),
                                          engine_version=ENGINE_VERSION + 1)
        self.assertEqual(result.analyzed, 16)  # every photo, again
        self.assertEqual(before, {"Ana": 8, "Ben": 5})  # Ben's rejected face is out
        self.assertEqual(self.named(), before)  # ...and stays out after re-analysis
        left, right = self.repo.faces_in_photo(photo.id)
        self.assertEqual((left.person_id, left.confirmed), (first.id, True))
        self.assertNotEqual(right.person_id, second.id)
        self.assertEqual(self.repo.person_groups(self.library)[0].members, tuple(sorted((first.id, second.id))))
        self.assertEqual(len(self.repo.query(self.library, group=group)), 1)  # p07: Ben's face in p06 was rejected

    def test_deleting_a_photo_removes_its_faces(self):
        self.analyze()
        before = len(self.repo.face_fingerprints()[0])
        self.paths[0].unlink()
        scan(self.library)
        self.assertEqual(len(self.repo.face_fingerprints()[0]), before - 1)
        self.repo.remove_folder(self.library)
        self.assertEqual(self.repo.face_fingerprints()[0], [])


@unittest.skipUnless(faces.FACES_ENABLED and os.environ.get("PHOTO_LIBRARY_FACE_MODELS"),
                     "set PHOTO_LIBRARY_FACE_MODELS to a folder holding the downloaded face models")
class RealModelTests(unittest.TestCase):
    def test_models_load_and_find_no_face_in_a_landscape(self):
        from PIL import Image, ImageDraw
        engine = faces.OpenCVFaceEngine(Path(os.environ["PHOTO_LIBRARY_FACE_MODELS"]))
        image = Image.new("RGB", (1280, 853), (90, 140, 200))
        ImageDraw.Draw(image).polygon([(0, 853), (400, 300), (800, 853)], fill=(40, 90, 50))
        self.assertEqual(engine.detect(image), [])
