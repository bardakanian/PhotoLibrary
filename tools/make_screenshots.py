"""Regenerate the README screenshots from a synthetic demo library.

    python tools/make_screenshots.py

Runs the real app headless (Qt "offscreen" platform) against a temporary library of
procedurally drawn landscapes, so the images contain no personal photos, paths or
metadata. Output goes to assets/screenshots/.

People are made up too: cartoon portraits drawn here, recognised by a stand-in face engine
(`DemoFaceEngine`) that knows each made-up person by their shirt colour. No real faces and no
face models are involved.
"""

import importlib.util
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT / "assets" / "screenshots"
WINDOW_SIZE = (1280, 800)

# Environment must be set before Qt or the app's constants are imported.
WORK = Path(tempfile.mkdtemp(prefix="photolibrary-shots-"))
os.environ["PHOTO_LIBRARY_HOME"] = str(WORK / "app-data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")  # Retina-sharp images
sys.path.insert(0, str(PROJECT))

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def load_demo_module():
    spec = importlib.util.spec_from_file_location("make_demo_library", PROJECT / "tools" / "make_demo_library.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_library(folder: Path) -> None:
    demo = load_demo_module()
    rng = random.Random(2024)
    end = datetime(2026, 9, 28, 18, 0)
    albums = ["", "Hiking", "Family", "Weekend Trips", "Trips/Iceland", "Trips/Japan"]
    for index in range(700):
        # Denser in recent years, like a real library.
        taken = end - timedelta(days=(rng.random() ** 1.6) * 365 * 7, minutes=rng.randrange(1440))
        album = folder / rng.choice(albums)
        album.mkdir(parents=True, exist_ok=True)
        demo.make_photo(album / f"IMG_{index:04d}.jpg", taken, index, rng)
    # One large photo for the viewer screenshot (the grid's demo photos are small).
    hero_rng = random.Random(11)
    demo.make_photo(folder / "Trips" / "Iceland" / "IMG_HERO.jpg", end + timedelta(hours=2), 9999, hero_rng,
                    size=(2400, 1600), palette=1, lake=True, sun_at=(0.7, 0.2))
    for index in range(5):
        taken = end - timedelta(days=rng.random() * 365 * 3)
        album = folder / rng.choice(albums)
        album.mkdir(parents=True, exist_ok=True)
        demo.make_video(album / f"MOV_{index:04d}.mp4", taken, rng)
    number = 5000
    for people, count in PORTRAITS:
        for _ in range(count):
            # Mostly this year, so a person's months fill up.
            taken = end - timedelta(days=(rng.random() ** 1.5) * 300, minutes=rng.randrange(1440))
            album = rng.choice(["Family", "Family", "Weekend Trips", ""])
            make_portrait(folder / album / f"IMG_{number}.jpg", taken, people, rng)
            number += 1


# Made-up people: (name or "" for unnamed, skin, hair, hair style, shirt, glasses).
PEOPLE = {
    1: ("Maya", (241, 200, 170), (70, 45, 30), "long", (226, 88, 78), False),
    2: ("Leo", (198, 140, 105), (30, 25, 22), "short", (38, 150, 150), True),
    3: ("Nora", (250, 214, 190), (200, 150, 70), "bun", (132, 90, 192), False),
    4: ("Sam", (150, 100, 70), (20, 18, 16), "curly", (222, 170, 40), False),
    5: ("", (230, 190, 160), (120, 120, 125), "bald", (78, 160, 70), True),
    6: ("", (210, 165, 130), (90, 60, 40), "short", (52, 72, 140), False),
}
# Who is in each portrait (one or two people), and how many such photos.
PORTRAITS = [((1,), 14), ((2,), 10), ((3,), 8), ((4,), 7), ((1, 2), 8), ((3, 4), 4), ((1, 3), 3),
             ((5,), 7), ((2, 5), 5), ((6,), 6)]
BACKDROPS = [(214, 228, 240), (236, 226, 210), (222, 236, 222), (238, 222, 228), (226, 226, 232)]
SHIRT_Y = 0.9


def _slots(count: int) -> list[float]:
    return [0.5] if count == 1 else [0.3, 0.7]


def make_portrait(path: Path, taken: datetime, people: tuple[int, ...], rng: random.Random) -> None:
    """A cartoon portrait of one or two made-up people."""
    width, height = 1600, 1200
    image = Image.new("RGB", (width, height), rng.choice(BACKDROPS))
    draw = ImageDraw.Draw(image)
    top = rng.choice(BACKDROPS)
    for y in range(height // 2):  # a soft gradient behind them
        t = y / (height / 2)
        colour = tuple(int(a * (1 - t) + b * t) for a, b in zip(top, image.getpixel((0, height - 1))))
        draw.line([(0, y), (width, y)], fill=colour)
    half_body = 0.17 if len(people) == 1 else 0.15
    for cx, person in zip(_slots(len(people)), people):
        _name, skin, hair, style, shirt, glasses = PEOPLE[person]
        x = cx * width
        head_w, head_h = 0.17 * width, 0.30 * height
        head = (x - head_w / 2, 0.25 * height, x + head_w / 2, 0.55 * height)
        if style in ("long", "bun"):  # hair behind the head
            draw.ellipse((head[0] - 22, head[1] - 20, head[2] + 22, head[3] + (90 if style == "long" else -40)), fill=hair)
        draw.ellipse(((cx - half_body) * width, 0.68 * height, (cx + half_body) * width, 1.25 * height), fill=shirt)
        draw.rectangle((x - 0.03 * width, 0.5 * height, x + 0.03 * width, 0.7 * height), fill=skin)
        draw.ellipse(head, fill=skin)
        if style == "bun":
            draw.ellipse((x - 55, head[1] - 95, x + 55, head[1] + 15), fill=hair)
        if style in ("long", "short", "bun"):
            draw.chord((head[0] - 6, head[1] - 12, head[2] + 6, head[1] + head_h * 0.55), 180, 360, fill=hair)
        elif style == "curly":
            for dx in range(-5, 6):
                draw.ellipse((x + dx * 22 - 34, head[1] - 30 + abs(dx) * 6, x + dx * 22 + 34, head[1] + 38 + abs(dx) * 6),
                             fill=hair)
        eye_y = 0.40 * height
        for side in (-1, 1):
            ex = x + side * 0.032 * width
            draw.ellipse((ex - 16, eye_y - 12, ex + 16, eye_y + 12), fill=(255, 255, 255))
            draw.ellipse((ex - 8, eye_y - 8, ex + 8, eye_y + 8), fill=(40, 34, 30))
            draw.line((ex - 22, eye_y - 30, ex + 22, eye_y - 34 + side * 2), fill=hair, width=9)
            draw.ellipse((ex + side * 18 - 22, eye_y + 40, ex + side * 18 + 22, eye_y + 62),
                         fill=tuple(min(255, c + 12) if i == 0 else max(0, c - 18) for i, c in enumerate(skin)))
            if glasses:
                draw.ellipse((ex - 34, eye_y - 30, ex + 34, eye_y + 30), outline=(45, 45, 50), width=7)
        if glasses:
            draw.line((x - 0.032 * width + 34, eye_y, x + 0.032 * width - 34, eye_y), fill=(45, 45, 50), width=7)
        draw.line((x, eye_y + 20, x - 10, eye_y + 60), fill=tuple(max(0, c - 40) for c in skin), width=6)
        draw.arc((x - 60, eye_y + 40, x + 60, eye_y + 120), 20, 160, fill=(150, 60, 60), width=8)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=90, exif=exif)


class DemoFaceEngine:
    """Finds the made-up people in `make_portrait` pictures by their shirt colour (screenshots only)."""

    version = 1

    def __init__(self):
        import numpy as np
        self.np = np
        self.vectors = {}
        for person in PEOPLE:
            vector = np.random.default_rng(person * 7919).normal(size=128)
            self.vectors[person] = vector / np.linalg.norm(vector)
        self.noise = np.random.default_rng(5)

    @staticmethod
    def _colour(image, fx: float, fy: float):
        x, y = int(fx * image.width), int(fy * image.height)
        return image.crop((x - 6, y - 6, x + 6, y + 6)).resize((1, 1)).getpixel((0, 0))

    @staticmethod
    def _close(a, b, limit: int = 40) -> bool:
        return sum((p - q) ** 2 for p, q in zip(a, b)) < limit ** 2

    def _person_at(self, image, cx: float):
        """The made-up person whose shirt is at this spot, if their skin is where their cheek should be."""
        shirt, cheek = self._colour(image, cx, SHIRT_Y), self._colour(image, cx, 0.53)  # chin, below the smile
        for person, entry in PEOPLE.items():
            if self._close(entry[4], shirt) and self._close(entry[1], cheek):
                return person
        return None

    def detect(self, image):
        from photolibrary.core.faces import Face
        np, rgb = self.np, image.convert("RGB")
        slots = [0.5] if self._person_at(rgb, 0.5) else [0.3, 0.7]
        faces = []
        for cx in slots:
            person = self._person_at(rgb, cx)
            if person:
                vector = self.vectors[person] + self.noise.normal(scale=0.03, size=128)
                vector = (vector / np.linalg.norm(vector)).astype(np.float32)
                faces.append(Face(cx - 0.085, 0.25, 0.17, 0.30, 0.97, vector.tobytes()))
        return faces


def save(window, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temp = WORK / f"{name}.png"
    window.grab().save(str(temp))
    # Re-save through Pillow to shrink the PNG.
    with Image.open(temp) as image:
        image.convert("RGB").save(OUTPUT / f"{name}.png", optimize=True)
    print(f"Saved {OUTPUT / name}.png")


def main() -> None:
    library = WORK / "Demo Library"
    print(f"Creating demo library in {library} …")
    build_library(library)
    app_data = Path(os.environ["PHOTO_LIBRARY_HOME"])
    app_data.mkdir(parents=True)
    (app_data / "config.json").write_text(
        '{"library_folder": %s, "view_mode": "all", "zoom": 150, "theme": "light", '
        '"sidebar_visible": true, "info_panel_visible": false, "time_format": "12", "find_people": true, '
        '"collapsed_sections": ["library"]}'
        % __import__("json").dumps(str(library)), encoding="utf-8")

    from photolibrary.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(app)
    window.face_engine_factory = lambda _models: DemoFaceEngine()
    window.resize(*WINDOW_SIZE)
    window.show()

    def idle() -> bool:
        loader = window.loader
        return (not window.scanning and window.snapshot is not None and not loader._requested
                and len(loader._read_queue) == 0 and len(loader._generate_queue) == 0
                and not window.people_pending and bool(window.people_list))

    steps = []

    def then(step):
        steps.append(step)

    def run_next():
        if not steps:
            window.close()
            app.quit()
            return
        if not idle():
            QTimer.singleShot(150, run_next)
            return
        # Let the last thumbnails paint before grabbing.
        QTimer.singleShot(400, lambda: (steps.pop(0)(), QTimer.singleShot(300, run_next)))

    from photolibrary.core.database import open_database

    def person_id(name: str) -> int:
        return next(person.id for person in window.people_list if person.name == name)

    def name_people():
        """Name the made-up people (each found person is whoever most of their photos show)."""
        by_file = {f"IMG_{number}.jpg": people for number, people in enumerate(
            (people for people, count in PORTRAITS for _ in range(count)), start=5000)}
        for person in window.people_list:
            votes = {}
            for item in window.repository.query(window.roots, person=person.id):
                for drawn in by_file.get(item.name, ()):
                    votes[drawn] = votes.get(drawn, 0) + 1
            drawn = max(votes, key=votes.get)
            if PEOPLE[drawn][0]:
                window.repository.rename_person(person.id, PEOPLE[drawn][0])
        window.refresh_library()

    def make_group():
        window.repository.create_group("Family", [person_id("Maya"), person_id("Leo")])
        window.refresh_library()

    def library_shot():
        if not window.settings.info_panel_visible:
            window.toggle_info()
        window.view.verticalScrollBar().setValue(0)
        model, selection = window.model, window.view.selectionModel()
        flags = selection.SelectionFlag
        selection.setCurrentIndex(model.index(2, 0), flags.ClearAndSelect)
        for row in (3, 4, 9):
            selection.select(model.index(row, 0), flags.Select)
        QApplication.processEvents()
        save(window, "library")

    def prepare_years():
        window.view.clearSelection()
        window.view.setFocus()
        window.toggle_info()
        window.set_view_mode("years")
        window.view.verticalScrollBar().setValue(0)

    def prepare_months_dark():
        window.theme.apply("dark")
        window.set_view_mode("months")
        window.view.verticalScrollBar().setValue(0)

    def prepare_search():
        window.theme.apply("light")
        window.set_view_mode("all")
        window.sidebar.select_album("Trips")
        window.search_box.setText("2025")
        window.search_timer.stop()
        window._apply_search()

    def prepare_viewer():
        window.clear_search()
        window.sidebar.select_scope("all")

    def open_viewer():
        items = window.model.items
        row = next((i for i, item in enumerate(items) if item.name == "IMG_HERO.jpg"), 0)
        window.open_viewer(row)

    then(name_people)
    then(make_group)
    then(library_shot)
    then(prepare_years)
    then(lambda: save(window, "years"))
    then(prepare_months_dark)
    then(lambda: save(window, "months-dark"))
    then(prepare_search)
    then(lambda: save(window, "search"))
    then(lambda: (window.clear_search(), window.sidebar.select_scope("all"), window.show_insights()))
    then(lambda: save(window, "insights"))
    then(window.close_insights)
    then(prepare_viewer)
    then(open_viewer)
    then(lambda: None)  # give the full-size decode a moment to replace the thumbnail
    then(lambda: save(window, "viewer"))

    def people_shot():
        window.close_viewer()
        window.sidebar.select_scope("person", None, person_id("Maya"))
        window.view.verticalScrollBar().setValue(0)

    def loosen_faces():
        """Leave a few of Nora's faces ungrouped, as grouping sometimes does, for Review to ask about."""
        connection = open_database()
        try:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM faces WHERE person_id = ? ORDER BY id LIMIT 3", (person_id("Nora"),))]
            connection.executemany("UPDATE faces SET person_id = NULL, pinned = 0 WHERE id = ?", [(i,) for i in ids])
            connection.commit()
        finally:
            connection.close()
        window.refresh_library()

    def viewer_faces():
        window.close_review()
        window.sidebar.select_scope("group", None, next(group.id for group in window.groups_list))

    def open_faces():
        window.open_viewer(0)
        window.viewer.faces_button.setChecked(True)

    then(people_shot)
    then(lambda: save(window, "people"))
    then(loosen_faces)
    then(window.show_review)
    then(lambda: save(window, "review"))
    then(viewer_faces)
    then(lambda: save(window, "group"))
    then(open_faces)
    then(lambda: None)  # the faces load in the background
    then(lambda: save(window, "viewer-faces"))
    QTimer.singleShot(500, run_next)
    QTimer.singleShot(300_000, app.quit)  # safety net
    app.exec()


if __name__ == "__main__":
    main()
