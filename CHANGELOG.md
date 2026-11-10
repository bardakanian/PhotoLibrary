# Changelog

With every change, bump `VERSION` in `photolibrary/core/constants.py` and add an entry here.

Versions before 1.6.0 were numbered afterwards from the git history: a minor version for each
commit that added features, a patch version for each fix.

## 1.17.0 — 2026-11-10

- **Review Suggestions** only asks about people you have named ("Is this Ana?"). Faces that look
  like one of the many unnamed groups, mostly passers-by, are no longer suggested. Name someone to
  get suggestions for them.

## 1.16.1 — 2026-11-10

Sixteen bug fixes:

- **Groups:** editing a group no longer drops members who have no photo shown at the moment (say,
  only in a hidden album). An unnamed member who loses their faces in a regroup stays in the group
  instead of being deleted, which made the group show every photo of the others. Unnamed members
  and hidden unnamed people are now kept in the people.json backup and come back after a rebuild.
- After finding people, an open group's photos are refreshed too (only a person's were).
- **Video dates** use the camera's own local time, as photos do: a video shot at 10:00 in Tokyo
  was filed at 18:00 the day before on a Mac in California. Every file is read once more on the
  next scan to fix existing dates.
- **Thumbnails:** one that couldn't be made (a drive asleep or unplugged) is tried again after
  30 seconds instead of staying blank until the app restarts. A damaged cached thumbnail is made
  again rather than marking the photo as having no preview.
- Moving to the Trash while another view was still loading no longer leaves the grid showing the
  old view.
- If a Live Photo is trashed but its video can't be, the video now shows as a video of its own
  (it was hidden, and so couldn't be trashed, until the next rescan).
- **Insights and Review:** ⌥⌘S and ⌘I no longer open the sidebar or info panel over them,
  ⌘⌫ no longer offers to trash the (hidden) grid selection, and ⌘↓ no longer opens the viewer from
  Review. Closing the viewer brings back the info panel if it is on.
- Sizes of 1,000 GB and more show in TB, and 999,999 bytes shows as 1.0 MB, not 1000.0 KB.
- Show in Finder and Open in Default App start their helper without forking the whole app (a brief
  UI freeze).
- Scan notes name missing and unreadable folders separately.
- Background queries dropped from the queue are no longer kept in memory.

## 1.16.0 — 2026-11-10

- **People finds more faces:** photos are analysed at up to 1,920 px (was 1,280) with a more
  sensitive detector (smaller and less certain faces count). On 400 photos of a real library
  that found 35% more faces, in 15% more photos, for about 20% more time per photo.
- Every photo is analysed once more with the new settings (about 10–15 minutes for 18,000
  photos). Names, confirmed faces, "not this person" answers and groups are all kept.

## 1.15.0 — 2026-11-10

- **Unnamed People** is its own sidebar section, after People, which now lists only named people
  (and Review Suggestions). Fold Unnamed People with its heading to hide them all; the app
  remembers it.
- Checkboxes are drawn in the theme's colours, so they're clearly visible in the dark theme: the
  people in the group window and the options in Settings were near-invisible black squares.

## 1.14.0 — 2026-11-10

- **Groups of people:** put several people in a group (right-click a person ▸ Add to Group ▸
  New Group…, or View ▸ New Group…). A group, listed under Groups in the sidebar, shows the
  photos where everyone in it appears together. Right-click a group to rename it, edit its
  members or delete it (people and photos stay). Groups follow merges and are kept in the
  people.json backup.

## 1.13.1 — 2026-11-10

- Larger face pictures in the sidebar's People list (34 px instead of 22, in slightly taller rows),
  so people are easier to recognise. Library, folder and album rows keep their size.

## 1.13.0 — 2026-11-10

- **Removing wrong photos from a person is easier:** right-click selected photos while viewing a
  person ▸ "Not Ana – Remove from Ana", or press ⌥⌫ (in the grid or on the photo open in the
  viewer). The menu item names the person, and the status line confirms what was removed.
- Fix: any sidebar refresh (after a scan, a rename, Move to Trash or a People change) switched
  from the person you were viewing back to All Photos.

## 1.12.0 — 2026-11-10

Names given to people are now kept safe:

- **Names follow the face, not the file.** Each named (or hidden) person keeps a reference face.
  When their photos come back as new to the app (moved or renamed files and folders, a folder
  removed and added back, new face models), the matching group rejoins them with their name.
- **Answers survive re-analysis.** When a photo is analysed again, faces in the same place keep
  their confirmed person and any "not this person" answers.
- **A backup of names:** `~/.photo_library/people.json` is rewritten after every change to
  people. If the database is lost or rebuilt, named and hidden people are restored from it on the
  next People pass, and their faces return to them.

## 1.11.0 — 2026-11-10

- **Check Faces:** right-click a person ▸ Check Faces (Least Typical First), or View ▸ Least
  Typical Faces First while viewing a person, lists their photos with the face least like their
  other faces at the top, where wrong matches gather. Fix them with Edit ▸ Remove from This
  Person or the viewer's face menu. Switching to Years or Months, or leaving the person, turns it off.

## 1.10.1 — 2026-11-10

- Opening a person in thousands of photos is instant: 16 ms instead of 29 seconds for someone
  with 6,000 faces. The query checked every one of their faces for every photo.
- Grouping no longer chains different people into one. A group whose faces aren't alike enough
  is regrouped more strictly; on a real library one "person" in 4,181 photos turned out to be
  three people (in 3,042, 871 and 824 photos).
- When a group splits, only the part with most of a named person's faces keeps the name; the
  others become new people to name. Merging locks the merged faces so the merge stays.
- Named people are listed first. Unnamed people in fewer than 10 photos are folded behind
  "Show N More…" at the end of People.

## 1.10.0 — 2026-11-10

- **Review People** (the "Review Suggestions" row in People, or View ▸ Review People…, ⇧⌘P):
  faces that look like someone but weren't grouped with them, one at a time: "Is this Ana?"
  Yes (Y) locks the face to the person, No (N) is remembered so it's never suggested or
  regrouped into them again, Skip (→) leaves it for later.
- **Faces in the viewer:** the Faces button (F) outlines each face with its name. Click a face
  to confirm it, say it's not that person, pick who it is, or start a new person from it.
  Confirmed faces have a solid outline; suggestions from grouping a dashed one.
- Removing photos from a person now remembers the face isn't them, so the face can still join
  the person it really is.

## 1.9.2 — 2026-11-10

- People analyses more photos at once: all cores but two, up to 8 (was a third of the cores).
  On a 10-core Mac that is 24 ms per photo instead of 36, about 7 minutes for 17,000 photos,
  with the window staying responsive.

## 1.9.1 — 2026-11-10

- People analyses HEIC photos about 9 times faster (47 ms instead of 446 ms each) by using the
  system's image decoder, which reads them straight at the smaller size. A library of about
  17,000 photos now takes roughly 15 minutes for the first pass instead of over an hour.
- Photos that can't be reached during a pass (an unplugged drive) are no longer recorded as
  having no faces; they are analysed once they're back.
- Face pictures in the sidebar are cut from the 512 px thumbnail, so they're sharp.
- A merge (or a photo removed from a person) made while people were being regrouped is no
  longer overwritten by the regroup.

## 1.9.0 — 2026-11-10

- **People** (off until turned on in Settings): faces in photos are found and grouped into
  people, listed in a new People section of the sidebar. Click a person to see their photos;
  name, merge or hide people, and remove wrongly matched photos (Edit ▸ Remove from This Person).
- Everything runs on this computer with two open OpenCV models (YuNet and SFace, about 38 MB,
  downloaded once and checked by SHA-256). New dependency: `opencv-python-headless`.
- Analysis runs in the background after thumbnails are ready, pauses for scans and video, and
  only looks at new or edited photos after the first pass.

## 1.8.0 — 2026-11-04

- A sidebar button at the left of the toolbar hides the whole sidebar and brings it back, in
  step with View ▸ Show Sidebar (⌥⌘S).
- On narrow windows the zoom slider's ▫/◻ marks hide along with the button labels, so the
  toolbar still fits at the smallest window size.

## 1.7.0 — 2026-11-04

- Sidebar sections fold: click Library, Folders, Albums or Hidden Albums to collapse or expand
  it. A folded section shows a › and stays folded after a restart.

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
