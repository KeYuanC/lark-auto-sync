from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime.converter import ConversionDependencyError, convert_attachment


class ConverterTests(unittest.TestCase):
    def test_txt_is_normalized_to_markdown(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source, output = root / "note.txt", root / "note.md"
            source.write_text("# Title\n\nFact", encoding="utf-8")

            result = convert_attachment(source, output)

            self.assertEqual(result.output_path, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "# Title\n\nFact\n")

    def test_doc_without_converter_pauses(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "note.doc"
            source.write_bytes(b"not-a-real-doc")

            with self.assertRaises(ConversionDependencyError):
                convert_attachment(source, root / "note.md", platform_name="darwin")
