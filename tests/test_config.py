from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.config import ProfileError, load_profile


class ProfileTests(unittest.TestCase):
    def test_loads_profile_inside_workspace(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / "profile.yaml"
            path.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n",
                encoding="utf-8",
            )

            profile = load_profile(path)

            self.assertEqual(profile.id, "demo")
            self.assertEqual(
                profile.resolve_path("output/note.md"),
                root / "workspace" / "output/note.md",
            )

    def test_rejects_unknown_fields(self):
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "profile.yaml"
            path.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "  extra: true\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n",
                encoding="utf-8",
            )

            with self.assertRaises(ProfileError):
                load_profile(path)

    def test_rejects_unsupported_version_and_unsafe_identifier(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / "profile.yaml"
            path.write_text(
                "profile:\n"
                "  id: '../demo'\n"
                "  version: 2\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n",
                encoding="utf-8",
            )

            with self.assertRaises(ProfileError):
                load_profile(path)

    def test_rejects_absolute_or_escaping_workspace_paths(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            absolute_root = root / "absolute.yaml"
            absolute_root.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: C:/workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n",
                encoding="utf-8",
            )
            with self.assertRaises(ProfileError):
                load_profile(absolute_root)

            valid = root / "valid.yaml"
            valid.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n",
                encoding="utf-8",
            )
            profile = load_profile(valid)
            with self.assertRaises(ValueError):
                profile.resolve_path("../outside.md")


if __name__ == "__main__":
    unittest.main()
