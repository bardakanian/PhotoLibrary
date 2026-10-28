"""Entry point for the Photo Library desktop app."""

import logging
import sys

from PySide6.QtCore import QCoreApplication, QLoggingCategory, Qt
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


def main() -> int:
    quiet_qt_logging()
    QCoreApplication.setOrganizationName(APP_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
