import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "release-notes.py"
SAMPLE = """# Changelog

## [1.1.0] - 2026-10-01

### Added
- Something new.

## [1.0.0] - 2026-09-05

### Added
- First release.
"""


class ReleaseNotesTests(unittest.TestCase):
    def run_script(self, version, changelog=SAMPLE):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), version],
                capture_output=True, text=True, check=False, cwd=directory,
            )

    def test_prints_only_the_requested_section(self):
        result = self.run_script("v1.0.0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- First release.", result.stdout)
        self.assertNotIn("Something new", result.stdout)
        self.assertIn("Full changelog:", result.stdout)

    def test_missing_version_fails(self):
        result = self.run_script("2.0.0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no section for version 2.0.0", result.stderr)

    def test_the_real_changelog_has_the_current_release(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "1.0.0"],
            capture_output=True, text=True, check=False, cwd=ROOT,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("### Added", result.stdout)


if __name__ == "__main__":
    unittest.main()
