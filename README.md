# Photo Library

A fast, private desktop library for the photos and videos in one folder, built with
PySide6. Pick a folder and browse everything in it newest first, grouped by month, in
the style of Apple Photos. Indexing, thumbnails and playback all happen on your
computer. Nothing is uploaded.

The look and the media core (capture-date detection, HEIC support, thumbnail cache,
incremental indexer) come from the On This Day app.

## Features

- **Library folder** chosen once and remembered. It's indexed in the background with a
  progress bar. Every launch, and **Rescan**, picks up added, changed and deleted files.
- **Virtualized grid** that stays smooth with 50,000+ items. It has month section
  headers with a sticky header at the top, and a thumbnail size slider.
- **Years / Months / All Photos** views. Click a year to see its months, and a month to
  jump to it in All Photos.
- **Sidebar**: All Photos, Photos and Videos with counts, plus each top-level subfolder
  as an **album** with its newest photo as a thumbnail. An album includes everything in
  its nested folders. Toggle the sidebar with ⌥⌘S.
- **Search** (⌘F) by filename words and by date: `2023`, `May`, `May 2023`, `2023-05`,
  `2023-05-14` or `14 May 2023`. Words and dates combine (`beach 2023`). Search applies
  to the current sidebar selection. Esc clears it, and Return jumps to the results.
- **Info panel** (⌘I or the ⓘ button) shows the filename, date taken, dimensions, file
  size, camera, duration for videos, album and path. It has **Reveal in Finder** and
  **Open in Default App** buttons. With several items selected it shows the count, the
  date range and the total size. It works in the viewer too.
- **Selection** works like Photos: click, ⌘-click to add, ⇧-click for a range, ⌘A, and Esc
  to deselect. Right-click for Open, Get Info, Reveal, Open in Default App and Move to Trash.
- **Move to Trash** (⌘⌫) works on the selection, or on the item open in the viewer. It
  always asks first, names what will be moved, and uses the system Trash (`QFile.moveToTrash`),
  so files can be restored. Trashed items leave the grid without a reload, the scroll
  position stays put and the next item is selected. Files that couldn't be moved are listed.
- **Library Insights** (the **Insights** toolbar button or ⌘⇧I) gives a snapshot of the
  whole library:
  - photo, video and total counts, and the storage they use
  - the date span from the oldest to the newest capture
  - the most used camera
  - the largest year, the most active month and the busiest calendar day
  - a photos-and-videos-by-year chart; hover a bar for exact counts

  It reads only the index, so it opens instantly and never rescans. It refreshes after
  each scan. Esc returns to the grid.
- **Empty states** for no folder, an empty library, an empty album, and no search results.
- **Sort order:** the toolbar button (or View → Newest First / Oldest First) switches between
  newest first and oldest first, so each month starts at its 1st. It applies everywhere:
  albums, Photos/Videos, search, Years/Months and the viewer's ← / →. It's remembered
  between launches. If you're at the top when you switch, you stay at the top; further
  down, the photo you were looking at stays in view.
- **Viewer**: double-click, Return or ⌘↓ opens an item full-window. ← and → step through
  the library. Esc (or ⌘↑) returns to the grid exactly where you left it.
  - **Photos** are shown with their EXIF rotation applied. Zoom with ⌘-scroll, pinch,
    ⌘= / ⌘−, or a double-click (Fit ↔ 100%), and drag to pan. The grid thumbnail
    appears at once, then a screen-sized decode replaces it; zooming in past that loads
    the full-resolution image. The next and previous photos are decoded in advance.
  - **Videos** play in the app (QtMultimedia) with play/pause, a scrubber, time, volume
    and mute.

## Screenshots

These screenshots come from a synthetic demo library of computer-drawn landscapes with
made-up dates. They contain no personal photos, file paths or metadata. To regenerate
them, run `python tools/make_screenshots.py`.

### Library with the info panel

All Photos grouped by month, with albums in the sidebar and a multi-item selection
summarised in the info panel.

![All Photos grid with sidebar, a selection of four photos and the info panel](assets/screenshots/library.png)

### Years

One tile per year. Click a year to see its months.

![Years view with a large tile for 2026](assets/screenshots/years.png)

### Months, in dark mode

Month tiles grouped by year, shown with the dark theme.

![Months view in dark mode](assets/screenshots/months-dark.png)

### Search within an album

Searching "2025" inside the Trips album.

![Search results for 2025 in the Trips album](assets/screenshots/search.png)

### Library Insights

Totals, storage, date span, the most used camera, the busiest times, and media by year.

![Library Insights with nine summary cards and a media-by-year chart](assets/screenshots/insights.png)

### Viewer

Full-window photo viewer with previous and next, zoom and the info toggle.

![Photo viewer showing a sunset landscape](assets/screenshots/viewer.png)

## Settings

Open **Settings** with ⌘, or the ⚙ button at the right end of the toolbar (also in the app menu on macOS, or File on other systems) to choose:

- **Theme**: follow the system appearance, or always light or dark.
- **Time format**: follow the system clock, a 12-hour clock (1:30 PM) or a 24-hour
  clock (13:30). It applies wherever a capture time is shown, such as the viewer header.

Qt Multimedia's informational messages (the "Using Qt multimedia with FFmpeg" banner
and FFmpeg's per-file stream details) are hidden. Warnings and errors still appear. To
see everything while debugging, run:

```bash
QT_LOGGING_RULES="qt.multimedia.*=true" python app.py
```

## Requirements

- Python 3.10 or newer (developed on 3.14)
- macOS is the primary target; Windows and Linux should work
- Dependencies in `requirements.txt`: PySide6, Pillow, pillow-heif (HEIC/HEIF) and
  imageio-ffmpeg (video dates, thumbnails and metadata)

The app still runs without pillow-heif or imageio-ffmpeg; HEIC files or video previews
are then limited.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

### Launch it like an app (macOS)

```bash
python tools/make_launcher.py
```

This builds `dist/Photo Library.app` and installs a copy in `~/Applications`. You can
double-click it, find it with Spotlight, or drag it to the Dock. It's a few KB and contains
no code of its own: it runs this project's `app.py` with the project's `.venv`, so it
always runs your latest code. Notes:

- While the app is running, macOS shows it as **Python** in the Dock and menu bar,
  because Homebrew's Python framework runs apps inside its own `Python.app`.
- Re-run the script if you move the project folder or recreate `.venv`. If the project
  can't be found, the launcher shows an alert.
- Output and errors go to `~/.photo_library/launcher.log`.

To try it without your own photos, generate a synthetic library first:

```bash
python tools/make_demo_library.py ~/Desktop/demo-library --photos 2000 --videos 10
```

## Keyboard shortcuts

On macOS, ⌘ is Command. On Windows and Linux, use Ctrl.

| Action | Shortcut |
| --- | --- |
| **Library** | |
| Choose library folder | ⌘O |
| Rescan library | ⌘R |
| Search by name or date | ⌘F (Esc clears it, Return jumps to the results) |
| Years / Months / All Photos | ⌘1 / ⌘2 / ⌘3 |
| Newest first / oldest first | Sort button in the toolbar, or the View menu |
| Bigger / smaller thumbnails | ⌘= / ⌘− (or ⌘-scroll, or pinch) |
| Show or hide the sidebar | ⌥⌘S |
| Show or hide info | ⌘I |
| Library Insights | ⌘⇧I (Esc returns) |
| **Selecting** | |
| Move between items | Arrow keys, Page Up/Down, Home/End |
| Add to the selection / select a range | ⌘-click / ⇧-click (or ⇧-arrows) |
| Select all / deselect all | ⌘A / ⌘⇧A (Esc also deselects) |
| **Items** | |
| Open in the viewer | Double-click, Return, Space or ⌘↓ |
| Move to Trash (asks first) | ⌘⌫ or Delete |
| Reveal in Finder | ⌘⇧R |
| Open in default app | ⌘⇧O |
| More actions | Right-click an item |
| **Viewer** | |
| Back to the grid | Esc or ⌘↑ (Space also closes a photo) |
| Previous / next item | ← / → (Home / End for first / last) |
| Zoom photo / actual size | ⌘= / ⌘−, ⌘0 (or double-click, pinch, ⌘-scroll) |
| Play / pause, skip 5 s, mute | Space, ⇧← / ⇧→, M |
| **App** | |
| Settings (appearance, 12/24-hour time) | ⌘, or the ⚙ toolbar button |
| Close window | ⌘W |

## How it stays fast

- **Indexing** (`core/indexer.py`) walks the folder with `os.scandir`. It reads
  metadata on a thread pool, and every SQLite write happens on the indexer's single
  thread. Unchanged files (same size and modification time) are skipped.
- **The grid** (`ui/grid_view.py`) is a custom `QAbstractItemView`. Its geometry comes
  from `core/grid_layout.py`, which works on month sections, not individual items. Lookups
  are binary searches, so painting and hit-testing only touch the cells on screen.
- **Thumbnails** are created once and cached on disk (512 px, aspect kept). Creating
  one means decoding the original: about 50 ms for a 12 MP JPEG and about 450 ms for a HEIC.
  Reading a cached one takes about 1 ms.
  - **Prepared in advance.** After each scan, `core/thumbnail_warmer.py` creates every
    missing thumbnail in the background, newest first, using half the CPU cores. The
    status bar shows its progress. Once it finishes, scrolling never waits on an original.
    In a test with 150 ms per original, blank cells while scrolling dropped from 78% to 0%.
    It's a one-time cost: rescans only prepare new or changed files.
  - **Loaded around you.** `ui/thumbnails.py` decodes cached thumbnails on worker threads
    at about the cell's size and keeps them in an in-memory LRU of about 480 MB. While you
    scroll, visible cells load first and work for cells that scrolled away is dropped. When
    you stop, the next 5 screens and the previous 2 are loaded too.
  - **Why not everything in memory?** Each decoded thumbnail is about 0.5 MB, so a
    50,000-item library would need about 25 GB of RAM. Disk-cached thumbnails plus
    read-ahead give the same smooth scrolling at a fixed memory cost.
- **The UI thread never reads files or the database.** Queries, grouping, settings
  writes and thumbnail decoding run in the background (`ui/tasks.py`).

## Project structure

```text
app.py                     Entry point
photolibrary/
├── core/                  No Qt imports; unit-tested
│   ├── constants.py       Paths, supported formats, thumbnail size
│   ├── database.py        SQLite index (WAL) and MediaRepository queries
│   ├── indexer.py         Incremental background indexer
│   ├── media.py           Dates, metadata, thumbnails, open/reveal helpers
│   ├── grouping.py        Month sections and Years/Months summaries
│   ├── grid_layout.py     Sectioned grid geometry
│   ├── search.py          Search box parsing (filename and dates)
│   ├── trash.py           Move to Trash plus index and thumbnail cleanup
│   ├── cache.py           Cost-bounded LRU cache
│   ├── workqueue.py       Re-prioritisable background job queue
│   ├── formatting.py      Labels for sizes, durations, dates
│   ├── zoom.py            Viewer zoom and pan arithmetic
│   └── settings.py        Remembered settings (JSON)
└── ui/
    ├── main_window.py     Toolbar, sidebar, search, menus, indexing and query plumbing
    ├── time_format.py     12/24-hour clock preference
    ├── grid_view.py       Virtualized grid view and thumbnail delegate
    ├── grid_model.py      Qt model over a library snapshot
    ├── thumbnails.py      Background thumbnail loader and pixmap cache
    ├── tasks.py           run_async helper for background work
    ├── viewer_images.py   Background full-size photo decoding for the viewer
    ├── pages/             Grid page, full-window viewer and Library Insights
    ├── theme/             Colours, typography, metrics, stylesheet
    └── widgets/           Sidebar, info panel, image canvas, video player, empty state, search box
tests/                     unittest suite for the core
tools/make_demo_library.py Synthetic library generator
tools/make_launcher.py     Builds the macOS launcher app
tools/make_screenshots.py  Regenerates the README screenshots
assets/screenshots/        README screenshots (synthetic demo library)
```

## Development

Run the tests from the project root:

```bash
python -m unittest -v
```

App data (config, the SQLite index, thumbnails and the log) lives in
`~/.photo_library/`. Set `PHOTO_LIBRARY_HOME` to use a different folder, for example
for a demo library:

```bash
PHOTO_LIBRARY_HOME=/tmp/photo-demo python app.py
```

## Project status

The app is under active development. Use a backed-up media library, review detected
capture dates, and keep the local app-data directory out of source control.

## Development approach

Developed with AI-assisted tools under human direction, review, testing, and refinement.

## License

This project is available under the [MIT License](LICENSE).
