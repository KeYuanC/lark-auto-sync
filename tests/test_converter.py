from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime.converter import (
    ConversionDependencyError,
    _convert_doc_to_docx,
    convert_attachment,
)


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

    def test_windows_word_converter_uses_com_adapter(self):
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "note.doc"
            source.write_bytes(b"not-a-real-doc")
            word = root / "WINWORD.EXE"
            converted = root / "note.docx"

            with patch("runtime.converter._word_com_convert", return_value=converted) as convert:
                result = _convert_doc_to_docx(source, root, ("word", word))

            self.assertEqual(result, converted)
            convert.assert_called_once_with(source, root, word)
