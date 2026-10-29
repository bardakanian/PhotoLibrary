import os
import time
import unittest

from photolibrary.ui.freeze_monitor import FreezeMonitor

from .base import application


def block_the_ui_thread(seconds: float) -> None:
    time.sleep(seconds)


@unittest.skipIf(os.environ.get("SKIP_UI_TESTS"), "SKIP_UI_TESTS is set")
class FreezeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.app = application()
        self.monitor = FreezeMonitor(threshold=0.08, report_interval=60)
        self.monitor.start()
        self.addCleanup(self.monitor.stop)
        self.pump(0.1)

    def pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def test_quiet_event_loop_reports_nothing(self):
        with self.assertNoLogs("photolibrary.ui.freeze_monitor"):
            self.pump(0.4)
        self.assertEqual(self.monitor.freezes, 0)

    def test_freeze_is_reported_with_the_blocking_code(self):
        with self.assertLogs("photolibrary.ui.freeze_monitor", "WARNING") as logs:
            block_the_ui_thread(0.3)
            self.pump(0.15)
        report, lasted = logs.output[0], logs.output[-1]
        self.assertIn("UI thread not responding", report)
        self.assertIn("MainThread (UI)", report)
        self.assertNotIn("inside Qt", report)  # Python code was running
        self.assertIn("block_the_ui_thread", report)
        self.assertRegex(lasted, r"UI freeze lasted \d+ ms")
        self.assertGreaterEqual(int(lasted.split()[-2]), 250)
        self.assertEqual(self.monitor.freezes, 1)

    def test_frequent_freezes_are_counted_not_each_reported(self):
        with self.assertLogs("photolibrary.ui.freeze_monitor", "WARNING") as logs:
            for _ in range(3):
                block_the_ui_thread(0.15)
                self.pump(0.1)
        reports = [line for line in logs.output if "not responding" in line]
        self.assertEqual(len(reports), 1)  # report_interval is 60 s
        self.assertEqual(self.monitor.freezes, 3)
