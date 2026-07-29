import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.finalize import finalize_job
from runtime.models import Profile
from runtime.queue import Queue


class FailingReceiptClient:
    def reply_message(self, message_id, text):
        return {"ok": False}


class FinalizeTests(unittest.TestCase):
    def test_keeps_source_and_job_when_receipt_fails(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = Profile(
                "demo", root / "profile.yaml", root,
                {"workspace": {"state_directory": ".state"}, "source": {"chat_ids": ["oc_demo"]},
                 "publish": {"lark_receipt": {"enabled": True, "template": "receipt.txt"}}},
            )
            (root / "receipt.txt").write_text("Done {{filename}}", encoding="utf-8")
            queue = Queue(profile)
            source = queue.staging_root / "job_1" / "attachment.md"
            markdown = queue.staging_root / "job_1" / "note.md"
            source.parent.mkdir(parents=True)
            source.write_text("source", encoding="utf-8")
            markdown.write_text("One. Two.", encoding="utf-8")
            queue.enqueue({"job_id": "job_1", "message_id": "message_1", "filename": "Ada.md", "source_path": str(source), "markdown_path": str(markdown)})
            extraction = root / "extraction.json"
            extraction.write_text(json.dumps({"participants": [], "evidence": ["One.", "Two."]}), encoding="utf-8")

            result = finalize_job(profile, "job_1", extraction, FailingReceiptClient())

            self.assertFalse(result["ok"])
            self.assertTrue(source.exists())
            self.assertEqual(len(queue.list_pending()), 1)

    def test_updates_each_participant_csv_row_from_mapping(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "config").mkdir()
            (root / "config" / "weekly.yaml").write_text(
                "path: weekly.csv\nexpected_headers: [name, summary]\nidentity:\n  name: '{{participant}}'\nchanges:\n  summary: '{{summary}}'\n",
                encoding="utf-8",
            )
            (root / "weekly.csv").write_text("name,summary\nAda,\n", encoding="utf-8-sig")
            profile = Profile(
                "demo", root / "profile.yaml", root,
                {"workspace": {"state_directory": ".state"}, "source": {"chat_ids": ["oc_demo"]},
                 "routes": [{"id": "weekly", "match": {"predicate": "always"}, "action": {"adapter": "csv_update", "config": "config/weekly.yaml"}}]},
            )
            queue = Queue(profile)
            source = queue.staging_root / "job_1" / "attachment.md"
            markdown = queue.staging_root / "job_1" / "note.md"
            source.parent.mkdir(parents=True)
            source.write_text("source", encoding="utf-8")
            markdown.write_text("Ada joined. Training completed.", encoding="utf-8")
            queue.enqueue({"job_id": "job_1", "filename": "Ada.md", "source_path": str(source), "markdown_path": str(markdown)})
            extraction = root / "extraction.json"
            extraction.write_text(json.dumps({"participants": ["Ada"], "summary": "completed", "evidence": ["Ada joined.", "Training completed."]}), encoding="utf-8")

            result = finalize_job(profile, "job_1", extraction)

            self.assertTrue(result["ok"])
            self.assertIn("Ada,completed", (root / "weekly.csv").read_text(encoding="utf-8-sig"))
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
