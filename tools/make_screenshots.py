"""Regenerate the README screenshots from a synthetic demo library.

    python tools/make_screenshots.py

Runs the real app headless (Qt "offscreen" platform) against a temporary library of
procedurally drawn landscapes, so the images contain no personal photos, paths or
metadata. Output goes to assets/screenshots/.
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

from PIL import Image  # noqa: E402
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
        '"sidebar_visible": true, "info_panel_visible": false, "time_format": "12"}'
        % __import__("json").dumps(str(library)), encoding="utf-8")

    from photolibrary.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(app)
    window.resize(*WINDOW_SIZE)
    window.show()

    def idle() -> bool:
        loader = window.loader
        return (not window.scanning and window.snapshot is not None and not loader._requested
                and len(loader._read_queue) == 0 and len(loader._generate_queue) == 0)

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
        window.sidebar.select_scope("all", "Trips")
        window.search_box.setText("2025")
        window.search_timer.stop()
        window._apply_search()

    def prepare_viewer():
        window.clear_search()
        window.sidebar.select_scope("all", None)

    def open_viewer():
        items = window.model.items
        row = next((i for i, item in enumerate(items) if item.name == "IMG_HERO.jpg"), 0)
        window.open_viewer(row)

    then(library_shot)
    then(prepare_years)
    then(lambda: save(window, "years"))
    then(prepare_months_dark)
    then(lambda: save(window, "months-dark"))
    then(prepare_search)
    then(lambda: save(window, "search"))
    then(lambda: (window.clear_search(), window.sidebar.select_scope("all", None), window.show_insights()))
    then(lambda: save(window, "insights"))
    then(window.close_insights)
    then(prepare_viewer)
    then(open_viewer)
    then(lambda: None)  # give the full-size decode a moment to replace the thumbnail
    then(lambda: save(window, "viewer"))
    QTimer.singleShot(500, run_next)
    QTimer.singleShot(180_000, app.quit)  # safety net
    app.exec()


if __name__ == "__main__":
    main()
