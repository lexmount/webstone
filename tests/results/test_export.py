"""Every track's export stage turns a synthetic scored run into a submission that passes
``ResultsClient.check``; no call reaches GitHub."""

import base64
import json
from pathlib import Path

import pytest

from harness.record_schema import RecordSchemaError
from harness.results import ResultsClient, SubmissionError
from harness.results.export import UNPUBLISHED, read_receipt, scored_result_schema

# The submission definitions the exporters target, as the website catalog declares them.
DEFINITIONS = {
    ("lexbench-webfetch", "split-media"): (
        "passRate requestRate totalMean totalP50 totalP95"),
    ("lexbench-websearch", "title-snippet-v2"): (
        "ndcg5 precision5 success5 meanChars meanTime p50Time p95Time pricePerThousand"),
    ("simpleqa-verified", "source-match-v2"): (
        "mrr5 hit5 answerMatch5 meanChars meanTime p50Time p95Time pricePerThousand"),
    ("webarena-verified", "standard"): (
        "successRate taskMean taskP50 taskP95 actionMean actionP50 actionP95"),
    ("lexbench-swde", "standard"): (
        "pageMacroF1 executedPages pagesWithFields valueMicroF1 fieldSetExact pageExact "
        "nullAccuracy timedPages untimedPages meanTime p50Time p95Time"),
}
CATALOG = {"providerUrls": {}, "benchmarks": [
    {"id": benchmark, "definitions": [{"id": definition, "metrics": [
        {"id": metric} for metric in metrics.split()]}]}
    for (benchmark, definition), metrics in DEFINITIONS.items()]}
RECEIPT = {"url": "https://reports.example.com/s/fixture", "version": "v1"}


class CatalogOnly:
    """Serves the catalog; any other GitHub request fails the test."""

    def request(self, method, path, body=None):
        assert (method, path) == ("GET", "/repos/fixture/site/contents/benchmarks/catalog.json")
        content = base64.b64encode(json.dumps(CATALOG).encode()).decode()
        return {"type": "file", "encoding": "base64", "content": content}


def check(report: dict) -> dict:
    urls = {item["id"]: "https://example.com/" + item["id"]
            for item in report["benchmarks"][0]["providers"]}
    return ResultsClient(CatalogOnly(), "fixture/site").check(report, urls)


def assert_exports(export, run, **options) -> dict:
    published = export(run, RECEIPT, **options)
    assert check(published)["preflight"] == "passed"
    assert published["source"]["url"] == published["benchmarks"][0]["reportUrl"] == RECEIPT["url"]
    unpublished = export(run, None, **options)
    assert unpublished["source"]["kind"] == UNPUBLISHED
    assert unpublished["benchmarks"][0]["reportUrl"] is None
    assert unpublished["benchmarks"][0]["providers"] == published["benchmarks"][0]["providers"]
    with pytest.raises(SubmissionError, match="public HTTPS evidence URL"):
        check(unpublished)
    return published


def test_web_fetch_run_exports_through_the_stage_command(tmp_path: Path, monkeypatch) -> None:
    from harness.cli import main
    from harness.web_fetch.export import export
    from tests.baselines.test_web_fetch import pin_environment, webstone

    pin_environment(monkeypatch)
    run = tmp_path / "run"
    webstone("run", "--delay", "0", "--no-report", "--output", str(run))
    result = assert_exports(export, run)["benchmarks"][0]
    fixture = result["providers"][0]
    assert (result["taskCount"], fixture["passed"], fixture["scores"]["passRate"]) == (2, 1, 50.0)
    assert result["categoryTotals"]["static"] == 2

    results = run / "results.jsonl"
    complete = results.read_text(encoding="utf-8")
    other = complete.splitlines()[0].replace('"provider": "fixture"', '"provider": "second"')
    results.write_text(complete + other + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete: second"):
        export(run)
    results.write_text(complete, encoding="utf-8")

    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"share_url": RECEIPT["url"], "versionId": "v2"}),
                       encoding="utf-8")
    output = tmp_path / "submission.json"
    main(["webfetch", "export", str(run), "--receipt", str(receipt), "--output", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["source"]["version"] == "v2"
    assert check(report)["preflight"] == "passed"


def test_web_search_run_exports_each_benchmark(tmp_path: Path) -> None:
    from harness.web_search.example import example
    from harness.web_search.export import export

    example(tmp_path, comparison=True)
    run = tmp_path / "fixture"
    with pytest.raises(ValueError, match="--benchmark"):
        export(run)
    factual = assert_exports(export, run, benchmark="simpleqa-verified")["benchmarks"][0]
    assert [p["scores"]["hit5"] for p in factual["providers"]] == [100.0, 0.0]
    scenario = assert_exports(export, run, benchmark="lexbench-websearch")["benchmarks"][0]
    assert scenario["providers"][0]["scores"]["ndcg5"] is None


def test_web_extract_run_exports(tmp_path: Path, monkeypatch) -> None:
    from harness.web_extract.example import create
    from harness.web_extract.export import export

    home = Path(create(tmp_path / "report", mode="field-extraction-cohort"))
    result = assert_exports(export, home / "runs/scrapegraph-extract")["benchmarks"][0]
    scores = result["providers"][0]["scores"]
    assert (scores["executedPages"], scores["pagesWithFields"], scores["timedPages"]) == (3, 2, 3)
    partial = export(home / "runs/firecrawl-json", RECEIPT)["benchmarks"][0]
    assert partial["providers"][0]["scores"]["pageMacroF1"] is None

    # A retried call leaves both attempts in the journal; the latest one is the result.
    run = home / "runs/scrapegraph-extract"
    journal = run / "calls.journal.jsonl"
    first = journal.read_text(encoding="utf-8").splitlines()[0]
    journal.write_text(first + "\n" + journal.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(run.parent)
    retried = export(Path(run.name), RECEIPT)["benchmarks"][0]
    assert retried["providers"] == result["providers"]


def test_browser_use_run_exports(tmp_path: Path, monkeypatch) -> None:
    from harness.browser_use.export import export
    from tests.browser_use.test_report import build_browser_use_fixture

    _, evidence, _ = build_browser_use_fixture(tmp_path, monkeypatch, "en")
    result = assert_exports(export, evidence)["benchmarks"][0]
    chromium = next(item for item in result["providers"] if item["id"] == "chromium")
    # The retry decides the verdict; task time adds both executions.
    assert (chromium["passed"], chromium["scores"]["taskMean"]) == (1, 1.9)
    assert chromium["samples"]["action"] > 0


def test_trajectory_replay_results_export(tmp_path: Path, monkeypatch) -> None:
    from harness.trajectory_replay.export import export
    from tests.trajectory_replay.test_webarena_report import build_replay_fixture

    build_replay_fixture(tmp_path, monkeypatch, failed=True)
    result = assert_exports(export, tmp_path)["benchmarks"][0]
    assert [item["passed"] for item in result["providers"]] == [1, 0, 1]
    assert result["providers"][0]["scores"]["taskMean"] == 0.1


def test_receipts_name_the_public_report(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"host": "https://reports.example.com/", "slug": "abc",
                                   "expected_version": "v3"}), encoding="utf-8")
    assert read_receipt(receipt) == {"url": "https://reports.example.com/s/abc", "version": "v3"}
    receipt.write_text(json.dumps({"slug": "abc"}), encoding="utf-8")
    with pytest.raises(ValueError, match="public HTTPS"):
        read_receipt(receipt)


def test_nullable_role_accepts_only_null_beyond_its_type() -> None:
    schema = scored_result_schema("fixture.scored_result", nullable=("elapsed",))
    record = {"method": "m", "case": "c", "elapsed_ms": None, "evidence": "e", "passed": True}
    assert schema.validate(record) == record
    with pytest.raises(RecordSchemaError, match="passed"):
        schema.validate({**record, "passed": None})


def test_methods_that_share_a_submission_id_are_rejected() -> None:
    from harness.results.export import provider, submission

    methods = [provider(name, {}, mode="m", description="d") for name in ("a-b", "A_B")]
    assert [item["id"] for item in methods] == ["a-b", "a-b"]
    with pytest.raises(ValueError, match="a-b, A_B"):
        submission(benchmark="b", definition="d", task_count=1, providers=methods,
                   receipt=None, track="T", scope="Synthetic scope.")
