from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]


class SkillLayoutTests(unittest.TestCase):
    def test_required_skill_files_exist(self):
        for relative_path in [
            "SKILL.md",
            "agents/openai.yaml",
            "requirements.txt",
            "scripts/lark_sync.py",
        ]:
            self.assertTrue((SKILL_ROOT / relative_path).is_file(), relative_path)


if __name__ == "__main__":
    unittest.main()
