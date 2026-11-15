"""Time-left estimates for scans, thumbnails and finding people (core/progress.py)."""

import unittest

from photolibrary.core.progress import Eta, format_duration_words, time_left_text


class EtaTests(unittest.TestCase):
    def test_steady_rate(self):
        eta = Eta(now=0)
        for second in range(1, 11):
            left = eta.remaining(second * 100, 10_000, now=second)
        self.assertAlmostEqual(left, 90, delta=1)  # 9,000 left at 100 a second

    def test_no_guess_too_early(self):
        eta = Eta(now=0)
        self.assertIsNone(eta.remaining(50, 10_000, now=1))  # under 3 seconds in
        self.assertIsNone(Eta(now=0).remaining(10, 10_000, now=10))  # under 1% done

    def test_bursts_and_stalls_dont_swing_the_estimate(self):
        # Fast cached files, then slow ones: progress arrives in bursts with stalls between.
        pattern = [0, 0, 0, 1, 2, 60, 120, 0, 3, 0]
        eta, done, estimates = Eta(now=0), 0, []
        for tick in range(1, 2_000):
            done += pattern[tick % len(pattern)]
            left = eta.remaining(done, 100_000, now=tick / 4)
            if tick % 40 == 0 and tick > 400:  # every 10 s, after the first 100 s
                estimates.append(left)
        jumps = [max(a / b, b / a) for a, b in zip(estimates, estimates[1:])]
        self.assertLess(max(jumps), 1.3, "the estimate should count down, not bounce")

    def test_follows_a_lasting_slowdown(self):
        eta = Eta(now=0)
        for second in range(1, 61):
            eta.remaining(second * 100, 100_000, now=second)  # 100 a second
        before = eta.remaining(6_000, 100_000, now=60)
        for second in range(61, 181):
            later = eta.remaining(6_000 + (second - 60) * 10, 100_000, now=second)  # then 10 a second
        self.assertGreater(later, 3 * before, "two minutes at a tenth of the speed should raise the estimate")

    def test_finished(self):
        self.assertEqual(Eta(now=0).remaining(5, 5, now=1), 0.0)

    def test_wording(self):
        self.assertEqual(format_duration_words(0.4), "1 s")
        self.assertEqual(format_duration_words(45), "45 s")
        self.assertEqual(format_duration_words(200), "3 min")
        self.assertEqual(format_duration_words(4_800), "1 h 20 min")
        self.assertEqual(format_duration_words(7_200), "2 h")
        self.assertEqual(time_left_text(None), "estimating time left…")
        self.assertEqual(time_left_text(2), "almost done")
        self.assertEqual(time_left_text(200), "about 3 min left")
