"""Public preparation, native-query delivery and source-specific answer acceptance."""

import copy
import importlib.util
import json
from decimal import Decimal

import pytest

from harness.web_extract import pipeline, queries, query_contract
from harness.web_extract.cli import main

pytestmark = pytest.mark.usefixtures("stubbed_serial_collection")


def schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def catalog(root):
    article = b"""<html><nav>Site navigation: header-only</nav><main id="story">
    <h1>Harbor Research</h1><p id="org">Harbor Lab</p><p id="trials">40</p>
    <section id="contact">No contact is supplied.</section></main></html>"""
    static = b"""<html><header>Catalog</header><section id="products"></section>
    <script>fetch('/api/products').then(renderProducts)</script></html>"""
    rendered = b"""<html><header>Catalog</header><section id="products"><article>
    <h2>Desk Lamp</h2><p class="price">\xc2\xa329.50</p></article></section></html>"""
    sources = {}
    for name, raw in (("article", article), ("static", static), ("rendered", rendered)):
        (root / (name + ".html")).write_bytes(raw)
        sources[name] = pipeline.digest(raw)
    base = {
        "source_versions": {"synthetic": "1" * 40},
        "selection": {"purpose": "synthetic controls"},
        "schema": "web-extract-query-catalog/1",
        "id": "synthetic-query-catalog",
        "name": "Synthetic query contracts",
        "repository": "example/synthetic",
        "source_license": "Synthetic fixture only",
        "input_mode": "snapshot",
    }
    pages = [
        {
            "id": "article",
            "url": "https://source.example/article",
            "content_kind": "article",
            "required_characteristics": ["article_body"],
            "source": {
                "input": "article.html",
                "sha256": sources["article"],
                "annotation_status": "verified",
            },
            "regions": [
                {
                    "id": "body",
                    "primary": True,
                    "selector": "//main[@id='story']",
                    "characteristics": ["article_body"],
                },
                {"id": "nav", "primary": False, "selector": "//nav", "characteristics": []},
            ],
            "queries": [
                {
                    "id": "research",
                    "query": "From the article body extract the organization and trial count.",
                    "schema": schema(
                        {"organization": {"type": "string"}, "trials": {"type": "integer"}}
                    ),
                    "answer": {"organization": "Harbor Lab", "trials": 40},
                    "selection": {
                        "region_id": "body",
                        "rationale": "The study's organization and trial count are article facts.",
                    },
                    "evidence": {
                        "leaves": [
                            {
                                "pointer": "/organization",
                                "selector": ".//p[@id='org']",
                                "raw_value": "Harbor Lab",
                            },
                            {
                                "pointer": "/trials",
                                "selector": ".//p[@id='trials']",
                                "raw_value": "40",
                            },
                        ],
                        "negative_answers": [{"organization": "header-only", "trials": 0}],
                    },
                },
                {
                    "id": "contact",
                    "query": "Extract the article's contact email; null when no email is supplied.",
                    "schema": schema({"email": {"type": ["string", "null"]}}),
                    "answer": {"email": None},
                    "selection": {
                        "region_id": "body",
                        "rationale": "Check an absent contact field inside the article body.",
                    },
                    "evidence": {
                        "absences": [
                            {"pointer": "/email", "selector": ".//a[starts-with(@href, 'mailto:')]"}
                        ],
                        "negative_answers": [{"email": "invented@example.com"}],
                    },
                },
            ],
        },
        {
            "id": "catalog",
            "url": "https://source.example/products",
            "content_kind": "product_catalog",
            "required_characteristics": ["javascript"],
            "source": {
                "input": "static.html",
                "sha256": sources["static"],
                "rendered_input": "rendered.html",
                "rendered_sha256": sources["rendered"],
                "annotation_status": "verified",
            },
            "regions": [
                {
                    "id": "products",
                    "primary": True,
                    "selector": "//section[@id='products']/article",
                    "characteristics": ["javascript", "product_records"],
                }
            ],
            "queries": [
                {
                    "id": "product",
                    "query": "Extract the rendered product name and price in GBP.",
                    "schema": schema({"name": {"type": "string"}, "price_gbp": {"type": "number"}}),
                    "answer": {"name": "Desk Lamp", "price_gbp": 29.5},
                    "selection": {
                        "region_id": "products",
                        "rationale": "The product records appear after asynchronous rendering.",
                    },
                    "evidence": {
                        "leaves": [
                            {"pointer": "/name", "selector": ".//h2", "raw_value": "Desk Lamp"},
                            {
                                "pointer": "/price_gbp",
                                "selector": ".//p[@class='price']",
                                "raw_value": "£29.50",
                            },
                        ],
                        "negative_answers": [{"name": "Catalog", "price_gbp": 0}],
                    },
                }
            ],
        },
    ]
    for page in pages:
        page["source"].update(observed_at="2026-01-01T00:00:00Z", method="Synthetic source fixture")
    path = root / "catalog.json"
    pipeline.save_json(path, {**base, "pages": pages})
    return path


def prepare(tmp_path, unit="query", *, draft=False):
    source = catalog(tmp_path)
    if not draft:
        data = pipeline.read_json(source)
        frozen = data["pages"][1]
        frozen["required_characteristics"] = ["product_records"]
        frozen["source"]["input"] = "rendered.html"
        frozen["source"]["sha256"] = frozen["source"]["rendered_sha256"]
        pipeline.save_json(source, data)
    home = tmp_path / (unit + ("-draft" if draft else ""))
    main(
        [
            "--home",
            str(home),
            "prepare",
            "--benchmark",
            "queries",
            "--source",
            str(source),
            "--unit",
            unit,
            *(["--draft"] if draft else []),
        ]
    )
    return source, home


@pytest.mark.parametrize("unit,count", [("query", 3), ("page", 2)])
def test_prepare_units_keep_url_characteristics_and_answers_bound(tmp_path, unit, count):
    _, home = prepare(tmp_path, unit)
    manifest = pipeline.validate_dataset(home)
    assert manifest["cases"] == count and manifest["task_unit"] == unit
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    assert len(truth) == count
    assert sum(len(case["task"]["queries"]) for case in truth.values()) == 3
    for case in truth.values():
        assert case["url"].startswith("https://source.example/")
        assert case["query"] and case["task"]["required_characteristics"]
    assert any(
        name.startswith("references/") and name.endswith("rendered.html")
        for name in manifest["files"]
    )
    assert (
        queries.score(truth, {cid: case["values"] for cid, case in truth.items()})[
            "query_accuracy"
        ]["rate"]
        == 1
    )
    spec = importlib.util.spec_from_file_location(
        "frozen_query_evaluator", home / "dataset/evaluate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        module.score(truth, {cid: case["values"] for cid, case in truth.items()})[
            "page_all_correct"
        ]["rate"]
        == 1
    )


def test_draft_export_cannot_enter_collection(tmp_path):
    source, home = prepare(tmp_path, "page", draft=True)
    requests = pipeline.read_json(home / "requests.json")
    assert len(requests) == 2
    assert all(set(case["input"]) == {"url", "query", "schema"} for case in requests)
    assert "answer" not in requests[0]["input"]
    with pytest.raises(ValueError, match="Draft query catalogs"):
        pipeline.validate_dataset(home)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("navigation", "primary content"),
        ("static-characteristic", "characteristics"),
        ("no-render", "Dynamic tasks"),
        ("present-before-js", "Dynamic tasks"),
        ("wrong-evidence", "evidence"),
        ("missing-control", "controls"),
        ("unverified", "annotation verification"),
        ("judge", "model judges"),
    ],
)
def test_characteristic_and_primary_content_admission(tmp_path, mutation, match):
    source = catalog(tmp_path)
    value = pipeline.read_json(source)
    article, dynamic = value["pages"]
    if mutation == "navigation":
        article["queries"][0]["selection"]["region_id"] = "nav"
    elif mutation == "static-characteristic":
        dynamic["regions"][0]["characteristics"] = ["product_records"]
    elif mutation == "no-render":
        del dynamic["source"]["rendered_input"]
    elif mutation == "present-before-js":
        dynamic["source"]["input"] = "rendered.html"
        dynamic["source"]["sha256"] = dynamic["source"]["rendered_sha256"]
    elif mutation == "wrong-evidence":
        article["queries"][0]["evidence"]["leaves"][0]["raw_value"] = "Wrong Organization"
    elif mutation == "missing-control":
        article["queries"][0]["evidence"]["negative_answers"] = []
    elif mutation == "unverified":
        article["source"]["annotation_status"] = "draft"
    elif mutation == "judge":
        article["queries"][0]["judge"] = {"model": "unspecified"}
    pipeline.save_json(source, value)
    with pytest.raises(ValueError, match=match):
        pipeline.prepare(tmp_path / "home", benchmark="queries", source=source)


@pytest.mark.parametrize("unit", ["query", "page"])
def test_query_verdicts_survive_packaging_and_wrong_sibling(tmp_path, unit):
    _, home = prepare(tmp_path, unit)
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    predictions = {cid: copy.deepcopy(case["values"]) for cid, case in truth.items()}
    target = next(
        cid for cid, case in truth.items() if case["task"]["queries"][0]["id"] == "research"
    )
    bad = predictions[target] if unit == "query" else predictions[target]["research"]
    bad["trials"] = 0
    result = queries.score(truth, predictions)
    assert result["query_accuracy"] == {"accepted": 2, "total": 3, "rate": 2 / 3}
    assert result["page_all_correct"]["accepted"] == 1
    assert next(row for row in result["queries"] if row["query_id"] == "contact")["accepted"]


@pytest.mark.parametrize("raw", ['{"x": 1, "x": 2}', '{"x": NaN}', "```json\n{}\n```"])
def test_native_invalid_json_cannot_become_valid_after_projection(raw):
    with pytest.raises(ValueError):
        query_contract.strict_json(raw)
    assert "non_json_response" in pipeline.decoded_response(raw)


def test_native_decimal_values_and_types_are_preserved_for_scoring():
    value = queries.scored_prediction(
        {
            "method": "cloudflare-json",
            "response_text": '{"result":{"price":1234567890.123456789,"sku":"0007"}}',
        },
        {},
    )
    assert value == {"price": Decimal("1234567890.123456789"), "sku": "0007"}


def test_record_relationship_order_duplicates_and_empty_selections():
    row_schema = schema({"company": {"type": "string"}, "contact": {"type": "string"}})
    query = {
        "id": "companies",
        "schema": schema({"companies": {"type": "array", "items": row_schema}}),
        "answer": {
            "companies": [{"company": "A", "contact": "Ada"}, {"company": "B", "contact": "Grace"}]
        },
    }
    swapped = {
        "companies": [{"company": "A", "contact": "Grace"}, {"company": "B", "contact": "Ada"}]
    }
    assert not query_contract.validate_query(query, swapped)["accepted"]
    reversed_rows = {"companies": list(reversed(query["answer"]["companies"]))}
    assert not query_contract.validate_query(query, reversed_rows)["accepted"]
    unordered = {**query, "equality": {"paths": {"/companies": {"array": "unordered"}}}}
    assert query_contract.validate_query(unordered, reversed_rows)["accepted"]
    reversed_rows["companies"].append(reversed_rows["companies"][0])
    assert not query_contract.validate_query(unordered, reversed_rows)["accepted"]
    empty = {**query, "answer": {"companies": []}}
    assert query_contract.validate_query(empty, {"companies": []})["accepted"]
    assert not query_contract.validate_query(empty, query["answer"])["accepted"]


def test_javascript_classification_rejects_answers_already_embedded_in_static_script(tmp_path):
    source = catalog(tmp_path)
    value = pipeline.read_json(source)
    page = value["pages"][1]
    path = tmp_path / page["source"]["input"]
    path.write_bytes(
        path.read_bytes()
        + b'<script>var data={"name":"Desk Lamp","price":"\xc2\xa329.50"};</script>'
    )
    page["source"]["sha256"] = pipeline.digest(path.read_bytes())
    pipeline.save_json(source, value)
    with pytest.raises(ValueError, match="embedded in static source"):
        queries.prepare(tmp_path / "home", source)


def test_source_inventory_keeps_unsupported_pages_and_checks_catalog_url_bindings(
    tmp_path, monkeypatch
):
    from harness.benchmarks import data

    source = catalog(tmp_path)
    authored = pipeline.read_json(source)
    for page in authored["pages"]:
        page["source_task_ids"] = [page["id"]]
    pipeline.save_json(source, authored)
    tasks = {
        "cases": [{"id": p["id"], "url": p["url"], "track": "web"} for p in authored["pages"]]
        + [{"id": "pending", "url": "https://source.example/blocked", "track": "web"}]
    }
    task_path = tmp_path / "tasks.json"
    pipeline.save_json(task_path, tasks)
    selected = {
        "repository": "example/source",
        "revision": "a" * 40,
        "datasets": {
            "pages": {
                "path": "tasks.json",
                "count": 3,
                "sha256": pipeline.digest(task_path.read_bytes()),
            }
        },
    }
    monkeypatch.setattr(data, "binding", lambda _: selected)
    monkeypatch.setattr(data, "definition_path", lambda *args: task_path)
    output = tmp_path / "inventory.json"
    summary = queries.source_inventory("synthetic", "pages", output, catalog=source)
    assert summary["tasks"] == 3 and summary["authored_queries"] == 3
    rows = pipeline.read_json(output)["cases"]
    assert rows[-1]["state"] == "pending_page_inspection" and rows[-1]["queries"] == []
    authored["pages"][0]["url"] = "https://wrong.example/"
    pipeline.save_json(source, authored)
    with pytest.raises(ValueError, match="source task URL"):
        queries.source_inventory("synthetic", "pages", tmp_path / "bad.json", catalog=source)


def test_reference_record_fields_must_be_grounded_in_the_same_source_row(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(
        b"</main>",
        b"""<table>
    <tr><td>A</td><td>Ada</td></tr><tr><td>B</td><td>Grace</td></tr>
    </table></main>""",
    )
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    row_schema = schema({"company": {"type": "string"}, "contact": {"type": "string"}})
    query = {
        "id": "rows",
        "query": "Extract the table company and contact rows.",
        "schema": schema({"rows": {"type": "array", "items": row_schema}}),
        "answer": {
            "rows": [{"company": "A", "contact": "Ada"}, {"company": "B", "contact": "Grace"}]
        },
        "selection": {"region_id": "body", "rationale": "The article's primary data table."},
        "evidence": {
            "record_regions": {
                "/rows/0": {"target_index": 0, "selector": ".//tr[1]"},
                "/rows/1": {"target_index": 0, "selector": ".//tr[2]"},
            },
            "leaves": [
                {"pointer": "/rows/0/company", "selector": "./td[1]", "raw_value": "A"},
                {"pointer": "/rows/0/contact", "selector": "./td[2]", "raw_value": "Ada"},
                {"pointer": "/rows/1/company", "selector": "./td[1]", "raw_value": "B"},
                {"pointer": "/rows/1/contact", "selector": "./td[2]", "raw_value": "Grace"},
            ],
            "negative_answers": [{"rows": []}],
        },
    }
    page["queries"] = [query]
    queries.qualify_page(page, tmp_path)
    query["answer"]["rows"][0]["contact"] = "Grace"
    query["answer"]["rows"][1]["contact"] = "Ada"
    query["evidence"]["leaves"][1]["raw_value"] = "Grace"
    query["evidence"]["leaves"][3]["raw_value"] = "Ada"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)


def synthetic_request(method, html, source_url, fixture_url, **options):
    from lxml import html as parser

    tree = parser.fromstring(html)
    fact = {
        "organization": tree.xpath("string(//p[@id='org'])"),
        "trials": int(tree.xpath("string(//p[@id='trials'])")),
    }
    properties = options["schema"]["properties"]
    if "research" in properties:
        answer = {"research": fact, "contact": {"email": None}}
    elif "organization" in properties:
        answer = fact
    else:
        answer = {"email": None}
    return {
        "state": "response_received",
        "http_status": 200,
        "elapsed_seconds": 0.01,
        "response": {"result": answer},
        "request": {"schema": options["schema"], "source_url": source_url},
    }


@pytest.mark.parametrize("unit", ["query", "page"])
def test_existing_collector_delivers_task_queries_and_scores_without_new_runner(
    tmp_path, monkeypatch, unit
):
    source = catalog(tmp_path)
    value = pipeline.read_json(source)
    value["pages"] = value["pages"][:1]
    pipeline.save_json(source, value)
    home = tmp_path / "home"
    pipeline.prepare(home, benchmark="queries", source=source, unit=unit)

    pipeline.METHODS.register(
        "query-synthetic",
        pipeline.ExtractMethod(
            name="query-synthetic",
            keys=(),
            input="html",
            request=synthetic_request,
            project=lambda payload, preserve: payload.get("result"),
            custom_schema=True,
        ),
    )
    try:
        monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
        initial = tmp_path / "preflight-plan.json"
        assessment = tmp_path / "assessment.json"
        plan_path = tmp_path / "plan.json"
        main(
            ["--home", str(home), "plan", "--methods", "query-synthetic", "--output", str(initial)]
        )
        main(
            [
                "--home",
                str(home),
                "preflight",
                "--profile",
                str(initial),
                "--method",
                "query-synthetic",
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
                "query-synthetic",
                "--output",
                str(plan_path),
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
                str(plan_path),
                "--method",
                "query-synthetic",
                "--run-dir",
                str(run),
            ]
        )
        main(["--home", str(home), "score", str(run), "--output", str(tmp_path / "rescored.json")])
        result = pipeline.read_json(tmp_path / "rescored.json")
        plan = pipeline.read_json(plan_path)
        assert result["scores"]["query-synthetic"]["query_accuracy"]["rate"] == 1
        for row in pipeline.load_run(run)[1]:
            text = " ".join(
                field.get("description", "")
                for field in row["request"]["schema"]["properties"].values()
            )
            assert "article" in text
            assert "contact email" in text or "trial count" in text
        assert plan["case_count"] == (2 if unit == "query" else 1)
    finally:
        pipeline.METHODS.unregister("query-synthetic")


@pytest.mark.parametrize("keyword", ["const", "enum", "multipleOf", "minimum", "maximum"])
@pytest.mark.parametrize("unit", ["query", "page"])
def test_native_decimal_schema_constraints_match_reference_numbers(tmp_path, keyword, unit):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    data["pages"] = data["pages"][:1]
    query = data["pages"][0]["queries"][0]
    data["pages"][0]["queries"] = [query]
    constraint = [0.1] if keyword == "enum" else 0.1
    query.update(
        schema=schema({"amount": {"type": "number", keyword: constraint}}), answer={"amount": 0.1}
    )
    query["schema"]["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    cases = query_contract.compile_cases(data, unit)
    case = cases[0]
    truth = {case["id"]: {"task": {"unit": unit, "page_id": "article", "queries": [query]}}}
    good = '{"amount": 0.1}' if unit == "query" else '{"research": {"amount": 1e-1}}'
    bad_value = 0 if keyword == "minimum" else 0.15
    bad = {"amount": bad_value}
    if unit == "page":
        bad = {"research": bad}
    for text, accepted in [(good, True), (json.dumps(bad), False)]:
        prediction = queries.scored_prediction(
            {"method": "cloudflare-json", "response_text": '{"result":' + text + "}"}, {}
        )
        result = queries.score(truth, {case["id"]: prediction})
        assert bool(result["query_accuracy"]["accepted"]) == accepted
        assert result["requests"][0]["schema_valid"] == accepted


def test_unordered_native_numbers_keep_equivalence_and_duplicate_counts():
    query = {
        "id": "values",
        "schema": schema({"values": {"type": "array", "items": {"type": "number"}}}),
        "answer": {"values": [10, 11, 10]},
        "equality": {"paths": {"/values": {"array": "unordered"}}},
    }
    for raw, accepted in [
        (' {"values": [11, 1e1, 10.0]}', True),
        (' {"values": [11, 1e1, 11]}', False),
    ]:
        assert (
            query_contract.validate_query(query, query_contract.strict_json(raw))["accepted"]
            == accepted
        )


@pytest.mark.parametrize("unit", ["query", "page"])
def test_local_schema_references_work_in_both_request_units(tmp_path, unit):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    data["pages"] = data["pages"][:1]
    query = data["pages"][0]["queries"][0]
    query["schema"]["$defs"] = {"count": {"type": "integer", "minimum": 1}}
    query["schema"]["properties"]["trials"] = {"$ref": "#/$defs/count"}
    query["evidence"]["negative_answers"] = [{"organization": "Navigation", "trials": 1}]
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    pipeline.prepare(home, benchmark="queries", source=source, unit=unit)
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    predictions = {cid: case["values"] for cid, case in truth.items()}
    assert queries.score(truth, predictions)["query_accuracy"]["rate"] == 1
    cid = next(iter(truth))
    bad = copy.deepcopy(predictions)
    if unit == "page":
        bad[cid]["research"]["trials"] = 0
    else:
        bad[cid]["trials"] = 0
    assert queries.score(truth, bad)["query_accuracy"]["rate"] < 1


def test_one_source_record_cannot_qualify_two_sibling_answer_records(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    query = page["queries"][0]
    query.update(
        schema=schema({"rows": {"type": "array", "items": schema({"name": {"type": "string"}})}}),
        answer={"rows": [{"name": "Harbor Lab"}, {"name": "Harbor Lab"}]},
    )
    query["evidence"] = {
        "record_regions": {
            f"/rows/{i}": {"target_index": 0, "selector": ".//p[@id='org']"} for i in range(2)
        },
        "leaves": [
            {"pointer": f"/rows/{i}/name", "raw_value": "Harbor Lab", "selector": "."}
            for i in range(2)
        ],
        "negative_answers": [{"rows": []}],
    }
    with pytest.raises(ValueError, match="distinct source records"):
        queries.qualify_page(page, tmp_path)
    query["answer"]["rows"].pop()
    del query["evidence"]["record_regions"]["/rows/1"]
    query["evidence"]["leaves"].pop()
    queries.qualify_page(page, tmp_path)
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b"</main>", b'<p id="other">Harbor Lab</p></main>')
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query["answer"]["rows"].append({"name": "Harbor Lab"})
    query["evidence"]["record_regions"]["/rows/1"] = {
        "target_index": 0,
        "selector": ".//p[@id='other']",
    }
    query["evidence"]["leaves"].append(
        {"pointer": "/rows/1/name", "raw_value": "Harbor Lab", "selector": "."}
    )
    queries.qualify_page(page, tmp_path)


@pytest.mark.parametrize("claimed", [4, 0])
def test_partial_numeric_lexemes_cannot_qualify_reference_counts(tmp_path, claimed):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    query = page["queries"][0]
    query["answer"]["trials"] = claimed
    query["evidence"]["leaves"][1]["raw_value"] = str(claimed)
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)
    query["answer"]["trials"] = 40
    query["evidence"]["leaves"][1]["raw_value"] = "40"
    queries.qualify_page(page, tmp_path)


def test_snapshot_cannot_serve_a_shell_whose_answers_only_exist_after_rendering(tmp_path):
    source = catalog(tmp_path)
    with pytest.raises(ValueError, match="Snapshot input does not contain"):
        pipeline.prepare(tmp_path / "home", benchmark="queries", source=source)
    assert not (tmp_path / "home/dataset").exists()


def test_inline_markup_preserves_literal_identifiers_during_source_qualification(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b"Harbor Lab", b"<span>api.</span><span>fetch</span>")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query.update(schema=schema({"name": {"type": "string"}}), answer={"name": "api.fetch"})
    query["evidence"] = {
        "leaves": [{"pointer": "/name", "raw_value": "api.fetch", "selector": ".//p[@id='org']"}],
        "negative_answers": [{"name": "Navigation"}],
    }
    page["queries"] = [query]
    queries.qualify_page(page, tmp_path)
    query["answer"]["name"] = "api. fetch"
    query["evidence"]["leaves"][0]["raw_value"] = "api. fetch"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)


@pytest.mark.parametrize("suffix", [b".", b", followed by discussion"])
def test_sentence_punctuation_is_not_part_of_numeric_evidence(tmp_path, suffix):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b">40<", b">40" + suffix + b"<")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    queries.qualify_page(page, tmp_path)


def test_tail_text_outside_the_primary_region_cannot_support_an_answer(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b"</main>", b"</main>Outside")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query.update(schema=schema({"name": {"type": "string"}}), answer={"name": "Outside"})
    query["evidence"] = {
        "leaves": [
            {"pointer": "/name", "raw_value": "Outside", "selector": "following-sibling::text()[1]"}
        ],
        "negative_answers": [{"name": "Navigation"}],
    }
    page["queries"] = [query]
    with pytest.raises(ValueError, match="escapes"):
        queries.qualify_page(page, tmp_path)
    query["answer"]["name"] = "Harbor Lab"
    query["evidence"]["leaves"][0].update(raw_value="Harbor Lab", selector=".//p[@id='org']/text()")
    queries.qualify_page(page, tmp_path)


def test_nested_answer_records_must_belong_to_their_bound_parent(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(
        b"</main>",
        b'<section id="a"><b>A</b><i>Alpha</i></section>'
        b'<section id="b"><b>B</b><i>Beta</i></section></main>',
    )
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query.update(
        schema=schema(
            {
                "groups": {
                    "type": "array",
                    "items": schema(
                        {
                            "name": {"type": "string"},
                            "items": {
                                "type": "array",
                                "items": schema({"label": {"type": "string"}}),
                            },
                        }
                    ),
                }
            }
        ),
        answer={"groups": [{"name": "A", "items": [{"label": "Beta"}]}]},
    )
    query["evidence"] = {
        "record_regions": {
            "/groups/0": {"target_index": 0, "selector": ".//section[@id='a']"},
            "/groups/0/items/0": {"target_index": 0, "selector": ".//section[@id='b']/i"},
        },
        "leaves": [
            {"pointer": "/groups/0/name", "raw_value": "A", "selector": "./b"},
            {"pointer": "/groups/0/items/0/label", "raw_value": "Beta", "selector": "."},
        ],
        "negative_answers": [{"groups": []}],
    }
    page["queries"] = [query]
    with pytest.raises(ValueError, match="bound parent"):
        queries.qualify_page(page, tmp_path)
    query["answer"]["groups"][0]["items"][0]["label"] = "Alpha"
    query["evidence"]["record_regions"]["/groups/0/items/0"]["selector"] = ".//section[@id='a']/i"
    query["evidence"]["leaves"][1]["raw_value"] = "Alpha"
    queries.qualify_page(page, tmp_path)


@pytest.mark.parametrize("location", ["allOf", "$defs"])
@pytest.mark.parametrize("unit", ["query", "page"])
def test_boolean_subschemas_can_compile_in_both_units(tmp_path, location, unit):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    data["pages"] = data["pages"][:1]
    contract = data["pages"][0]["queries"][0]["schema"]
    contract[location] = [True] if location == "allOf" else {"permitted": True}
    query_contract.compile_cases(data, unit)


@pytest.mark.parametrize(
    "location",
    [
        "prefixItems",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "patternProperties",
        "propertyNames",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentSchema",
    ],
)
@pytest.mark.parametrize("reference_keyword", ["$ref", "$dynamicRef"])
def test_external_schema_references_are_rejected_in_every_schema_location(
    tmp_path, location, reference_keyword
):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    contract = data["pages"][0]["queries"][0]["schema"]
    child = {reference_keyword: "https://untrusted.invalid/schema"}
    nested = (
        [child]
        if location == "prefixItems"
        else {"unused": child}
        if location in {"dependentSchemas", "patternProperties"}
        else child
    )
    # Admit a valid reference answer without reaching this branch, then verify
    # admission still rejects the external lookup before predictions can reach it.
    contract["if"] = False
    contract["then"] = {location: nested}
    with pytest.raises(ValueError, match="external references"):
        query_contract.compile_cases(data)
    child[reference_keyword] = "#/$defs/unused"
    contract["$defs"] = {"unused": True}
    query_contract.compile_cases(data)


@pytest.mark.parametrize("unit", ["query", "page"])
def test_nested_dialect_keeps_exact_native_decimal_validation(tmp_path, unit):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    data["pages"] = data["pages"][:1]
    query = data["pages"][0]["queries"][0]
    query.update(
        schema=schema(
            {
                "amount": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "number",
                    "const": 0.1,
                }
            }
        ),
        answer={"amount": 0.1},
    )
    data["pages"][0]["queries"] = [query]
    case = query_contract.compile_cases(data, unit)[0]
    value = query_contract.strict_json('{"amount": 0.1}')
    if unit == "page":
        value = {"research": value}
    truth = {case["id"]: {"task": {"unit": unit, "page_id": "article", "queries": [query]}}}
    result = queries.score(truth, {case["id"]: value})
    assert result["request_accuracy"]["rate"] == 1


def test_local_references_cannot_reach_unadmitted_schema_locations(tmp_path):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    contract = data["pages"][0]["queries"][0]["schema"]
    contract["properties"]["trials"] = {
        "anyOf": [
            {"type": "integer"},
            {"$ref": "#/hidden"},
        ]
    }
    contract["hidden"] = {"$ref": "https://untrusted.invalid/schema"}
    with pytest.raises(ValueError, match="declared schema locations"):
        query_contract.compile_cases(data)
    del contract["hidden"]
    contract["$defs"] = {"count": {"type": "integer"}}
    contract["properties"]["trials"]["anyOf"][1]["$ref"] = "#/$defs/count"
    query_contract.compile_cases(data)


@pytest.mark.parametrize("index", ["-1", "00"])
def test_local_schema_pointer_requires_a_valid_array_index(tmp_path, index):
    source = catalog(tmp_path)
    data = pipeline.read_json(source)
    contract = data["pages"][0]["queries"][0]["schema"]
    contract["prefixItems"] = [{"type": "integer"}]
    branch = {"$ref": "#/prefixItems/" + index}
    contract["properties"]["trials"] = {"anyOf": [{"type": "integer"}, branch]}
    with pytest.raises(ValueError, match="declared schema locations"):
        query_contract.compile_cases(data)
    branch["$ref"] = "#/prefixItems/0"
    query_contract.compile_cases(data)


def test_unsigned_suffix_of_a_signed_number_cannot_support_an_answer(tmp_path):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b">40<", b">USD-12<")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query["answer"]["trials"] = 12
    query["evidence"]["leaves"][1]["raw_value"] = "12"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)
    raw = raw.replace(b"USD-12", b"USD -12")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query["answer"]["trials"] = -12
    query["evidence"]["leaves"][1]["raw_value"] = "-12"
    queries.qualify_page(page, tmp_path)


@pytest.mark.parametrize("value,source_word", [(True, "untrue"), (False, "falsehood")])
def test_boolean_evidence_requires_the_complete_word(tmp_path, value, source_word):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    original = path.read_bytes()
    raw = original.replace(b">40<", b">" + source_word.encode() + b"<")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query.update(schema=schema({"flag": {"type": "boolean"}}), answer={"flag": value})
    query["evidence"] = {
        "leaves": [
            {"pointer": "/flag", "raw_value": str(value).lower(), "selector": ".//p[@id='trials']"}
        ],
        "negative_answers": [{"flag": not value}],
    }
    page["queries"] = [query]
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)
    raw = original.replace(b">40<", b">" + str(value).lower().encode() + b"<")
    path.write_bytes(raw)
    page["source"]["sha256"] = pipeline.digest(raw)
    queries.qualify_page(page, tmp_path)


@pytest.mark.parametrize(
    "source_text,negative_lexeme",
    [
        ("-$5", "-$5"),
        ("-£5", "-£5"),
        ("USD-$5", "-$5"),
        ("US$-5", "$-5"),
    ],
)
def test_currency_decoration_cannot_hide_a_negative_sign(tmp_path, source_text, negative_lexeme):
    source = catalog(tmp_path)
    page = pipeline.read_json(source)["pages"][0]
    path = tmp_path / "article.html"
    raw = path.read_bytes().replace(b">40<", b">" + source_text.encode() + b"<")
    path.write_bytes(raw)
    page["source"]["encoding"] = "utf-8"
    page["source"]["sha256"] = pipeline.digest(raw)
    query = page["queries"][0]
    query["answer"]["trials"] = 5
    query["evidence"]["leaves"][1]["raw_value"] = "£5" if "£" in source_text else "$5"
    with pytest.raises(ValueError, match="evidence"):
        queries.qualify_page(page, tmp_path)
    query["answer"]["trials"] = -5
    query["evidence"]["leaves"][1]["raw_value"] = negative_lexeme
    queries.qualify_page(page, tmp_path)
