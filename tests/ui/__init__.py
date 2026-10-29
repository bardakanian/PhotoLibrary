"""Headless tests of the real main window (Qt's offscreen platform)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Keep test output readable: hide Qt's informational platform and multimedia messages.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;qt.multimedia.*.info=false;qt.multimedia.ffmpeg=false")
