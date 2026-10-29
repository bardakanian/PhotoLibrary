from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from photolibrary.core import media

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
        self.window.sidebar.select_scope("all", "2026-1")
        self.wait_until(lambda: len(self.window.model.items) == 12, message="album")
        self.assertTrue(all(name.startswith("JAN_") for name in self.names))
        self.assertEqual([section.title for section in self.window.model.sections], ["January 2026"])

    def test_sort_order_toggle(self):
        self.window.sidebar.select_scope("all", "2026-2")
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
        self.wait_until(lambda: self.window.watcher is not None and self.window.watcher.running, message="watcher")
        self.settle(1.3)  # past the watcher's start-up grace period
        self.write_photo(self.library / "2026-2" / "ARRIVED.jpg", datetime(2026, 2, 20, 8, 0))
        self.wait_until(lambda: "ARRIVED.jpg" in self.names, timeout=20, message="new photo to appear")
