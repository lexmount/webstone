"""Deterministic text extraction for raw documents returned by a provider."""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

DOCUMENT_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


def document_view(raw: bytes, media_type: str, *, encoding="utf-8") -> tuple[bytes, str]:
    """Return a deterministic reference view; the original bytes remain the input.

    PDF pages and DOCX parts retain boundaries. DOCX tables retain their actual
    row/cell structure. This view is never sent in place of a native document.
    """
    if media_type not in DOCUMENT_EXTENSIONS:
        raise ValueError("Unsupported source document media type: " + media_type)
    root = ET.Element("html")
    main = ET.SubElement(root, "main", id="document")
    if media_type == "application/pdf":
        if not raw.startswith(b"%PDF"):
            raise ValueError("Declared PDF input does not contain a PDF")
        from pypdf import PdfReader, __version__

        for index, page in enumerate(PdfReader(io.BytesIO(raw)).pages, 1):
            section = ET.SubElement(main, "section", {"data-page": str(index)})
            ET.SubElement(section, "p").text = page.extract_text() or ""
        extractor = "pypdf/" + __version__
    elif media_type == "text/plain":
        for index, paragraph in enumerate(
            re.split(r"\r?\n[ \t]*\r?\n", raw.decode(encoding or "utf-8")), 1
        ):
            ET.SubElement(main, "p", {"data-block": str(index)}).text = paragraph
        extractor = "plain_text"
    else:
        if not zipfile.is_zipfile(io.BytesIO(raw)):
            raise ValueError("Declared DOCX input does not contain a DOCX")
        namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        tags = {
            f"{{{namespace}}}" + name: tag
            for name, tag in (
                ("tbl", "table"),
                ("tr", "tr"),
                ("tc", "td"),
                ("p", "p"),
            )
        }

        def project(node, parent):
            if node.tag == f"{{{namespace}}}p":
                ET.SubElement(parent, "p").text = "".join(_text_nodes(node))
                return
            target = ET.SubElement(parent, tags[node.tag]) if node.tag in tags else parent
            for child in node:
                project(child, target)

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in _docx_parts(archive):
                section = ET.SubElement(main, "section", {"data-part": name})
                project(ET.fromstring(archive.read(name)), section)
        extractor = "docx_xml"
    implementation = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity = extractor + "/document-view/1/" + implementation
    return ET.tostring(root, encoding="utf-8", method="html"), identity


def extract_document_text(raw: bytes, *, preserve_whitespace: bool = False) -> tuple[str, str]:
    """Return extracted text and the extractor identity for supported documents."""
    if raw.startswith(b"%PDF"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages), "pypdf"
    if raw.startswith(b"PK"):
        return _extract_docx(raw, preserve_whitespace=preserve_whitespace), "docx_xml"
    raise ValueError("Unsupported raw document type")


def _extract_docx(raw: bytes, *, preserve_whitespace: bool = False) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        lines = []
        for name in _docx_parts(archive):
            root = ET.fromstring(archive.read(name))
            lines.extend(
                (line if preserve_whitespace else line.strip())
                for line in "".join(_text_nodes(root)).splitlines()
                if line.strip()
            )
    return "\n".join(lines)


def _docx_parts(archive):
    return ["word/document.xml"] + sorted(
        name
        for name in archive.namelist()
        if re.fullmatch(r"word/(footnotes|endnotes|header\d+|footer\d+)\.xml", name)
    )


def _text_nodes(node):
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraph = f"{{{namespace}}}p"
    text = f"{{{namespace}}}t"
    spacing = {f"{{{namespace}}}tab", f"{{{namespace}}}br"}

    if node.tag == text:
        yield node.text or ""
    elif node.tag in spacing:
        yield " "
    for child in node:
        yield from _text_nodes(child)
    if node.tag == paragraph:
        yield "\n"
