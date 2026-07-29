from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.adapters.csv_update import CsvAmbiguityError, CsvHeaderError, update_unique_row


class CsvUpdateTests(unittest.TestCase):
    def test_requires_exactly_one_identity_match(self):
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "people.csv"
            path.write_text("name,status\nAda,pending\nAda,pending\n", encoding="utf-8-sig")

            with self.assertRaises(CsvAmbiguityError):
                update_unique_row(path, {"name": "Ada"}, {"status": "done"}, ["name", "status"])

    def test_updates_one_row_preserving_headers_and_unrelated_rows(self):
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "people.csv"
            path.write_text("name,status,note\nAda,pending,keep\nBea,pending,other\n", encoding="utf-8-sig")

            result = update_unique_row(
                path,
                {"name": "Ada"},
                {"status": "done"},
                ["name", "status", "note"],
            )

            self.assertTrue(result.updated)
            self.assertEqual(result.row_index, 0)
            self.assertEqual(
                path.read_text(encoding="utf-8-sig"),
                "name,status,note\nAda,done,keep\nBea,pending,other\n",
            )

    def test_rejects_non_exact_headers_before_writing(self):
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "people.csv"
            original = "status,name\npending,Ada\n"
            path.write_text(original, encoding="utf-8-sig")

            with self.assertRaises(CsvHeaderError):
                update_unique_row(path, {"name": "Ada"}, {"status": "done"}, ["name", "status"])

            self.assertEqual(path.read_text(encoding="utf-8-sig"), original)

    def test_rejects_unknown_change_column_without_replacing_file(self):
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "people.csv"
            original = "name,status\nAda,pending\n"
            path.write_text(original, encoding="utf-8-sig")

            with self.assertRaises(CsvHeaderError):
                update_unique_row(path, {"name": "Ada"}, {"owner": "Bea"}, ["name", "status"])

            self.assertEqual(path.read_text(encoding="utf-8-sig"), original)


if __name__ == "__main__":
    unittest.main()
