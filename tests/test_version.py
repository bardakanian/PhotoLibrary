import re
import unittest
from pathlib import Path

from photolibrary.core.constants import VERSION

PROJECT = Path(__file__).resolve().parent.parent
CHANGELOG = PROJECT / "CHANGELOG.md"


class VersionTests(unittest.TestCase):
    def test_version_is_semantic(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_changelog_has_an_entry_for_the_current_version(self):
        headings = re.findall(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$", CHANGELOG.read_text(encoding="utf-8"),
                              re.MULTILINE)
        self.assertTrue(headings, "CHANGELOG.md has no version headings")
        self.assertEqual(headings[0], VERSION, "bump VERSION and add a CHANGELOG.md entry together")

    def test_everything_reads_the_one_constant(self):
        self.assertIn("app.setApplicationVersion(VERSION)", (PROJECT / "app.py").read_text(encoding="utf-8"))
        self.assertIn('"CFBundleShortVersionString": VERSION', (PROJECT / "tools" / "make_launcher.py").read_text(encoding="utf-8"))
        sources = [PROJECT / "app.py", *(PROJECT / "photolibrary").rglob("*.py"), *(PROJECT / "tools").glob("*.py")]
        hard_coded = [path.relative_to(PROJECT) for path in sources
                      if path.name != "constants.py" and f'"{VERSION}"' in path.read_text(encoding="utf-8")]
        self.assertEqual(hard_coded, [])
