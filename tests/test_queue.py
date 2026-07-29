from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.models import Profile
from runtime.queue import Queue, QueueBusyError


class QueueTests(unittest.TestCase):
    def test_claim_is_atomic_and_completion_moves_job_to_done(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile = Profile(
                "demo",
                root / "profile.yaml",
                root,
                {"workspace": {"state_directory": ".state"}},
            )
            queue = Queue(profile)
            queue.enqueue({"job_id": "job_1", "source_path": "staging/a.md"})
            queue.claim("job_1")
            with self.assertRaises(QueueBusyError):
                queue.claim("job_1")
            queue.complete("job_1", {"ok": True})
            self.assertEqual(queue.list_pending(), [])
            self.assertTrue((queue.done_root / "job_1.json").is_file())


if __name__ == "__main__":
    unittest.main()
