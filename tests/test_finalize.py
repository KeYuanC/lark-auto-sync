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


if __name__ == "__main__":
    unittest.main()
