"""Native URL documents retain original inputs and use source-bound JSON acceptance."""

import copy
import io
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from harness.document_text import document_view
from harness.web_extract import pipeline, queries
from harness.web_extract.cli import main

pytestmark = pytest.mark.usefixtures("stubbed_serial_collection")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def source_document(kind, *, pdf_lines=None):
    if kind == "txt":
        return b"Harbor Lab completed 40 trials.", "text/plain"
    if kind == "pdf":
        pdf_lines = pdf_lines or [b"Harbor Lab completed 40 trials."]
        stream = (
            b"BT /F1 12 Tf 72 720 Td "
            + b" 0 -14 Td ".join(b"(" + line + b") Tj" for line in pdf_lines)
            + b" ET"
        )
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        ]
        raw = b"%PDF-1.4\n"
        offsets = [0]
        for index, value in enumerate(objects, 1):
            offsets.append(len(raw))
            raw += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
        start = len(raw)
        raw += b"xref\n0 6\n0000000000 65535 f \n"
        raw += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
        raw += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
        return raw, "application/pdf"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>Harbor Lab completed 40 trials.</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Books</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:p><w:r><w:t>Pens</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>""",
        )
    return output.getvalue(), DOCX


def catalog(root, kind):
    raw, media_type = source_document(kind)
    view, extractor = document_view(raw, media_type)
    (root / ("source." + kind)).write_bytes(raw)
    q = dict(
        id="research",
        query="Report the organization and number of completed trials in the document.",
        schema={
            "type": "object",
            "properties": {"organization": {"type": "string"}, "trials": {"type": "integer"}},
            "required": ["organization", "trials"],
            "additionalProperties": False,
        },
        answer={"organization": "Harbor Lab", "trials": 40},
        selection={"region_id": "body", "rationale": "The document's study facts."},
        evidence={
            "leaves": [
                {"pointer": "/organization", "selector": ".", "raw_value": "Harbor Lab"},
                {"pointer": "/trials", "selector": ".", "raw_value": "40"},
            ],
            "negative_answers": [{"organization": "Harbor Lab", "trials": 4}],
        },
    )
    page = dict(
        id="document",
        url="https://source.example/study." + kind,
        content_kind="research_document",
        required_characteristics=["document_body"],
        source=dict(
            input="source." + kind,
            sha256=pipeline.digest(raw),
            media_type=media_type,
            view={"sha256": pipeline.digest(view), "extractor": extractor},
            annotation_status="verified",
            observed_at=datetime.now(UTC).isoformat(),
            method="independent native document capture",
        ),
        regions=[
            dict(
                id="body",
                primary=True,
                selector="//main[@id='document']",
                characteristics=["document_body"],
            )
        ],
        queries=[q],
    )
    data = dict(
        schema="web-extract-query-catalog/1",
        id="native-documents",
        name="Native document controls",
        repository="example/native-documents",
        source_versions={"synthetic": "1" * 40},
        source_license="Synthetic fixture only",
        input_mode="live",
        selection={"purpose": "native document behavior controls"},
        pages=[page],
    )
    path = root / "catalog.json"
    pipeline.save_json(path, data)
    return path, raw, media_type


@pytest.mark.parametrize("kind", ["pdf", "docx", "txt"])
@pytest.mark.parametrize("unit", ["query", "page"])
def test_native_document_public_pipeline_preserves_bytes_url_query_schema(
    tmp_path, monkeypatch, kind, unit
):
    source, raw, media_type = catalog(tmp_path, kind)
    home = tmp_path / "home"
    pipeline.prepare(home, benchmark="queries", source=source, unit=unit)
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    cid, reference = next(iter(truth.items()))
    assert (home / "dataset" / reference["input_path"]).read_bytes() == raw
    assert reference["input_path"].endswith("." + kind)
    assert not list((home / "dataset/html").glob("*.html"))
    monkeypatch.setattr(
        "harness.http_client.request",
        lambda url, **kw: SimpleNamespace(
            status=200, url=url, body=raw, headers={"content-type": media_type}
        ),
    )
    positive = queries.qualify_live(home, [cid])
    assert positive[0]["reference_values_verified"] == 2
    altered = raw.replace(b"40 trials", b"41 trials") if kind == "txt" else None
    if altered:
        monkeypatch.setattr(
            "harness.http_client.request",
            lambda url, **kw: SimpleNamespace(
                status=200, url=url, body=altered, headers={"content-type": media_type}
            ),
        )
        with pytest.raises(ValueError, match="no longer supported"):
            queries.qualify_live(home, [cid])
        monkeypatch.setattr(
            "harness.http_client.request",
            lambda url, **kw: SimpleNamespace(
                status=200, url=url, body=raw, headers={"content-type": media_type}
            ),
        )

    def request(method, html, source_url, fixture_url, **options):
        assert html == "" and source_url == reference["url"] == fixture_url
        assert "completed trials" in options["prompt"]
        result = {"organization": "Harbor Lab", "trials": 40}
        if unit == "page":
            result = {"research": result}
        return dict(
            state="response_received",
            http_status=200,
            elapsed_seconds=0.01,
            response={"result": result},
            request={"url": fixture_url, "schema": options["schema"], "prompt": options["prompt"]},
        )

    pipeline.METHODS.register(
        "native-document-control",
        pipeline.ExtractMethod(
            name="native-document-control",
            keys=(),
            input="url",
            request=request,
            project=lambda payload, preserve: payload["result"],
            custom_schema=True,
            task_prompt_location="prompt",
        ),
    )
    try:
        monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
        initial = tmp_path / "initial.json"
        assessment = tmp_path / "assessment.json"
        plan = tmp_path / "plan.json"
        main(
            [
                "--home",
                str(home),
                "plan",
                "--methods",
                "native-document-control",
                "--output",
                str(initial),
            ]
        )
        main(
            [
                "--home",
                str(home),
                "preflight",
                "--profile",
                str(initial),
                "--method",
                "native-document-control",
                "--run-dir",
                str(tmp_path / "preflight"),
                "--assessment-output",
                str(assessment),
                "--sample-size",
                "1",
            ]
        )
        main(
            [
                "--home",
                str(home),
                "plan",
                "--methods",
                "native-document-control",
                "--output",
                str(plan),
                "--execution-assessment",
                str(assessment),
            ]
        )
        run = tmp_path / "run"
        main(
            [
                "--home",
                str(home),
                "run",
                "--profile",
                str(plan),
                "--method",
                "native-document-control",
                "--run-dir",
                str(run),
            ]
        )
        result = pipeline.read_json(run / "scores.json")
        assert result["scores"]["native-document-control"]["query_accuracy"]["accepted"] == 1
    finally:
        pipeline.METHODS._items.pop("native-document-control", None)


def test_native_reference_view_and_record_associations_are_verified(tmp_path):
    source, _, _ = catalog(tmp_path, "docx")
    data = pipeline.read_json(source)
    p = data["pages"][0]
    p["source"]["view"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="view"):
        queries.qualify_page(p, tmp_path)
    data = pipeline.read_json(source)
    p = data["pages"][0]
    q = copy.deepcopy(p["queries"][0])
    q.update(
        id="items",
        query="List each table row as item and count in source order.",
        schema={
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"item": {"type": "string"}, "count": {"type": "integer"}},
                        "required": ["item", "count"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["rows"],
            "additionalProperties": False,
        },
        answer={"rows": [{"item": "Books", "count": 1}, {"item": "Pens", "count": 3}]},
        evidence={
            "leaves": [
                {"pointer": f"/rows/{i}/{k}", "selector": f"./td[{j + 1}]", "raw_value": str(v)}
                for i, row in enumerate(
                    [{"item": "Books", "count": 1}, {"item": "Pens", "count": 3}]
                )
                for j, (k, v) in enumerate(row.items())
            ],
            "record_regions": {
                f"/rows/{i}": {"target_index": 0, "selector": f".//table/tr[{i + 1}]"}
                for i in range(2)
            },
            "negative_answers": [{"rows": []}],
        },
    )
    p["queries"] = [q]
    queries.qualify_page(p, tmp_path)
    q["answer"]["rows"][0]["count"] = 3
    q["evidence"]["leaves"][1]["raw_value"] = "3"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(p, tmp_path)


@pytest.mark.parametrize("kind", ["pdf", "docx", "txt"])
def test_native_documents_reject_unsupported_gold_and_html_snapshot_routes(tmp_path, kind):
    source, _, _ = catalog(tmp_path, kind)
    data = pipeline.read_json(source)
    page = data["pages"][0]
    page["queries"][0]["answer"]["trials"] = 41
    page["queries"][0]["evidence"]["leaves"][1]["raw_value"] = "41"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)
    data = pipeline.read_json(source)
    data["input_mode"] = "snapshot"
    pipeline.save_json(source, data)
    with pytest.raises(ValueError, match="original live URLs"):
        pipeline.prepare(tmp_path / "snapshot", benchmark="queries", source=source)


def test_native_plain_text_uses_declared_encoding_and_literal_text(tmp_path, monkeypatch):
    source, _, _ = catalog(tmp_path, "txt")
    data = pipeline.read_json(source)
    page = data["pages"][0]
    raw = b"Harbor Lab completed 40 trials. caf\xe9 <script>literal source text</script>"
    (tmp_path / "source.txt").write_bytes(raw)
    view, extractor = document_view(raw, "text/plain", encoding="iso-8859-1")
    assert b"&lt;script&gt;literal source text&lt;/script&gt;" in view
    page["source"].update(
        encoding="iso-8859-1",
        sha256=pipeline.digest(raw),
        view={"sha256": pipeline.digest(view), "extractor": extractor},
    )
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    pipeline.prepare(home, benchmark="queries", source=source)
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    monkeypatch.setattr(
        "harness.http_client.request",
        lambda url, **kw: SimpleNamespace(
            status=200, url=url, body=raw, headers={"content-type": "text/plain"}
        ),
    )
    assert queries.qualify_live(home, list(truth))[0]["reference_values_verified"] == 2
    page["source"]["view"]["extractor"] = "unbound-extractor"
    with pytest.raises(ValueError, match="extractor"):
        queries.qualify_page(page, tmp_path)


def test_native_plain_text_keeps_independent_paragraph_evidence_scopes(tmp_path):
    from lxml import html

    raw = b"Organization: Harbor Lab\nTrials: 40\r\n\r\nOrganization: Other Lab\nTrials: 4"
    view, _ = document_view(raw, "text/plain")
    tree = html.fromstring(view)
    assert tree.xpath("//main/p/@data-block") == ["1", "2"]
    assert tree.xpath("string(//p[@data-block='1'])") == "Organization: Harbor Lab\nTrials: 40"
    assert tree.xpath("string(//p[@data-block='2'])") == "Organization: Other Lab\nTrials: 4"


def test_native_pdf_preserves_independent_line_evidence_scopes():
    from lxml import html

    raw, media_type = source_document("pdf", pdf_lines=[b"Books 1", b"Pens 3"])
    view, _ = document_view(raw, media_type)
    tree = html.fromstring(view)
    assert tree.xpath("//section/@data-page") == ["1"]
    assert tree.xpath("//section/p/@data-line") == ["1", "2"]
    assert tree.xpath("//section/p/text()") == ["Books 1", "Pens 3"]


@pytest.mark.parametrize("span", [1, 2])
def test_docx_reference_view_keeps_horizontal_and_vertical_merged_cell_geometry(span):
    from lxml import html

    def cell(text, properties=""):
        return f"<w:tc><w:tcPr>{properties}</w:tcPr><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"

    horizontal = f'<w:gridSpan w:val="{span}"/>'
    first = cell("Shared category", horizontal + '<w:vMerge w:val="restart"/>')
    continuation = cell("", horizontal + "<w:vMerge/>")
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:tbl><w:tr>"
        + first
        + cell("First", "")
        + "</w:tr><w:tr>"
        + continuation
        + cell("Second", "")
        + "</w:tr></w:tbl></w:body></w:document>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", xml)
    view, _ = document_view(output.getvalue(), DOCX)
    tree = html.fromstring(view)
    origin = tree.xpath("//table/tr[1]/td[1]")[0]
    assert int(origin.get("colspan", "1")) == span
    assert origin.get("rowspan") == "2"
    assert tree.xpath("//table/tr[1]/td[2]/p/text()") == ["First\n"]
    assert tree.xpath("//table/tr[2]/td/p/text()") == ["Second\n"]


@pytest.mark.parametrize(
    "row_properties,cell_properties",
    [
        ("", "<w:vMerge/>"),
        ("", '<w:gridSpan w:val="0"/>'),
        ("", '<w:hMerge w:val="restart"/>'),
        ('<w:gridBefore w:val="1"/>', ""),
    ],
)
def test_docx_reference_view_rejects_geometry_it_cannot_represent(row_properties, cell_properties):
    output = io.BytesIO()
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:tbl><w:tr><w:trPr>{row_properties}</w:trPr>"
        f"<w:tc><w:tcPr>{cell_properties}</w:tcPr><w:p/></w:tc>"
        "</w:tr></w:tbl></w:body></w:document>"
    )
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", xml)
    with pytest.raises(ValueError):
        document_view(output.getvalue(), DOCX)
