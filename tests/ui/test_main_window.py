import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from photolibrary.core import media
from photolibrary.core.faces import FACES_ENABLED
from photolibrary.ui.widgets.sidebar import CountRole, ScopeRole

from ..fake_faces import FakeFaceEngine, write_people_photo
from .base import WindowTestCase


class GridTests(WindowTestCase):
    def test_library_is_grouped_by_month_newest_first(self):
        self.assertTrue(self.window.grid_page.showing_grid)
        self.assertEqual([section.title for section in self.window.model.sections],
                         ["February 2026", "January 2026", "December 2025"])
        self.assertEqual(self.names[0], "FEB_11.jpg")
        self.assertEqual(self.names[-1], "DEC_0.jpg")

    def test_click_cmd_click_and_shift_click_selection(self):
        self.click_row(1)
        self.assertEqual(self.selected_rows(), [1])
        self.click_row(4, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selected_rows(), [1, 4])
        self.click_row(1)
        self.click_row(5, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.selected_rows(), [1, 2, 3, 4, 5])
        QTest.keyClick(self.view, Qt.Key.Key_Escape)
        self.assertEqual(self.selected_rows(), [])

    def test_info_panel_follows_selection(self):
        self.window.toggle_info()
        self.click_row(0)
        self.window._update_info()
        self.assertEqual(self.window.info_panel.title.text(), "FEB_11.jpg")
        self.click_row(3, Qt.KeyboardModifier.ShiftModifier)
        self.window._update_info()
        self.assertEqual(self.window.info_panel.title.text(), "4 items selected")

    def test_zoom_changes_columns(self):
        before = self.view.grid_layout.columns
        self.window.zoom_slider.setValue(300)
        self.settle(0.1)
        self.assertLess(self.view.grid_layout.columns, before)

    def test_years_and_months_drill_down(self):
        self.window.set_view_mode("years")
        self.assertEqual([self.window.model.summary_at(row).title for row in range(self.window.model.rowCount())],
                         ["2026", "2025"])
        self.window._open_index(self.window.model.index(0, 0))
        self.assertEqual(self.window.model.mode, "months")
        self.window._open_index(self.window.model.index(1, 0))  # January 2026
        self.assertEqual(self.window.model.mode, "all")
        first_january = self.names.index("JAN_11.jpg")
        offset = self.view.verticalOffset()
        visible = self.view.grid_layout.indexes_in(offset, offset + self.view.viewport().height())
        self.assertIn(first_january, visible)


class TitleTests(WindowTestCase):
    def test_title_shows_the_version(self):
        from photolibrary.core.constants import APP_NAME, VERSION
        self.assertEqual(self.window.windowTitle(), f"{APP_NAME} {VERSION}")


class ViewerTests(WindowTestCase):
    def test_open_navigate_and_escape_back_to_same_place(self):
        bar = self.view.verticalScrollBar()
        self.window.zoom_slider.setValue(80)  # small cells so the grid scrolls
        self.settle(0.1)
        bar.setValue(bar.maximum() // 2)
        self.settle(0.1)
        scroll = bar.value()
        row = self.view.anchor()[0] + 1
        self.view.setCurrentIndex(self.window.model.index(row, 0))
        QTest.keyClick(self.view, Qt.Key.Key_Return)
        self.assertTrue(self.window.viewer_active)
        self.assertEqual(self.window.viewer.current_item.name, self.names[row])
        QTest.keyClick(self.window.viewer, Qt.Key.Key_Right)
        self.assertEqual(self.window.viewer.current_item.name, self.names[row + 1])
        QTest.keyClick(self.window.viewer, Qt.Key.Key_Left)
        QTest.keyClick(self.window.viewer, Qt.Key.Key_Escape)
        self.assertFalse(self.window.viewer_active)
        self.assertEqual(bar.value(), scroll)
        self.assertEqual(self.view.currentIndex().row(), row)

    def test_photo_is_decoded_for_the_viewer(self):
        self.window.open_viewer(0)
        self.wait_until(lambda: self.window.viewer.canvas.has_image, message="photo decoded")
        self.assertTrue(self.window.viewer.canvas.is_fitted)
        self.window.viewer.canvas.toggle_actual_size()
        self.assertFalse(self.window.viewer.canvas.is_fitted)

    def test_photo_without_a_cached_thumbnail_is_drawn_once_decoded(self):
        """It used to stay black: "Loading…" dropped the zoom state the decoded photo needs."""
        self.window.loader.cache.clear()
        self.window.open_viewer(3)
        canvas = self.window.viewer.canvas
        self.wait_until(lambda: canvas.has_image, message="photo decoded")
        self.settle(0.05)
        image = canvas.grab().toImage()
        lit = [image.pixelColor(x, y).lightness() > 40
               for x in range(0, image.width(), 9) for y in range(0, image.height(), 9)]
        self.assertTrue(any(lit), "the decoded photo was not drawn")


class TrashTests(WindowTestCase):
    def confirm_trash(self):
        def fake_exec(dialog):
            self.dialog_text = dialog.text()
            dialog._choice = next(button for button in dialog.buttons()
                                  if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole)
            return 0

        return [patch.object(QMessageBox, "exec", fake_exec),
                patch.object(QMessageBox, "clickedButton", lambda dialog: getattr(dialog, "_choice", None))]

    def test_trash_selected_items_keeps_place_and_selects_next(self):
        trashed = []

        def fake_trash(path):
            trashed.append(Path(path).name)
            Path(path).unlink()
            return True

        patches = self.confirm_trash() + [patch("photolibrary.ui.main_window.QFile.moveToTrash", side_effect=fake_trash)]
        for item in patches:
            item.start()
        try:
            self.click_row(2)
            self.click_row(3, Qt.KeyboardModifier.ShiftModifier)
            QTest.keyClick(self.view, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
            self.wait_until(lambda: len(self.window.model.items) == self.expected_total - 2, message="trash")
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(sorted(trashed), ["FEB_08.jpg", "FEB_09.jpg"])
        self.assertIn("2 items", self.dialog_text)
        self.assertEqual(self.view.currentIndex().row(), 2)
        self.assertNotIn("FEB_09.jpg", self.names)

    def test_cancel_keeps_everything(self):
        with patch.object(QMessageBox, "exec", lambda dialog: 0), \
                patch("photolibrary.ui.main_window.QFile.moveToTrash") as trash:
            self.click_row(0)
            self.window.move_to_trash()
            self.settle(0.3)
            trash.assert_not_called()
        self.assertEqual(len(self.window.model.items), self.expected_total)

    def test_delete_keys_in_search_box_edit_text(self):
        with patch.object(QMessageBox, "exec") as dialog:
            self.click_row(0)
            self.window.search_box.setFocus()
            self.window.search_box.setText("abc def")
            QTest.keyClick(self.window.search_box, Qt.Key.Key_Backspace, Qt.KeyboardModifier.ControlModifier)
            dialog.assert_not_called()
        self.assertNotEqual(self.window.search_box.text(), "abc def")


class FilterTests(WindowTestCase):
    def test_search_filters_and_escape_clears(self):
        self.window.focus_search()
        QTest.keyClicks(self.window.search_box, "December 2025")
        self.wait_until(lambda: len(self.window.model.items) == 2, message="search results")
        self.assertEqual(set(self.names), {"DEC_0.jpg", "DEC_1.jpg"})
        QTest.keyClick(self.window.search_box, Qt.Key.Key_Escape)
        self.wait_until(lambda: len(self.window.model.items) == self.expected_total, message="cleared search")

    def test_no_results_state(self):
        self.window.search_box.setText("nothing-like-this")
        self.window._apply_search()
        self.wait_until(lambda: not self.window.grid_page.showing_grid, message="empty state")
        self.assertIn("No results", self.window.grid_page.empty.title_label.text())

    def test_album_scope(self):
        self.window.sidebar.select_album("2026-1")
        self.wait_until(lambda: len(self.window.model.items) == 12, message="album")
        self.assertTrue(all(name.startswith("JAN_") for name in self.names))
        self.assertEqual([section.title for section in self.window.model.sections], ["January 2026"])

    def test_sort_order_toggle(self):
        self.window.resize(1600, 800)  # full toolbar labels even with the offscreen platform's wide glyphs
        self.settle(0.1)
        self.window.sidebar.select_album("2026-2")
        self.wait_until(lambda: len(self.window.model.items) == 12, message="album")
        self.assertEqual(self.names[0], "FEB_11.jpg")
        self.window.sort_button.click()
        self.wait_until(lambda: self.names[0] == "FEB_00.jpg", message="oldest first")
        self.assertEqual(self.view.verticalScrollBar().value(), 0)
        self.assertEqual(self.window.sort_button.text().strip(), "↑  Oldest First".strip())

    def test_insights_page(self):
        self.window.show_insights()
        self.wait_until(lambda: self.window.insights.states.currentWidget() is self.window.insights.content,
                        message="insights")
        self.assertEqual(self.window.insights.cards["total"].value.text(), "26")
        self.assertEqual(self.window.insights.cards["year"].value.text(), "2026")
        QTest.keyClick(self.window.insights, Qt.Key.Key_Escape)
        self.assertFalse(self.window.insights_active)


class LivePhotoTests(WindowTestCase):
    def build_extra_library(self):
        if not media.FFMPEG_ENABLED:
            self.skipTest("ffmpeg not installed")
        from ..test_live_photos import make_clip
        from datetime import datetime
        self.write_photo(self.library / "2026-2" / "IMG_4330.JPG", datetime(2026, 2, 28, 12, 0, 0))
        make_clip(self.library / "2026-2" / "IMG_4330.MOV", 1.2, "2026-02-28T12:00:00Z", live_tag=True)
        self.expected_total += 1  # the clip folds into its photo

    def test_live_photo_shows_once_with_badge_and_plays(self):
        photo = next(item for item in self.window.model.items if item.name == "IMG_4330.JPG")
        self.assertTrue(photo.is_live)
        self.assertNotIn("IMG_4330.MOV", self.names)
        self.window.open_viewer(self.names.index("IMG_4330.JPG"))
        self.assertTrue(self.window.viewer.live_button.isVisible())
        self.window.viewer.play_live()
        self.assertIs(self.window.viewer.content.currentWidget(), self.window.viewer.video)
        self.wait_until(lambda: self.window.viewer.content.currentWidget() is self.window.viewer.canvas,
                        timeout=10, message="back to the still")


class WatchingTests(WindowTestCase):
    watch_folder = True

    def test_new_photo_appears_without_rescan(self):
        from datetime import datetime
        from photolibrary.core import watcher
        if not watcher.WATCHDOG_ENABLED:
            self.skipTest("watchdog not installed")
        self.wait_until(lambda: self.window.watchers and self.window.watchers[0].running, message="watcher")
        self.settle(1.3)  # past the watcher's start-up grace period
        self.write_photo(self.library / "2026-2" / "ARRIVED.jpg", datetime(2026, 2, 20, 8, 0))
        self.wait_until(lambda: "ARRIVED.jpg" in self.names, timeout=20, message="new photo to appear")


class MultiFolderTests(WindowTestCase):
    def names_now(self):
        return sorted(item.name for item in self.window.model.items)

    def test_add_browse_and_remove_a_second_folder(self):
        from datetime import datetime
        drive = self.make_library("Drive")
        self.write_photo(drive / "2026-1" / "DRIVE_JAN.jpg", datetime(2026, 1, 20, 12))
        self.write_photo(drive / "Family" / "DRIVE_FAM.jpg", datetime(2024, 6, 1, 12))
        window = self.window

        # Overlapping folders are refused with an explanation.
        with patch("photolibrary.ui.main_window.QMessageBox.information") as told:
            self.assertFalse(window.add_library_folder(self.library / "2026-1"))
            told.assert_called_once()

        self.assertTrue(window.add_library_folder(drive))
        self.wait_until(lambda: len(window.model.items) == 28 and not window.scanning, timeout=20,
                        message="second folder indexed")
        sidebar = window.sidebar
        self.wait_until(lambda: sidebar.folders_header.rowCount() == 2, message="Folders section")
        self.assertEqual(window.folder_status.toolTip().splitlines(), [str(self.library), str(drive.resolve())])

        # Both folders have a "2026-1" album; they are listed separately and named by folder.
        labels = [sidebar.albums_header.child(row).text() for row in range(sidebar.albums_header.rowCount())]
        self.assertIn("2026-1 — Drive", labels)
        self.assertIn("Family", labels)

        sidebar.select_scope("all", str(drive.resolve()))
        self.wait_until(lambda: self.names_now() == ["DRIVE_FAM.jpg", "DRIVE_JAN.jpg"], message="folder scope")
        sidebar.select_album("2026-1", str(drive.resolve()))
        self.wait_until(lambda: self.names_now() == ["DRIVE_JAN.jpg"], message="drive album")

        window.remove_folder(drive.resolve(), confirm=False)
        self.wait_until(lambda: len(window.model.items) == self.expected_total and sidebar.scope == ("all", None, None),
                        timeout=20, message="folder removed")
        self.wait_until(lambda: sidebar.folders_header.model() is None, message="Folders section hidden")
        self.assertEqual(window.settings.library_folders, [self.library.resolve()])
        self.assertTrue((drive / "Family" / "DRIVE_FAM.jpg").exists())
        self.wait_until(lambda: json.loads(self.config_file.read_text()).get("library_folders")
                        == [str(self.library.resolve())], message="settings saved")


class PaintingTests(WindowTestCase):
    def test_painters_are_ended_even_if_the_frame_lives_on(self):
        """A paint frame kept alive (as a stack snapshot can) must not leave its QPainter open."""
        import sys
        seen = []

        def profile(frame, event, _arg):
            if event == "return" and frame.f_code.co_name == "paintEvent" and "painter" in frame.f_locals:
                seen.append(frame.f_locals["painter"].isActive())

        sys.setprofile(profile)
        try:
            self.window.view.viewport().repaint()
            self.window.open_current() if self.window.view.currentIndex().isValid() else None
        finally:
            sys.setprofile(None)
        self.assertTrue(seen, "no paintEvent ran")
        self.assertFalse(any(seen), "a painter was still active when paintEvent returned")


class HiddenAlbumWindowTests(WindowTestCase):
    def test_hide_and_show_an_album(self):
        window, sidebar = self.window, self.window.sidebar
        root = str(self.library.resolve())
        window.set_album_hidden(root, "2026-2", True)
        self.wait_until(lambda: len(window.model.items) == self.expected_total - 12, message="album hidden")
        self.assertFalse(any(name.startswith("FEB_") for name in self.names))
        self.wait_until(lambda: sidebar.hidden_header.rowCount() == 1, message="Hidden Albums section")
        self.assertEqual(sidebar._library_items["all"].data(CountRole), self.expected_total - 12)
        self.assertTrue(sidebar.is_hidden(root, "2026-2"))

        # The status line counts what All Photos shows, and says how many are hidden.
        shown = self.expected_total - 12
        self.wait_until(lambda: window.ready_text.startswith(f"Library ready  ·  {shown} items (12 hidden)"),
                        message="ready text before rescan")
        self.assertIn("Hid “2026-2”", window.status.text())  # the newer message isn't overwritten
        window.start_indexing()
        self.wait_until(lambda: not window.scanning and window.status.text().startswith(
            f"Library ready  ·  {shown} items (12 hidden)"), timeout=20, message="ready text after rescan")

        # The hidden album still opens from the sidebar.
        sidebar.select_album("2026-2")
        self.wait_until(lambda: len(window.model.items) == 12, message="hidden album opened")
        sidebar.select_scope("all")
        self.wait_until(lambda: len(window.model.items) == self.expected_total - 12, message="back to All Photos")

        window.set_album_hidden(root, "2026-2", False)
        self.wait_until(lambda: len(window.model.items) == self.expected_total, message="album shown again")
        self.wait_until(lambda: sidebar.hidden_header.model() is None, message="Hidden Albums section gone")
        self.wait_until(lambda: window.ready_text.startswith(f"Library ready  ·  {self.expected_total} items  ·")
                        or window.ready_text == f"Library ready  ·  {self.expected_total} items",
                        message="ready text without hidden albums")
        self.wait_until(lambda: json.loads(self.config_file.read_text()).get("hidden_albums") == [],
                        message="settings saved")


class ToolbarFitTests(WindowTestCase):
    def test_view_buttons_are_never_squeezed(self):
        window = self.window
        # The compact toolbar's width depends on the font: Qt's offscreen platform on Windows has no
        # real fonts and draws placeholder glyphs, which need about 1,060 px.
        narrowest = max(900, window.compact_toolbar_width())
        for width in sorted({narrowest, max(narrowest, 1000), max(narrowest, 1200), 1600}):
            window.resize(width, 800)
            self.settle(0.1)
            for mode, button in window.mode_buttons.items():
                self.assertGreaterEqual(button.width(), button.sizeHint().width(), f"{mode} at {width} px")
            self.assertLessEqual(window.toolbar.layout().minimumSize().width(), window.width())
        # With room to spare the labels come back.
        self.assertEqual(window.rescan_button.text(), "Rescan")
        window.resize(narrowest, 800)
        self.settle(0.1)
        self.assertEqual(window.rescan_button.text(), "")
        window.set_sort_order("oldest")
        self.assertEqual(window.sort_button.text(), "↑")


class ScrollbarBounceTests(WindowTestCase):
    """At some sizes the scrollbar's own width made everything fit, so it hid, then came back, forever."""

    def build_extra_library(self):
        for index in range(129):
            self.write_photo(self.library / "2026-9" / f"SEP_{index:03d}.jpg",
                             datetime(2026, 9, 1) + timedelta(hours=index))
        self.expected_total = 26 + 129

    def test_scrollbar_settles_when_it_alone_decides_whether_things_fit(self):
        from photolibrary.core.grid_layout import GridLayout
        from photolibrary.ui.grid_view import MODE_GEOMETRY
        window, view = self.window, self.window.view
        window.zoom_slider.setValue(80)
        window.sidebar.select_album("2026-9")
        self.wait_until(lambda: len(window.model.items) == 129, message="album shown")
        bar = view.verticalScrollBar()
        full = view.contentsRect().width()
        low, high = (GridLayout([129], width, 80, **MODE_GEOMETRY["all"]).total_height
                     for width in (full - bar.sizeHint().width(), full))
        self.assertLess(low, high)
        # A height where the grid fits with the scrollbar but not without it.
        window.resize(window.width(), window.height() + (low + high) // 2 - view.viewport().height())
        self.settle(0.1)
        flips = []
        bar.rangeChanged.connect(lambda *_: flips.append(bar.isVisible()))
        visible = []
        for _ in range(10):
            self.settle(0.02)
            visible.append(bar.isVisible())
        self.assertEqual(len(set(visible)), 1, "the scrollbar kept appearing and disappearing")
        self.assertEqual(flips, [])


class ShiftClickTests(WindowTestCase):
    def test_shift_click_range_starts_at_the_clicked_photo_after_zooming(self):
        self.window.zoom_slider.setValue(200)
        self.settle(0.1)
        self.click_row(2)
        self.window.zoom_slider.setValue(80)  # every photo moves on screen
        self.settle(0.1)
        self.click_row(6, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.selected_rows(), [2, 3, 4, 5, 6])

    def test_shift_click_after_a_resize(self):
        self.window.zoom_slider.setValue(120)
        self.click_row(9)
        self.window.resize(900, 700)
        self.settle(0.1)
        self.click_row(11, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.selected_rows(), [9, 10, 11])


class SidebarSectionTests(WindowTestCase):
    def click_header(self, header):
        tree = self.window.sidebar.tree
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         tree.visualRect(header.index()).center())
        self.settle(0.05)

    def test_clicking_a_section_header_folds_it_and_the_choice_is_remembered(self):
        sidebar = self.window.sidebar
        albums, tree = sidebar.albums_header, sidebar.tree
        first_album = albums.child(0).index()
        self.assertFalse(tree.visualRect(first_album).isEmpty())
        self.click_header(albums)
        self.assertFalse(tree.isExpanded(albums.index()))
        self.assertTrue(tree.visualRect(first_album).isEmpty(), "albums still listed")
        self.wait_until(lambda: json.loads(self.config_file.read_text()).get("collapsed_sections") == ["albums"],
                        message="collapsed section saved")
        # Reloading the album list (after a scan) keeps it folded.
        self.window.reload_sidebar()
        self.settle(0.3)
        self.assertFalse(tree.isExpanded(albums.index()))
        self.click_header(albums)
        self.assertTrue(tree.isExpanded(albums.index()))
        self.wait_until(lambda: json.loads(self.config_file.read_text()).get("collapsed_sections") == [],
                        message="expanded section saved")

    def test_folding_the_section_with_the_open_album_keeps_showing_it(self):
        sidebar = self.window.sidebar
        sidebar.select_album("2026-2")
        self.wait_until(lambda: len(self.window.model.items) == 12, message="album shown")
        self.click_header(sidebar.albums_header)
        self.click_header(sidebar.albums_header)
        self.assertEqual(sidebar.scope, ("all", sidebar.albums_header.child(1).data(ScopeRole)[1], "2026-2"))
        self.assertEqual(len(self.window.model.items), 12)
        self.assertEqual(sidebar.tree.currentIndex().data(ScopeRole)[2], "2026-2")

    def test_saved_sections_start_folded(self):
        from photolibrary.ui.widgets.sidebar import Sidebar
        sidebar = Sidebar(self.window.loader, self.window.theme)
        sidebar.set_collapsed(["library", "bogus"])
        self.assertEqual(sidebar.collapsed, {"library"})
        self.assertFalse(sidebar.tree.isExpanded(sidebar.library_header.index()))
        self.assertTrue(sidebar.tree.isExpanded(sidebar.albums_header.index()))
        sidebar.deleteLater()


class SidebarButtonTests(WindowTestCase):
    def test_toolbar_button_hides_and_shows_the_sidebar_and_stays_in_step_with_the_menu(self):
        window = self.window
        button = window.sidebar_button
        self.assertTrue(window.sidebar.isVisible() and button.isChecked())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.settle(0.05)
        self.assertFalse(window.sidebar.isVisible())
        self.assertFalse(window.sidebar_action.isChecked())
        self.assertIn("Show the sidebar", button.toolTip())
        self.wait_until(lambda: json.loads(self.config_file.read_text()).get("sidebar_visible") is False,
                        message="hidden sidebar saved")
        window.sidebar_action.trigger()  # the View menu / ⌥⌘S
        self.settle(0.05)
        self.assertTrue(window.sidebar.isVisible() and button.isChecked())
        self.assertIn("Hide the sidebar", button.toolTip())
        self.assertLessEqual(window.compact_toolbar_width(), 900 if not sys.platform.startswith("win") else 1100)


@unittest.skipUnless(FACES_ENABLED, "OpenCV is not installed")
class PeopleWindowTests(WindowTestCase):
    def build_extra_library(self):
        start = datetime(2026, 3, 1, 12)
        for index in range(7):
            write_people_photo(self.library / "Family" / f"ana_{index}.jpg", start + timedelta(days=index), (1,))
        for index in range(5):
            write_people_photo(self.library / "Family" / f"ben_{index}.jpg", start + timedelta(days=10 + index), (2,))
        self.expected_total = 26 + 12

    def find_people(self, fold: bool = False):
        window = self.window
        if not fold:  # these test people are in fewer photos than the sidebar folds away
            from photolibrary.ui.widgets import sidebar
            folding = patch.object(sidebar, "FEW_PHOTOS", 0)
            folding.start()
            self.addCleanup(folding.stop)
        window.face_engine_factory = lambda models: FakeFaceEngine()
        window.settings.find_people = True
        window.start_people()
        self.wait_until(lambda: window.sidebar.unnamed_header.rowCount() == (1 if fold else 2), timeout=20,
                        message="people listed")
        return window.sidebar.unnamed_header  # nobody is named yet

    def test_people_in_few_photos_are_folded_until_asked_for(self):
        from photolibrary.ui.widgets.sidebar import MoreRole
        header = self.find_people(fold=True)
        tree = self.window.sidebar.tree
        more = header.child(0)
        self.assertTrue(more.data(MoreRole))
        self.assertEqual(more.text(), "Show 2 More…")
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         tree.visualRect(more.index()).center())
        self.assertEqual([header.child(row).data(CountRole) for row in range(2)], [7, 5])
        self.assertEqual(header.child(2).text(), "Show Fewer")
        ana = header.child(0).data(ScopeRole)[1]
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         tree.visualRect(header.child(2).index()).center())
        # A named person is always listed, however few their photos.
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Ana", True)):
            self.window.rename_person(ana)
        named = self.window.sidebar.people_header
        self.wait_until(lambda: named.rowCount() == 1 and named.child(0).text() == "Ana"
                        and header.rowCount() == 1 and header.child(0).text() == "Show 1 More…",
                        message="named person under People, the folded one under Unnamed People")

    def test_people_is_off_until_turned_on(self):
        self.assertFalse(self.window.settings.find_people)
        self.window.start_people()
        self.settle(0.3)
        self.assertIsNone(self.window.sidebar.people_header.model())

    def test_people_are_listed_and_open_their_photos(self):
        header = self.find_people()
        self.assertEqual([header.child(row).data(CountRole) for row in range(2)], [7, 5])
        # Face pictures are cut from the largest grid thumbnail, so they stay sharp.
        from photolibrary.ui.widgets.sidebar import FACE_BUCKET, CoverRole
        cover = header.child(0).data(CoverRole)
        self.window.sidebar.tree.viewport().repaint()
        self.wait_until(lambda: self.window.loader.has_exact(cover, FACE_BUCKET), message="sharp face picture")
        self.assertEqual(header.child(0).text(), "Unnamed person")
        self.assertEqual(header.row(), self.window.sidebar.albums_header.row() - 1)  # just above Albums
        self.assertIsNone(self.window.sidebar.people_header.model())  # no named people yet
        person = header.child(0).data(ScopeRole)[1]
        self.window.sidebar.select_scope("person", None, person)
        self.wait_until(lambda: len(self.window.model.items) == 7, message="person's photos")
        self.assertTrue(all(item.name.startswith("ana_") for item in self.window.model.items))
        self.assertTrue(self.window.remove_from_person_action.isEnabled())
        # Remove one photo from the person.
        self.click_row(0)
        self.window.remove_from_person()
        self.wait_until(lambda: len(self.window.model.items) == 6, message="photo removed from person")

    def test_rename_merge_and_hide(self):
        header = self.find_people()
        ana, ben = (header.child(row).data(ScopeRole)[1] for row in range(2))
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Ana", True)):
            self.window.rename_person(ana)
        named = self.window.sidebar.people_header
        self.wait_until(lambda: named.rowCount() == 1 and named.child(0).text() == "Ana", message="renamed")
        self.assertEqual(header.rowCount(), 1)  # Ben, still unnamed
        self.window.set_person_hidden(ben, True)
        self.wait_until(lambda: self.window.sidebar.hidden_people_header.rowCount() == 1, message="hidden")
        self.assertIsNone(header.model())  # no unnamed people shown: the section goes
        with patch.object(QMessageBox, "exec", lambda dialog: dialog.buttons()[0].click()):
            self.window.merge_people(ana, ben)
        self.wait_until(lambda: named.rowCount() == 1 and named.child(0).data(CountRole) == 12
                        and self.window.sidebar.hidden_people_header.model() is None, message="merged")
        self.assertEqual(named.child(0).text(), "Ana")

    def loosen_faces(self, person_id: int, count: int) -> list[int]:
        from photolibrary.core import database
        connection = database.open_database()
        try:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM faces WHERE person_id = ? ORDER BY id LIMIT ?", (person_id, count))]
            connection.executemany("UPDATE faces SET person_id = NULL WHERE id = ?", [(face_id,) for face_id in ids])
            connection.commit()
        finally:
            connection.close()
        return ids

    def face_rows(self, face_ids):
        from photolibrary.core import database
        connection = database.open_database()
        try:
            return {face_id: connection.execute("SELECT person_id, pinned FROM faces WHERE id = ?", (face_id,)).fetchone()
                    for face_id in face_ids}
        finally:
            connection.close()

    def test_review_asks_about_loose_faces_and_keeps_the_answers(self):
        from photolibrary.ui.widgets.sidebar import ReviewRole
        header = self.find_people()
        ana = header.child(0).data(ScopeRole)[1]
        self.loosen_faces(ana, 3)
        self.window.reload_sidebar()
        header = self.window.sidebar.people_header  # Review Suggestions sits at the top of People
        self.settle()
        self.assertIsNone(header.model())  # nobody named yet, so nothing to suggest
        self.window.repository.rename_person(ana, "Ana")
        self.window.reload_sidebar()
        self.wait_until(lambda: header.rowCount() and header.child(0).data(ReviewRole)
                        and header.child(0).data(CountRole) == 3, message="review row with 3 suggestions")
        # Clicking the row opens the page, without changing what the grid shows.
        scope = self.window.sidebar.scope
        tree = self.window.sidebar.tree
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         tree.visualRect(header.child(0).index()).center())
        review = self.window.review
        self.wait_until(lambda: self.window.review_active and len(review.suggestions) == 3, message="suggestions")
        self.assertEqual(self.window.sidebar.scope, scope)
        self.assertIn("Is this", review.question.text())
        first, second = review.suggestions[0].face_id, review.suggestions[1].face_id
        QTest.keyClick(review, Qt.Key.Key_Y)
        QTest.keyClick(review, Qt.Key.Key_N)
        QTest.keyClick(review, Qt.Key.Key_Right)  # skip the third
        self.assertEqual(review.empty.title_label.text(), "All caught up")
        self.wait_until(lambda: self.face_rows([first, second]) == {first: (ana, 1), second: (None, 0)},
                        message="answers saved")
        QTest.keyClick(review, Qt.Key.Key_Escape)
        self.assertFalse(self.window.review_active)
        # The rejected face isn't suggested again; the skipped one still is.
        self.wait_until(lambda: header.child(0).data(ReviewRole) and header.child(0).data(CountRole) == 1,
                        message="one suggestion left")

    def test_viewer_shows_faces_and_fixes_one_face(self):
        header = self.find_people()
        ana = header.child(0).data(ScopeRole)[1]
        # From All Photos, so the photo stays on screen after its face leaves the person.
        row = next(row for row, item in enumerate(self.window.model.items) if item.name == "ana_6.jpg")
        viewer = self.window.viewer
        self.window.open_viewer(row)
        self.assertTrue(viewer.faces_button.isVisible())
        viewer.faces_button.setChecked(True)
        self.wait_until(lambda: len(viewer.faces) == 1 and viewer.canvas.face_rects(), message="face shown")
        face = viewer.faces[0]
        self.assertEqual(face.person_id, ana)
        rect = viewer.canvas.face_rects()[0]
        self.assertTrue(viewer.canvas.rect().contains(rect.center().toPoint()))
        shown = []

        def capture(menu, *args):
            shown.append([action.text() for action in menu.actions()])
            next(action for action in menu.actions() if action.text().startswith("Not ")).trigger()

        with patch.object(self.window, "_exec_menu", capture):
            QTest.mouseClick(viewer.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             rect.center().toPoint())
        self.assertIn("New Person…", shown[0])
        self.wait_until(lambda: viewer.faces and viewer.faces[0].person_id is None, message="face taken out")
        self.assertIsNone(self.face_rows([face.id])[face.id][0])
        # Name it as somebody new instead.
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Dana", True)):
            self.window._name_face(viewer.faces[0])
        self.wait_until(lambda: viewer.faces and viewer.faces[0].name == "Dana" and viewer.faces[0].confirmed,
                        message="named Dana")
        # Turning faces off clears them; another photo loads its own.
        viewer.faces_button.setChecked(False)
        self.assertEqual(viewer.canvas.face_rects(), [])

    def test_check_faces_puts_the_least_typical_first(self):
        import numpy as np
        from photolibrary.core import database
        from ..fake_faces import person_vector
        header = self.find_people()
        ana = header.child(0).data(ScopeRole)[1]
        connection = database.open_database()
        try:  # one of Ana's faces looks a little different
            face_id, blob, odd = connection.execute(
                "SELECT faces.id, faces.embedding, media.file_path FROM faces JOIN media ON media.id = faces.media_id "
                "WHERE faces.person_id = ? AND media.file_name = 'ana_2.jpg'", (ana,)).fetchone()
            vector = np.frombuffer(blob, dtype=np.float32) + person_vector(40) * 0.8
            connection.execute("UPDATE faces SET embedding = ? WHERE id = ?",
                               ((vector / np.linalg.norm(vector)).astype(np.float32).tobytes(), face_id))
            connection.commit()
        finally:
            connection.close()
        window = self.window
        window.check_person(ana)
        self.wait_until(lambda: window.model.items and window.model.items[0].name == "ana_2.jpg"
                        and len(window.model.items) == 7, message="least typical first")
        self.assertEqual([section.title for section in window.model.sections],
                         ["Least typical first  ·  wrong matches gather here"])
        self.assertTrue(window.least_typical_action.isChecked())
        window.set_view_mode("months")  # Years and Months are by date: the check turns off
        self.assertFalse(window.least_typical)
        window.set_view_mode("all")
        self.wait_until(lambda: window.model.sections and "Least" not in window.model.sections[0].title,
                        message="back to months")
        window.check_person(ana)
        self.wait_until(lambda: window.least_typical and window.model.items[0].name == "ana_2.jpg", message="on again")
        window.sidebar.select_scope("all", None, None)  # leaving the person turns it off
        self.assertFalse(window.least_typical or window.least_typical_action.isEnabled())

    def test_removing_a_wrong_photo_from_a_person(self):
        header = self.find_people()
        ana = header.child(0).data(ScopeRole)[1]
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Ana", True)):
            self.window.rename_person(ana)
        named = self.window.sidebar.people_header
        self.wait_until(lambda: named.rowCount() and named.child(0).text() == "Ana", message="named")
        self.window.sidebar.select_scope("person", None, ana)
        self.wait_until(lambda: len(self.window.model.items) == 7, message="Ana's photos")
        self.assertEqual(self.window.remove_from_person_action.text(), "Remove from Ana")
        self.assertEqual(self.window.remove_from_person_action.shortcut().toString(), QKeySequence("Alt+Backspace").toString())
        # Right-click two selected photos: "Not Ana – Remove 2 Photos from Ana".
        self.click_row(0)
        self.click_row(1, Qt.KeyboardModifier.ControlModifier)
        wrong = {item.id for item in self.window.selected_items()}
        shown = []

        def choose(menu, *args):
            shown.append([action.text() for action in menu.actions()])
            next(action for action in menu.actions() if action.text().startswith("Not Ana")).trigger()

        with patch.object(self.window, "_exec_menu", choose):
            self.window._grid_context_menu(self.view.visualRect(self.window.model.index(0, 0)).center())
        self.assertIn("Not Ana – Remove 2 Photos from Ana", shown[0])
        self.wait_until(lambda: len(self.window.model.items) == 5, message="two photos removed")
        self.assertFalse(wrong & {item.id for item in self.window.model.items})
        self.assertEqual(self.window.status.text(), "Removed 2 photos from Ana")
        # In the viewer, the shortcut removes the photo being viewed.
        self.window.open_viewer(0)
        viewing = self.window.viewer.current_item.id
        self.assertEqual(self.window.scope_kind, "person")  # a sidebar reload used to reset this to All Photos
        self.window.remove_from_person_action.trigger()
        self.wait_until(lambda: viewing not in {item.id for item in self.window.model.items}, message="viewer photo removed")

    def test_unnamed_people_fold_away_on_their_own(self):
        unnamed = self.find_people()
        ana = unnamed.child(0).data(ScopeRole)[1]
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Ana", True)):
            self.window.rename_person(ana)
        named = self.window.sidebar.people_header
        self.wait_until(lambda: named.rowCount() == 1, message="Ana under People")
        tree = self.window.sidebar.tree
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         tree.visualRect(unnamed.index()).center())
        self.assertFalse(tree.isExpanded(unnamed.index()))
        self.assertTrue(tree.isExpanded(named.index()))  # named people stay in view
        self.wait_until(lambda: "unnamed" in json.loads(self.config_file.read_text()).get("collapsed_sections", []),
                        message="folded section saved")
        self.window.reload_sidebar()
        self.settle(0.3)
        self.assertFalse(tree.isExpanded(unnamed.index()))


@unittest.skipUnless(FACES_ENABLED, "OpenCV is not installed")
class PeopleGroupWindowTests(WindowTestCase):
    def build_extra_library(self):
        start = datetime(2026, 4, 1, 12)
        for index, people in enumerate([(1,)] * 5 + [(2,)] * 4 + [(1, 2)] * 3):
            write_people_photo(self.library / "Family" / f"g{index:02d}.jpg", start + timedelta(days=index), people)
        self.expected_total = 26 + 12

    def setUp(self):
        super().setUp()
        from photolibrary.ui.widgets import sidebar
        folding = patch.object(sidebar, "FEW_PHOTOS", 0)
        folding.start()
        self.addCleanup(folding.stop)
        window = self.window
        window.face_engine_factory = lambda models: FakeFaceEngine()
        window.settings.find_people = True
        window.new_group_action.setEnabled(True)
        window.start_people()
        self.wait_until(lambda: window.sidebar.unnamed_header.rowCount() == 2, timeout=20, message="people listed")
        self.first, self.second = (window.sidebar.unnamed_header.child(row).data(ScopeRole)[1] for row in range(2))

    def test_a_group_shows_the_photos_of_everyone_together(self):
        window, groups = self.window, self.window.sidebar.groups_header
        self.assertIsNone(groups.model())  # no Groups section until there is a group
        with patch.object(window, "_open_group_dialog", return_value=("Family", [self.first, self.second])):
            window.new_group([self.first])
        self.wait_until(lambda: groups.rowCount() == 1 and window.scope_kind == "group", message="group created and shown")
        self.assertEqual(groups.row(), window.sidebar.unnamed_header.row() + 1)  # right after the people
        self.assertEqual((groups.child(0).text(), groups.child(0).data(CountRole)), ("Family", 3))
        self.wait_until(lambda: len(window.model.items) == 3, message="photos together")
        self.assertTrue(all(item.name in {"g09.jpg", "g10.jpg", "g11.jpg"} for item in window.model.items))
        self.assertEqual(window.folder_status.text().split("  ·  ")[0], "Family")
        # Rename, then take a member out: a group of one person shows all their photos.
        with patch("photolibrary.ui.main_window.QInputDialog.getText", return_value=("Us", True)):
            window.rename_group(groups.child(0).data(ScopeRole)[1])
        self.wait_until(lambda: groups.rowCount() and groups.child(0).text() == "Us", message="renamed")
        group = groups.child(0).data(ScopeRole)[1]
        with patch.object(window, "_open_group_dialog", return_value=("Us", [self.first])):
            window.edit_group(group)
        self.wait_until(lambda: len(window.model.items) == 8, message="one member's photos")
        self.assertEqual(window.scope_kind, "group")  # a sidebar refresh keeps the group selected
        def confirm(dialog):
            next(button for button in dialog.buttons() if button.text() == "Delete Group").click()

        with patch.object(QMessageBox, "exec", confirm):
            window.delete_group(group)
        self.wait_until(lambda: groups.model() is None and window.scope_kind == "all", message="deleted")
        self.assertEqual(window.sidebar.unnamed_header.rowCount(), 2)  # people untouched

    def test_the_dialog_needs_a_name_and_two_people(self):
        from PySide6.QtWidgets import QDialogButtonBox
        from photolibrary.ui.dialogs import GroupDialog
        dialog = GroupDialog(self.window.people_list, selected=[self.first])
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.assertFalse(save.isEnabled())
        dialog.name.setText("Family")
        self.assertFalse(save.isEnabled())  # still only one person
        for row in range(dialog.list.count()):
            dialog.list.item(row).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(save.isEnabled())
        self.assertEqual(sorted(dialog.selected_members), sorted([self.first, self.second]))
        dialog.deleteLater()



class FullPageTests(WindowTestCase):
    def test_insights_and_review_keep_the_grid_controls_away(self):
        window = self.window
        window.view.selectAll()
        window.show_insights()
        self.assertEqual(window.target_items(), [])  # ⌘⌫ can't trash a selection that isn't on screen
        window.toggle_sidebar()
        window.toggle_info()
        self.assertFalse(window.sidebar.isVisible())
        self.assertFalse(window.info_panel.isVisible())
        window.close_insights()
        self.assertFalse(window.sidebar.isVisible())  # the sidebar setting was turned off meanwhile
        self.assertTrue(window.info_panel.isVisible())
        window.toggle_sidebar()
        self.assertTrue(window.sidebar.isVisible())

    def test_open_viewer_is_ignored_on_the_review_page(self):
        window = self.window
        window.settings.find_people = True
        window.view.setCurrentIndex(window.model.index(0, 0))
        window.show_review()
        window.open_current()
        self.assertTrue(window.review_active)

    def test_closing_the_viewer_brings_the_info_panel_back(self):
        window = self.window
        window.toggle_info()
        window.info_panel.hide()  # as the Review page leaves it
        window.open_viewer(0)
        window.close_viewer()
        self.assertTrue(window.info_panel.isVisible())


class QueryTaskTests(WindowTestCase):
    def test_queries_dropped_from_the_queue_are_not_kept_alive(self):
        from photolibrary.ui import tasks
        window = self.window
        for _ in range(20):
            window.reload_library()
        self.wait_until(lambda: window.applied_request_id == window.query_request_id, message="last query applied")
        self.settle()
        self.assertFalse([task for task in tasks._active if task.pool is window.query_pool])


class ThumbnailRetryTests(WindowTestCase):
    def test_a_failed_thumbnail_is_tried_again_later(self):
        from photolibrary.ui import thumbnails
        loader, item = self.window.loader, self.window.model.items[0]
        loader._failed[(item.id, item.modified_ns)] = thumbnails.time.monotonic()
        self.assertTrue(loader.is_unavailable(item))
        loader._failed[(item.id, item.modified_ns)] -= thumbnails.FAILED_RETRY_SECONDS + 1
        self.assertFalse(loader.is_unavailable(item))  # the drive may be back: worth another try

    def test_a_damaged_cached_thumbnail_is_made_again(self):
        from photolibrary.ui.thumbnails import THUMBNAIL_EDGE
        loader, item = self.window.loader, self.window.model.items[0]
        cached = media.get_cached_thumbnail(item.path, "image", item.file_size, item.modified_ns, THUMBNAIL_EDGE)
        cached.write_bytes(b"\xff\xd8 cut short")
        loader.reset()
        loader.request(item, 96)
        self.wait_until(lambda: loader.has_exact(item, 96) or loader.is_unavailable(item), message="loaded")
        self.assertTrue(loader.has_exact(item, 96))
