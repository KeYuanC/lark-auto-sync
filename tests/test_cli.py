from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.lark_sync import main


class CliTests(unittest.TestCase):
    def test_version_accepts_json(self):
        result = _run(["--json", "--version"])
        self.assertEqual(result[0], 0)
        self.assertTrue(result[1]["ok"])
        self.assertEqual(result[1]["operation"], "version")
        self.assertEqual(result[1]["status"], "ready")
        self.assertIn("errors", result[1])

    def test_queue_list_returns_stable_json_envelope(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = root / "profile.yaml"
            profile.write_text(_profile_text(), encoding="utf-8")

            code, payload = _run(["--json", "--profile", str(profile), "queue", "list"])

            self.assertEqual(code, 0)
            self.assertEqual(payload["profile"], "demo")
            self.assertEqual(payload["operation"], "queue.list")
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["errors"], [])
            self.assertEqual(payload["details"]["jobs"], [])

    def test_heartbeat_prompt_is_printed_without_json(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = root / "profile.yaml"
            profile.write_text(_profile_text(), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--profile", str(profile), "heartbeat-prompt"])

            self.assertEqual(code, 0)
            self.assertIn("Process the Lark Auto Sync extraction queue", output.getvalue())

    def test_finalize_missing_job_is_a_structured_error(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = root / "profile.yaml"
            extraction = root / "extraction.json"
            profile.write_text(_profile_text(), encoding="utf-8")
            extraction.write_text("{}", encoding="utf-8")

            code, payload = _run([
                "--json", "--profile", str(profile), "queue", "finalize",
                "--job-id", "job_1", "--extracted-json", str(extraction),
            ])

            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "queue_claim_failed")
            self.assertEqual(payload["errors"], ["job_not_found"])

    def test_package_includes_bilingual_readmes(self):
        with TemporaryDirectory() as raw_root:
            archive = Path(raw_root) / "lark-auto-sync.zip"

            code, payload = _run(["--json", "package", "--output", str(archive)])

            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
            self.assertTrue({
                "lark-auto-sync/README.md",
                "lark-auto-sync/README.zh-CN.md",
                "lark-auto-sync/README.en.md",
            }.issubset(names))
            self.assertFalse(any(name.startswith("lark-auto-sync/.git/") for name in names))


def _run(arguments: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(arguments)
    return code, json.loads(output.getvalue())


def _profile_text() -> str:
    return """profile:
  id: demo
  version: 1
workspace:
  root: ./workspace
source:
  chat_ids: [oc_demo]
  bot_name: Sync Bot
"""


if __name__ == "__main__":
    unittest.main()
