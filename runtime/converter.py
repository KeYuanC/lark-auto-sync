"""Safe, local conversion of supported chat attachments to Markdown."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from typing import Iterable
import zipfile
import xml.etree.ElementTree as ET


SUPPORTED_SUFFIXES = {".txt", ".md", ".docx", ".doc"}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_HEADER = b"PK\x03\x04"
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ConversionError(ValueError):
    """Raised when an attachment cannot safely be converted."""


class ConversionDependencyError(ConversionError):
    """Raised when a required local office converter is unavailable."""


@dataclass(frozen=True)
class ConversionResult:
    output_path: Path
    source_suffix: str
    converter: str


def convert_attachment(
    source: Path, destination: Path, platform_name: str | None = None
) -> ConversionResult:
    """Convert one supported local attachment to Markdown.

    This function never evaluates attachment content and invokes external
    applications only through fixed argument arrays. The caller owns profile
    containment checks for ``source`` and ``destination``.
    """

    source = Path(source)
    destination = Path(destination)
    suffix = source.suffix.lower()
    _validate_paths(source, destination, suffix)

    if suffix in {".txt", ".md"}:
        _validate_text_signature(source)
        content = _decode_text(source.read_bytes())
        _write_markdown(destination, content)
        return ConversionResult(destination, suffix, "text")

    if suffix == ".docx":
        content = _docx_to_markdown(source)
        _write_markdown(destination, content)
        return ConversionResult(destination, suffix, "docx")

    converter = _find_doc_converter(platform_name)
    if converter is None:
        raise ConversionDependencyError("doc_converter_unavailable")

    with tempfile.TemporaryDirectory(prefix="lark-auto-sync-doc-") as raw_root:
        converted = _convert_doc_to_docx(source, Path(raw_root), converter)
        content = _docx_to_markdown(converted)
    _write_markdown(destination, content)
    return ConversionResult(destination, suffix, converter[0])


def _validate_paths(source: Path, destination: Path, suffix: str) -> None:
    if suffix not in SUPPORTED_SUFFIXES:
        raise ConversionError("unsupported_attachment_type")
    if not source.is_file():
        raise ConversionError("attachment_not_found")
    if source.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ConversionError("attachment_too_large")
    if destination.suffix.lower() != ".md":
        raise ConversionError("markdown_destination_required")


def _validate_text_signature(source: Path) -> None:
    header = source.read_bytes()[:8]
    if header.startswith(_OLE_HEADER) or header.startswith(_ZIP_HEADER):
        raise ConversionError("attachment_type_mismatch")


def _decode_text(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConversionError("unsupported_text_encoding")


def _write_markdown(destination: Path, content: str) -> None:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(f"{normalized}\n", encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _docx_to_markdown(source: Path) -> str:
    _validate_docx(source)
    try:
        with zipfile.ZipFile(source) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
    except (ET.ParseError, OSError, zipfile.BadZipFile, KeyError) as error:
        raise ConversionError("invalid_docx") from error

    blocks: list[str] = []
    for child in document.findall(f"{_WORD_NS}body/*"):
        if child.tag == f"{_WORD_NS}p":
            paragraph = _paragraph_to_markdown(child)
            if paragraph:
                blocks.append(paragraph)
        elif child.tag == f"{_WORD_NS}tbl":
            table = _table_to_markdown(child)
            if table:
                blocks.append(table)
    return "\n\n".join(blocks)


def _validate_docx(source: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            uncompressed_size = sum(info.file_size for info in archive.infolist())
            if (
                not source.read_bytes()[:4].startswith(_ZIP_HEADER)
                or "[Content_Types].xml" not in names
                or "word/document.xml" not in names
                or uncompressed_size > MAX_DOCX_UNCOMPRESSED_BYTES
            ):
                raise ConversionError("attachment_type_mismatch")
    except zipfile.BadZipFile as error:
        raise ConversionError("attachment_type_mismatch") from error


def _paragraph_to_markdown(paragraph: ET.Element) -> str:
    text = _element_text(paragraph).strip()
    if not text:
        return ""
    style = paragraph.find(f"{_WORD_NS}pPr/{_WORD_NS}pStyle")
    style_name = "" if style is None else style.attrib.get(f"{_WORD_NS}val", "")
    if style_name.lower().startswith("heading"):
        level = _heading_level(style_name)
        return f"{'#' * level} {text}"
    if paragraph.find(f"{_WORD_NS}pPr/{_WORD_NS}numPr") is not None:
        return f"- {text}"
    return text


def _heading_level(style_name: str) -> int:
    digits = "".join(character for character in style_name if character.isdigit())
    return min(max(int(digits or "1"), 1), 6)


def _table_to_markdown(table: ET.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(f"{_WORD_NS}tr"):
        cells = [
            _element_text(cell).strip().replace("|", "\\|").replace("\n", "<br>")
            for cell in row.findall(f"{_WORD_NS}tc")
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    separator = ["---"] * width
    body = padded[1:]
    return "\n".join(
        [
            _markdown_table_row(header),
            _markdown_table_row(separator),
            *(_markdown_table_row(row) for row in body),
        ]
    )


def _markdown_table_row(cells: Iterable[str]) -> str:
    return f"| {' | '.join(cells)} |"


def _element_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{_WORD_NS}t"))


def _find_doc_converter(platform_name: str | None) -> tuple[str, Path] | None:
    current_platform = (platform_name or platform.system()).lower()
    if current_platform in {"darwin", "macos"}:
        libreoffice = _find_executable(("soffice", "libreoffice"))
        return ("libreoffice", libreoffice) if libreoffice else None
    if current_platform in {"windows", "win32"}:
        libreoffice = _find_executable(("soffice.exe", "soffice", "libreoffice.exe"))
        if libreoffice:
            return "libreoffice", libreoffice
        word = _find_windows_word()
        return ("word", word) if word else None
    raise ConversionDependencyError("unsupported_platform")


def _find_executable(names: Iterable[str]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _find_windows_word() -> Path | None:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for raw_root in roots:
        if not raw_root:
            continue
        candidate = Path(raw_root) / "Microsoft Office" / "root" / "Office16" / "WINWORD.EXE"
        if candidate.is_file():
            return candidate
    found = shutil.which("winword.exe")
    return Path(found) if found else None


def _convert_doc_to_docx(source: Path, output_root: Path, converter: tuple[str, Path]) -> Path:
    name, executable = converter
    if name == "word":
        return _word_com_convert(source, output_root, executable)
    if name != "libreoffice":
        raise ConversionDependencyError("doc_converter_unavailable")

    completed = subprocess.run(
        [str(executable), "--headless", "--convert-to", "docx", "--outdir", str(output_root), str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    output = output_root / f"{source.stem}.docx"
    if completed.returncode != 0 or not output.is_file():
        raise ConversionError("doc_conversion_failed")
    return output


def _word_com_convert(source: Path, output_root: Path, executable: Path) -> Path:
    """Convert a legacy document with Word's macro-free COM API.

    ``executable`` is discovered before this function is called. COM starts
    the registered Word server without constructing a command line from the
    attachment, so the document path is data passed to the automation API,
    never an executable argument or shell fragment.
    """

    del executable
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as error:
        raise ConversionDependencyError("word_com_unavailable") from error

    output = output_root / f"{source.stem}.docx"
    application = None
    document = None
    try:
        application = win32com.client.DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        # msoAutomationSecurityForceDisable prevents document auto-macros.
        application.AutomationSecurity = 3
        document = application.Documents.Open(
            FileName=str(source),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=False,
            NoEncodingDialog=True,
        )
        document.SaveAs2(
            FileName=str(output),
            FileFormat=16,  # wdFormatDocumentDefault (.docx)
            AddToRecentFiles=False,
        )
    except Exception as error:
        raise ConversionError("word_conversion_failed") from error
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit(SaveChanges=0)
            except Exception:
                pass

    if not output.is_file():
        raise ConversionError("word_conversion_failed")
    return output
