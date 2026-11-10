"""Group face fingerprints into people, and keep each person (and their name) stable across regroupings.

Grouping is DBSCAN on cosine similarity, written with numpy alone: a face with enough close
neighbours is a "core" face, core faces that are close to each other form one group, and a
face near a group joins it. Lone faces (strangers in the background) stay ungrouped. The
similarity matrix is never held whole: rows are compared in blocks, so tens of thousands of
faces need little memory.
"""

from collections import Counter
from collections.abc import Sequence

# SFace's published cosine threshold for comparing two faces is 0.363, but grouping tens of
# thousands of faces chains through the rare unrelated pairs above it and merges everyone into
# one group (measured with 30,000 synthetic faces). 0.5 kept 600 people apart; 0.45 mixed some.
SIMILARITY_THRESHOLD = 0.5
MIN_NEIGHBOURS = 3      # a core face has at least this many close faces (itself included)
MIN_FACES = 5           # smaller groups are not shown as a person
MIN_VOTES = 3           # faces a group needs from an existing person to continue it
# A group whose faces are, typically, less alike than this (to the group's average face) is
# several people chained together, and is regrouped with a stricter cutoff. Measured on a real
# library: one person's group (median 0.50) held three people of 3,350, 943 and 931 faces; single
# people's groups were 0.70-0.77. 0.6 and 0.65 gave the same groups there; 0.65 also separates two
# people chained together, whose shared average sits around 0.7 when their faces are tightly alike.
MIN_COHESION = 0.65
SPLIT_STEP = 0.05
SPLIT_LIMIT = 0.7       # the strictest cutoff a group is regrouped with
BLOCK = 2048


def cluster_faces(embeddings, threshold: float = SIMILARITY_THRESHOLD, min_neighbours: int = MIN_NEIGHBOURS,
                  min_faces: int = MIN_FACES, block: int = BLOCK, split: bool = True):
    """Group label per face (0, 1, …, largest first), or -1 for faces in no group. Rows have length 1.

    With `split`, groups that are several people chained together are regrouped more strictly.
    """
    import numpy as np  # only needed once People is on (it comes with OpenCV)

    vectors = np.asarray(embeddings, dtype=np.float32)
    labels = _dbscan(vectors, threshold, min_neighbours, block)
    if split:
        labels = _split_loose_groups(vectors, labels, threshold, min_neighbours, block)
    # Drop small groups and number the rest 0, 1, … by size (largest first).
    grouped = labels >= 0
    sizes = Counter(labels[grouped].tolist())
    order = {label: index for index, (label, size) in enumerate(sorted(sizes.items(), key=lambda pair: (-pair[1], pair[0])))
             if size >= min_faces}
    return np.array([order.get(int(label), -1) if label >= 0 else -1 for label in labels], dtype=np.int64)


def _cohesion(vectors) -> float:
    """How alike a group's faces are: the median similarity to their average face."""
    import numpy as np

    centre = vectors.mean(axis=0)
    norm = float(np.linalg.norm(centre))
    return float(np.median(vectors @ (centre / norm))) if norm else 0.0


def _split_loose_groups(vectors, labels, threshold: float, min_neighbours: int, block: int):
    """Regroup each loose group on its own with a stricter cutoff, until every group is cohesive."""
    import numpy as np

    labels = labels.copy()
    next_label = int(labels.max()) + 1 if len(labels) else 0
    pending = [(int(label), threshold) for label in set(labels.tolist()) if label >= 0]
    while pending:
        label, cutoff = pending.pop()
        members = np.flatnonzero(labels == label)
        stricter = round(cutoff + SPLIT_STEP, 4)
        if len(members) < 2 * MIN_FACES or stricter > SPLIT_LIMIT or _cohesion(vectors[members]) >= MIN_COHESION:
            continue
        parts = _dbscan(vectors[members], stricter, min_neighbours, block)
        labels[members] = -1
        for part in set(parts.tolist()) - {-1}:
            labels[members[parts == part]] = next_label
            pending.append((next_label, stricter))
            next_label += 1
    return labels


def _dbscan(vectors, threshold: float, min_neighbours: int, block: int):
    """Raw DBSCAN labels (any numbers, -1 for none)."""
    import numpy as np

    count = len(vectors)
    labels = np.full(count, -1, dtype=np.int64)
    if count == 0:
        return labels
    # 1. Core faces: enough neighbours above the threshold.
    neighbours = np.zeros(count, dtype=np.int64)
    for start in range(0, count, block):
        neighbours[start:start + block] = (vectors[start:start + block] @ vectors.T >= threshold).sum(axis=1)
    core = np.flatnonzero(neighbours >= min_neighbours)
    if not len(core):
        return labels
    # 2. Connect core faces that are close (edges kept only one way, i < j).
    core_vectors = vectors[core]
    sources, targets = [], []
    for start in range(0, len(core), block):
        close = core_vectors[start:start + block] @ core_vectors.T >= threshold
        rows, cols = np.nonzero(close)
        rows += start
        keep = rows < cols
        sources.append(rows[keep].astype(np.int64))
        targets.append(cols[keep].astype(np.int64))
    sources, targets = np.concatenate(sources), np.concatenate(targets)
    # 3. Connected components: push the smallest label along every edge, then jump pointers.
    component = np.arange(len(core), dtype=np.int64)
    while True:
        lowest = np.minimum(component[sources], component[targets])
        updated = component.copy()
        np.minimum.at(updated, sources, lowest)
        np.minimum.at(updated, targets, lowest)
        while True:
            jumped = updated[updated]
            if np.array_equal(jumped, updated):
                break
            updated = jumped
        if np.array_equal(updated, component):
            break
        component = updated
    labels[core] = component
    # 4. Border faces join the group of their most similar core face, if it is close enough.
    border = np.setdiff1d(np.flatnonzero(neighbours > 0), core, assume_unique=True)
    for start in range(0, len(border), block):
        rows = border[start:start + block]
        similarity = vectors[rows] @ core_vectors.T
        best = similarity.argmax(axis=1)
        close = similarity[np.arange(len(rows)), best] >= threshold
        labels[rows[close]] = component[best[close]]
    return labels


def assign_people(labels: Sequence[int], previous: Sequence[int | None]) -> dict[int, int | None]:
    """Which existing person each new group continues (None: a new person).

    A group inherits the person most of its previously assigned faces belonged to, so names
    survive regrouping. Each person is continued by one group only, the one holding most of their
    faces: when a group turns out to be several people, the others become new people. (Merges
    survive because merging locks the faces in place.)
    """
    votes: dict[int, Counter] = {}
    sizes: Counter = Counter()
    for label, person in zip(labels, previous):
        if label < 0:
            continue
        sizes[int(label)] += 1
        counter = votes.setdefault(int(label), Counter())
        if person is not None:
            counter[person] += 1
    claims = []
    for label, counter in votes.items():
        if counter:
            person, count = min(counter.items(), key=lambda pair: (-pair[1], pair[0]))
            # A couple of stray faces must not hand a new person someone else's name.
            if count >= MIN_VOTES or count * 2 >= sizes[label]:
                claims.append((-count, label, person))
    result: dict[int, int | None] = dict.fromkeys(votes)
    taken = set()
    for _count, label, person in sorted(claims):
        if person not in taken:
            taken.add(person)
            result[label] = person
    return result


def regroup(repository, threshold: float = SIMILARITY_THRESHOLD) -> int:
    """Regroup every face the user hasn't pinned, keeping existing people. Returns how many groups there are."""
    import numpy as np

    face_ids, embeddings, _people = repository.face_fingerprints()
    if not face_ids:
        return 0
    vectors = np.frombuffer(b"".join(embeddings), dtype=np.float32).reshape(len(face_ids), -1)
    labels = cluster_faces(vectors, threshold=threshold)
    # Who each group continues is decided inside the write, on the people as they are by then.
    repository.apply_grouping(face_ids, labels.tolist(), assign_people, vectors)
    return int(labels.max()) + 1 if len(labels) and labels.max() >= 0 else 0


# A face in no group is suggested for the person whose average face it is this close to. Lower
# than SIMILARITY_THRESHOLD: comparing with an average is steadier than with single faces, and
# the user decides. Not yet tuned on real photos.
SUGGEST_THRESHOLD = 0.45


def suggest_people(faces, centroids, person_ids: Sequence[int], rejected: Sequence[set],
                   threshold: float = SUGGEST_THRESHOLD) -> list[tuple[int, int, float]]:
    """(face index, person id, similarity) for faces close to a person's average face, best first.

    `faces` and `centroids` are rows of length 1; `rejected[i]` holds people face i is not.
    """
    import numpy as np

    faces = np.asarray(faces, dtype=np.float32)
    centroids = np.asarray(centroids, dtype=np.float32)
    if not len(faces) or not len(centroids):
        return []
    found = []
    for start in range(0, len(faces), BLOCK):
        similarity = faces[start:start + BLOCK] @ centroids.T
        order = np.argsort(-similarity, axis=1)
        for row in range(len(similarity)):
            index = start + row
            for column in order[row]:
                value = float(similarity[row, column])
                if value < threshold:
                    break
                if person_ids[column] not in rejected[index]:
                    found.append((index, int(person_ids[column]), value))
                    break
    found.sort(key=lambda entry: -entry[2])
    return found


def centroids_of(vectors, person_of: Sequence[int]):
    """(person ids, average face of each, of length 1) from faces and their people."""
    import numpy as np

    people = sorted(set(person_of))
    position = {person: index for index, person in enumerate(people)}
    sums = np.zeros((len(people), vectors.shape[1]), dtype=np.float64)
    np.add.at(sums, [position[person] for person in person_of], vectors)
    norms = np.linalg.norm(sums, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return people, (sums / norms).astype(np.float32)


# A new group rejoins a named person whose faces had gone (moved files, a folder added back, new
# face models) when its average face is this close to the person's stored reference. Different
# people's averages were at most 0.43 apart in a real library; the same person's sit near 0.9.
REJOIN_SIMILARITY = 0.7


def match_references(groups: dict, references: dict, threshold: float = REJOIN_SIMILARITY) -> dict:
    """{group label: person} for groups whose average face matches a stored reference; each person once."""
    import numpy as np

    pairs = []
    for label, centre in groups.items():
        for person, reference in references.items():
            pairs.append((float(np.dot(centre, reference)), label, person))
    matched, used = {}, set()
    for similarity, label, person in sorted(pairs, key=lambda pair: (-pair[0], pair[1], pair[2])):
        if similarity < threshold:
            break
        if label not in matched and person not in used:
            matched[label] = person
            used.add(person)
    return matched


SAVE_BATCH = 50  # photos per database write while analysing


def run_people_pass(repository, folders, make_engine, stop_event, on_progress=None, engine_version: int | None = None,
                    load_image=None):
    """Analyse every photo not yet analysed, then regroup. Runs on a background thread.

    `make_engine()` is only called when there is work, so the models are only loaded (or
    downloaded) when needed. Returns (AnalyzeResult, number of groups), or groups None if
    the pass was cancelled before regrouping.
    """
    from .faces import ENGINE_VERSION, analyze_photos  # needs OpenCV; imported only when People is on

    version = ENGINE_VERSION if engine_version is None else engine_version
    repository.restore_people_backup()  # names lost with the database come back from people.json
    todo = repository.photos_to_analyze(folders, version)
    batch: list = []

    def keep(item, found) -> None:
        batch.append((item, found))
        if len(batch) >= SAVE_BATCH:
            repository.save_faces(batch, version)
            batch.clear()

    engine = make_engine() if todo else None
    extra = {"load_image": load_image} if load_image is not None else {}
    result = analyze_photos(todo, engine, stop_event, keep, on_progress, **extra)
    if batch:
        repository.save_faces(batch, version)
    if stop_event.is_set():
        return result, None
    return result, regroup(repository)
