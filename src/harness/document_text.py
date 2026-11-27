"""Deterministic text and reference views for original document bytes."""

from __future__ import annotations

import hashlib
import io
import posixpath
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
    ET.SubElement(ET.SubElement(root, "head"), "meta", charset="utf-8")
    main = ET.SubElement(root, "main", id="document")
    if media_type == "application/pdf":
        if not raw.startswith(b"%PDF"):
            raise ValueError("Declared PDF input does not contain a PDF")
        from pypdf import PdfReader, __version__

        for index, page in enumerate(PdfReader(io.BytesIO(raw)).pages, 1):
            section = ET.SubElement(main, "section", {"data-page": str(index)})
            for line, text in enumerate((page.extract_text() or "").splitlines(), 1):
                ET.SubElement(section, "p", {"data-line": str(line)}).text = text
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

        def members(node, tag):
            for child in node:
                if child.tag == f"{{{namespace}}}" + tag:
                    yield child
                elif child.tag == f"{{{namespace}}}sdt":
                    content = child.find(f"{{{namespace}}}sdtContent")
                    if content is not None:
                        yield from members(content, tag)
                elif child.tag == f"{{{namespace}}}customXml":
                    yield from members(child, tag)

        def table_view(node, parent):
            table = ET.SubElement(parent, "table")
            active = {}
            for row in members(node, "tr"):
                before = row.find(f"{{{namespace}}}trPr/{{{namespace}}}gridBefore")
                if before is not None and int(before.get(f"{{{namespace}}}val", "0")):
                    raise ValueError(
                        "DOCX tables with omitted leading grid cells are not supported"
                    )
                target_row = ET.SubElement(table, "tr")
                after = row.find(f"{{{namespace}}}trPr/{{{namespace}}}gridAfter")
                if after is not None:
                    target_row.set("data-grid-after", after.get(f"{{{namespace}}}val", "0"))
                column, continued = 0, {}
                for cell in members(row, "tc"):
                    properties = cell.find(f"{{{namespace}}}tcPr")
                    span, merge = 1, None
                    if properties is not None:
                        if properties.find(f"{{{namespace}}}hMerge") is not None:
                            raise ValueError("Legacy DOCX horizontal merge cells are not supported")
                        horizontal = properties.find(f"{{{namespace}}}gridSpan")
                        if horizontal is not None:
                            span = int(horizontal.get(f"{{{namespace}}}val", "0"))
                        merge = properties.find(f"{{{namespace}}}vMerge")
                    if span < 1:
                        raise ValueError("DOCX cell spans must be positive")
                    state = (
                        merge.get(f"{{{namespace}}}val", "continue") if merge is not None else None
                    )
                    if state == "continue":
                        prior = active.get(column)
                        if prior is None or prior[1] != span:
                            raise ValueError(
                                "DOCX vertical continuation has no matching merged cell"
                            )
                        if "".join(_text_nodes(cell)).strip():
                            raise ValueError("DOCX vertical continuation contains separate text")
                        target = prior[0]
                        target.set("rowspan", str(int(target.get("rowspan", "1")) + 1))
                        continued[column] = prior
                    else:
                        if state not in {None, "restart"}:
                            raise ValueError("Unsupported DOCX vertical merge state")
                        target = ET.SubElement(target_row, "td")
                        if span > 1:
                            target.set("colspan", str(span))
                        for child in cell:
                            project(child, target)
                        if state == "restart":
                            continued[column] = (target, span)
                    column += span
                active = continued

        def project(node, parent):
            if node.tag == f"{{{namespace}}}tbl":
                table_view(node, parent)
                return
            if node.tag == f"{{{namespace}}}p":
                ET.SubElement(parent, "p").text = "".join(_text_nodes(node))
                return
            target = ET.SubElement(parent, tags[node.tag]) if node.tag in tags else parent
            for child in node:
                project(child, target)

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name, source_root in _docx_parts(archive):
                section = ET.SubElement(main, "section", {"data-part": name})
                project(source_root, section)
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
        for _, root in _docx_parts(archive):
            lines.extend(
                (line if preserve_whitespace else line.strip())
                for line in "".join(_text_nodes(root)).splitlines()
                if line.strip()
            )
    return "\n".join(lines)


def _docx_parts(archive):
    main = _docx_root(archive, "word/document.xml")
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    relation_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    references = []
    for kind in ("header", "footer", "footnote", "endnote"):
        references.extend((kind, node) for node in main.iter(namespace + kind + "Reference"))
    if not references:
        return [("word/document.xml", main)]
    rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    relationships = {node.get("Id"): node for node in rels}
    parts, note_ids = {}, {}
    for kind, reference in references:
        if kind in {"header", "footer"}:
            relation = relationships.get(reference.get(relation_ns + "id"))
        else:
            matches = [node for node in rels if node.get("Type", "").endswith("/" + kind + "s")]
            relation = matches[0] if len(matches) == 1 else None
        if relation is None or relation.get("TargetMode") == "External":
            raise ValueError("DOCX content reference needs an internal relationship")
        if not relation.get("Type", "").endswith("/" + kind + ("s" if "note" in kind else "")):
            raise ValueError("DOCX content relationship has the wrong type")
        target = relation.get("Target", "")
        name = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                 else posixpath.join("word", target))
        if not name.startswith("word/"):
            raise ValueError("DOCX content relationship is outside the document package")
        if name not in parts:
            parts[name] = _docx_root(archive, name)
        if kind in {"footnote", "endnote"}:
            note_ids.setdefault(name, set()).add(reference.get(namespace + "id"))
    for name, ids in note_ids.items():
        root = parts[name]
        actual = {node.get(namespace + "id") for node in root}
        if not ids.issubset(actual):
            raise ValueError("DOCX note reference has no corresponding note")
        for node in list(root):
            if node.get(namespace + "id") not in ids:
                root.remove(node)
    return [("word/document.xml", main)] + sorted(parts.items())


def _docx_root(archive, name):
    root = ET.fromstring(archive.read(name))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    if not root.tag.startswith(namespace):
        raise ValueError("Unsupported DOCX WordprocessingML namespace")
    alternate = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
    if next(root.iter(alternate), None) is not None:
        raise ValueError("DOCX AlternateContent requires an explicit rendering representation")
    if next(root.iter(namespace + "altChunk"), None) is not None:
        raise ValueError("DOCX altChunk requires an explicit embedded-content representation")
    revisions = {"ins", "del", "delText", "delInstrText", "cellIns", "cellDel", "cellMerge"}
    for node in root.iter():
        if node.tag.startswith(namespace):
            name = node.tag[len(namespace):]
            if (name in revisions or name.endswith("Change")
                    or name.startswith(("moveFrom", "moveTo"))):
                raise ValueError("DOCX tracked revisions require an explicit revision policy")
    return root


def _text_nodes(node):
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraph = f"{{{namespace}}}p"
    text = f"{{{namespace}}}t"
    spacing = {f"{{{namespace}}}tab", f"{{{namespace}}}br", f"{{{namespace}}}cr"}
    characters = {f"{{{namespace}}}noBreakHyphen": "\u2011", f"{{{namespace}}}softHyphen": "\u00ad"}

    if node.tag == text:
        yield node.text or ""
    elif node.tag in spacing:
        yield " "
    elif node.tag in characters:
        yield characters[node.tag]
    for child in node:
        yield from _text_nodes(child)
    if node.tag == paragraph:
        yield "\n"
