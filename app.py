"""Entry point for the Photo Library desktop app."""

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLoggingCategory, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from photolibrary.core.constants import APP_DIR, APP_NAME


def configure_logging() -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=APP_DIR / "photo_library.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)


def quiet_qt_logging() -> None:
    """Hide Qt Multimedia's informational chatter (the "Using Qt multimedia with FFmpeg"
    banner and FFmpeg's per-file stream dump). Warnings and errors still come through,
    and QT_LOGGING_RULES can turn the messages back on for debugging."""
    QLoggingCategory.setFilterRules(
        "qt.multimedia.ffmpeg.info=false\n"
        "qt.multimedia.ffmpeg.*.info=false\n"
        "qt.rhi.*.info=false"
    )


def prefer_ui_thread() -> None:
    """Hand the GIL around every 1 ms instead of 5 ms. The scan, library loading and thumbnail
    checks are pure-Python background threads; at 5 ms the UI thread waits behind them so often
    that video stutters (measured: 52 late frames in 10 s, none at 1 ms), while the background
    work is only about 3% slower."""
    sys.setswitchinterval(0.001)


def main() -> int:
    prefer_ui_thread()
    quiet_qt_logging()
    QCoreApplication.setOrganizationName(APP_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    # Written by tools/make_launcher.py on Windows; without it the taskbar shows Python's icon.
    icon = Path(__file__).resolve().parent / "dist" / f"{APP_NAME}.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    configure_logging()

    def report_exception(exc_type, exc_value, exc_traceback):
        logging.getLogger(__name__).exception(
            "Unhandled application error", exc_info=(exc_type, exc_value, exc_traceback)
        )
        QMessageBox.critical(
            None, "Unexpected error",
            f"{APP_NAME} encountered an unexpected problem. Details were written to the application log.",
        )

    sys.excepthook = report_exception
    # Imported after QApplication exists so widget modules can query the platform.
    from photolibrary.ui.main_window import MainWindow
    window = MainWindow(app)
    window.show()
    # Logs the cause of any UI freeze (which also makes video stutter).
    from photolibrary.ui.freeze_monitor import FreezeMonitor
    monitor = FreezeMonitor(app)
    monitor.start()
    app.aboutToQuit.connect(monitor.stop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
