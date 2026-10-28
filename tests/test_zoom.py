import unittest

from photolibrary.core.zoom import MAX_SCALE, ZoomState


class ZoomTests(unittest.TestCase):
    def test_fit_centres_large_image(self):
        state = ZoomState.fitted(4000, 3000, 800, 800)
        self.assertAlmostEqual(state.scale, 0.2)
        x, y, width, height = state.target_rect()
        self.assertEqual((round(x), round(y), round(width), round(height)), (0, 100, 800, 600))

    def test_small_images_are_not_enlarged(self):
        state = ZoomState.fitted(200, 100, 800, 600)
        self.assertEqual(state.scale, 1.0)
        self.assertEqual(state.target_rect(), (300, 250, 200, 100))

    def test_zoom_keeps_point_under_cursor(self):
        state = ZoomState.fitted(4000, 3000, 800, 600)
        anchor = (600, 150)
        before = ((anchor[0] - state.target_rect()[0]) / state.scale, (anchor[1] - state.target_rect()[1]) / state.scale)
        zoomed = state.with_scale(1.0, anchor)
        x, y, _, _ = zoomed.target_rect()
        after = ((anchor[0] - x) / zoomed.scale, (anchor[1] - y) / zoomed.scale)
        self.assertAlmostEqual(before[0], after[0], places=6)
        self.assertAlmostEqual(before[1], after[1], places=6)

    def test_scale_limits(self):
        state = ZoomState.fitted(4000, 3000, 800, 600)
        self.assertEqual(state.with_scale(0.01).scale, state.fit_scale)
        self.assertEqual(state.with_scale(100).scale, MAX_SCALE)

    def test_pan_is_clamped_to_image_edges(self):
        state = ZoomState.fitted(4000, 3000, 800, 600).with_scale(1.0)
        moved = state.panned(10_000, 10_000)  # drag far to the bottom-right
        x, y, _, _ = moved.target_rect()
        self.assertEqual((x, y), (0, 0))      # image's top-left corner stops at the view's
        fitted = ZoomState.fitted(4000, 3000, 800, 600)
        self.assertEqual(fitted.panned(50, 50).target_rect(), fitted.target_rect())

    def test_resize_keeps_fit(self):
        state = ZoomState.fitted(4000, 3000, 800, 600).resized(1600, 1200)
        self.assertTrue(state.is_fitted)
        self.assertAlmostEqual(state.scale, 0.4)
        zoomed = ZoomState.fitted(4000, 3000, 800, 600).with_scale(1.0).resized(1000, 700)
        self.assertEqual(zoomed.scale, 1.0)
