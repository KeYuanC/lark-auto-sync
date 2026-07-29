from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.collector import Collector


class FakeLarkClient:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def download_file(self, file_key: str, destination: Path) -> None:
        self.downloaded.append(file_key)
        destination.write_text("# Person A\n\nFact", encoding="utf-8")

    def recent_messages(self, chat_id: str, *, limit: int = 100) -> list[dict]:
        del chat_id, limit
        return []


class CollectorTests(unittest.TestCase):
    def test_keeps_pending_job_and_staged_source_when_download_fails(self):
        class PartialDownloadClient(FakeLarkClient):
            def download_file(self, file_key: str, destination: Path) -> None:
                self.downloaded.append(file_key)
                destination.write_text("partial attachment", encoding="utf-8")
                raise OSError("network interrupted")

        with TemporaryDirectory() as raw_root:
            client = PartialDownloadClient()
            collector = Collector.for_test(
                Path(raw_root), client, bot_name="Sync Bot", window_seconds=180
            )
            collector.handle_message(
                {
                    "id": "mention-1",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "text": "@Sync Bot",
                    "created_at": 1000,
                }
            )

            jobs = collector.handle_message(
                {
                    "id": "attachment-1",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "file_key": "file_1",
                    "filename": "Person A.md",
                    "created_at": 1010,
                }
            )

            self.assertEqual(len(jobs), 1)
            pending = collector.queue.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["job_id"], jobs[0])
            self.assertEqual(pending[0]["intake_error"], "download_failed")
            self.assertTrue(Path(pending[0]["source_path"]).is_file())
            self.assertIn("markdown_path", pending[0])

    def test_queues_attachment_after_matching_mention(self):
        with TemporaryDirectory() as raw_root:
            client = FakeLarkClient()
            collector = Collector.for_test(
                Path(raw_root), client, bot_name="Sync Bot", window_seconds=180
            )
            collector.handle_message(
                {
                    "id": "m1",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "text": "@Sync Bot",
                    "created_at": 1000,
                }
            )
            jobs = collector.handle_message(
                {
                    "id": "m2",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "file_key": "file_1",
                    "filename": "Person A.md",
                    "created_at": 1010,
                }
            )

            self.assertEqual(len(jobs), 1)
            self.assertEqual(client.downloaded, ["file_1"])
            self.assertEqual(len(collector.queue.list_pending()), 1)

    def test_pairs_file_before_mention_only_for_same_chat_sender_and_window(self):
        with TemporaryDirectory() as raw_root:
            client = FakeLarkClient()
            collector = Collector.for_test(
                Path(raw_root), client, bot_name="Sync Bot", window_seconds=180
            )
            collector.handle_message(
                {
                    "id": "file-before",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "file_key": "file_before",
                    "filename": "Person A.md",
                    "created_at": 1000,
                }
            )
            self.assertEqual(
                collector.handle_message(
                    {
                        "id": "wrong-chat",
                        "chat_id": "oc_other",
                        "sender": "Ada",
                        "text": "@Sync Bot",
                        "created_at": 1010,
                    }
                ),
                [],
            )
            self.assertEqual(
                collector.handle_message(
                    {
                        "id": "wrong-sender",
                        "chat_id": "oc_demo",
                        "sender": "Bea",
                        "text": "@Sync Bot",
                        "created_at": 1010,
                    }
                ),
                [],
            )
            self.assertEqual(
                collector.handle_message(
                    {
                        "id": "too-late",
                        "chat_id": "oc_demo",
                        "sender": "Ada",
                        "text": "@Sync Bot",
                        "created_at": 1181,
                    }
                ),
                [],
            )

            jobs = collector.handle_message(
                {
                    "id": "paired",
                    "chat_id": "oc_demo",
                    "sender": "Ada",
                    "text": "@Sync Bot",
                    "created_at": 1100,
                }
            )
            self.assertEqual(len(jobs), 1)
            self.assertEqual(client.downloaded, ["file_before"])

    def test_scan_once_returns_number_of_enqueued_jobs(self):
        class ScanningClient(FakeLarkClient):
            def recent_messages(self, chat_id: str, *, limit: int = 100) -> list[dict]:
                del limit
                return [
                    {
                        "id": "m1",
                        "chat_id": chat_id,
                        "sender": "Ada",
                        "text": "@Sync Bot",
                        "created_at": 1000,
                    },
                    {
                        "id": "m2",
                        "chat_id": chat_id,
                        "sender": "Ada",
                        "file_key": "file_1",
                        "filename": "Person A.md",
                        "created_at": 1010,
                    },
                ]

        with TemporaryDirectory() as raw_root:
            collector = Collector.for_test(
                Path(raw_root), ScanningClient(), bot_name="Sync Bot", window_seconds=180
            )
            self.assertEqual(collector.scan_once(), 1)


if __name__ == "__main__":
    unittest.main()
