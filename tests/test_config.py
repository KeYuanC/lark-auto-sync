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

    def test_validates_processing_alias_and_terminology_paths(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            valid = root / "valid.yaml"
            valid.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n"
                "processing:\n"
                "  aliases: config/aliases.yaml\n"
                "  terminology: config/terminology.yaml\n",
                encoding="utf-8",
            )

            profile = load_profile(valid)

            self.assertEqual(
                profile.resolve_path(profile.data["processing"]["aliases"]),
                root / "workspace" / "config" / "aliases.yaml",
            )
            self.assertEqual(
                profile.resolve_path(profile.data["processing"]["terminology"]),
                root / "workspace" / "config" / "terminology.yaml",
            )

            for field in ("aliases", "terminology"):
                escaping = root / f"{field}-escaping.yaml"
                escaping.write_text(
                    "profile:\n"
                    "  id: demo\n"
                    "  version: 1\n"
                    "workspace:\n"
                    "  root: ./workspace\n"
                    "source:\n"
                    "  chat_ids: [oc_demo]\n"
                    "processing:\n"
                    f"  {field}: ../outside.yaml\n",
                    encoding="utf-8",
                )
                with self.subTest(field=field), self.assertRaises(ProfileError):
                    load_profile(escaping)

    def test_validates_router_predicate_grammar_and_action_config_path(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            valid = root / "routed.yaml"
            valid.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n"
                "routes:\n"
                "  - id: update-plan\n"
                "    match:\n"
                "      predicate: filename_contains\n"
                "      value: 进阶\n"
                "    action:\n"
                "      adapter: csv_update\n"
                "      config: config/weekly-plan.yaml\n",
                encoding="utf-8",
            )

            profile = load_profile(valid)

            self.assertEqual(profile.data["routes"][0]["action"]["adapter"], "csv_update")
            self.assertEqual(
                profile.resolve_path(profile.data["routes"][0]["action"]["config"]),
                root / "workspace" / "config" / "weekly-plan.yaml",
            )

            invalid = root / "invalid-predicate.yaml"
            invalid.write_text(
                "profile:\n"
                "  id: demo\n"
                "  version: 1\n"
                "workspace:\n"
                "  root: ./workspace\n"
                "source:\n"
                "  chat_ids: [oc_demo]\n"
                "routes:\n"
                "  - id: invalid\n"
                "    match:\n"
                "      predicate: arbitrary_code\n"
                "    action:\n"
                "      adapter: csv_update\n",
                encoding="utf-8",
            )
            with self.assertRaises(ProfileError):
                load_profile(invalid)


if __name__ == "__main__":
    unittest.main()
