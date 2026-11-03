# Changelog

With every change, bump `VERSION` in `photolibrary/core/constants.py` and add an entry here.

Versions before 1.6.0 were numbered afterwards from the git history: a minor version for each
commit that added features, a patch version for each fix.

## 1.6.1 — 2026-11-02

- A library or album folder that exists but can't be read (no permission, a drive that isn't
  ready) no longer has all its photos removed from the library. The scan keeps them and says
  the folder couldn't be read.
- Shift-click selects from the photo you clicked, even after zooming or resizing the window.
  It used to start from whatever photo had moved under that spot.
- Settings changes made in quick succession are no longer lost when an older save finishes last.
- Automatic updates work for a library inside a hidden folder, such as `~/.backup/Photos`.
- Search ignores letter case for every letter (`été` finds `Été.jpg`), not only A–Z.

## 1.6.0 — 2026-11-01

- The version number is shown in the window title and set as the application version.
- This changelog; the macOS launcher's bundle version now follows `VERSION`.

## 1.5.4 — 2026-10-31

- Fix the grid scrollbar bouncing on and off at some window sizes (for example half screen
  with the info panel open), where the scrollbar's own width made everything fit.

## 1.5.3 — 2026-10-31

- Fix a black viewer screen for photos opened before their thumbnail was in memory.

## 1.5.2 — 2026-10-30

- Folder display names and the toolbar tests work on Windows.

## 1.5.1 — 2026-10-30

- Toolbar labels no longer clip on narrower windows; Choose Folder, Rescan and the sort
  button drop to icons when the full toolbar doesn't fit.

## 1.5.0 — 2026-10-29

- Several library folders: Add Folder (⌘O), a Folders section in the sidebar, and Remove
  Folder from Library. A missing folder (an unplugged drive) keeps its index.
- Hidden albums: an album's photos can be hidden from All Photos, search, counts and Insights.
- Fix a crash in `QBackingStore::flush` caused by the freeze monitor keeping a painter alive.
- Fix 100–500 ms UI freezes during scans: helper programs start with `posix_spawn`, not `fork()`.

## 1.4.0 — 2026-10-29

- Windows Start menu and Desktop launcher, and a window icon.
- Fix video stutter on Windows caused by last-access-time changes triggering rescans.
- Faster missing-thumbnail checks; thumbnail warming pauses while a video plays.
- A freeze monitor logs what every thread was doing when the UI stalls.

## 1.3.0 — 2026-10-28

- Live Photos: a HEIC/MOV pair shows once with a LIVE badge and plays in the viewer (L).
- Folder watching picks up added, moved and deleted files without a rescan.
- Sharp covers for Years/Months tiles, and cleanup of thumbnails no file needs.

## 1.2.0 — 2026-10-28

- Newest-first / oldest-first sort order, remembered between launches.

## 1.1.0 — 2026-10-28

- Library Insights (⌘⇧I): counts, storage, date span, cameras and busiest periods.
- Background thumbnail warming so scrolling never waits on an original.

## 1.0.0 — 2026-10-28

- First published version: a virtualized photo and video grid for a local folder, with
  Years/Months/All views, zoom, a viewer with video playback, albums, search, an info panel
  and Move to Trash.
