from __future__ import annotations

from pathlib import Path
import unittest

from runtime.config import load_profile


SKILL_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = (
    SKILL_ROOT / "profiles" / "generic.example.yaml",
    SKILL_ROOT / "profiles" / "meeting-minutes.example.yaml",
)


class ExampleProfileTests(unittest.TestCase):
    def test_both_example_profiles_load(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                profile = load_profile(path)
                self.assertEqual(profile.source_path, path.resolve())


if __name__ == "__main__":
    unittest.main()
