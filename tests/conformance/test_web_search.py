"""Web Search conformance: a synthetic adapter and benchmark through every offline stage.

The synthetic search transport answers from this process at the network boundary
(``http_call``), so ``run`` collects and scores a certified cohort without network or
credentials. ``report``, ``check`` and ``export`` then run through
``webstone web-search``. Publishing needs a Yours site, so the test stops before it.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.web_search import adapters, runner
from harness.web_search.benchmarks import BENCHMARKS, SearchBenchmark, ordered
from tests.conformance.helpers import declared_stages, registered, webstone

ADAPTER = "conformance-search"
METHOD = "conformance-method"
BENCHMARK = "conformance-benchmark"
KEY = "CONFORMANCE_SEARCH_KEY"
TARGET = "https://reference.invalid/conformance"
OFFLINE = ("run", "report", "check", "export")


def synthetic_benchmark() -> SearchBenchmark:
    """A third population: cases that name it; metrics and grading reuse a registered one."""
    template = ordered()[0]
    return SearchBenchmark(
        name=BENCHMARK, label=template.label, order=len(BENCHMARKS),
        member=lambda case: case.get("benchmark") == BENCHMARK,
        metric_sets=template.metric_sets, level_values=template.level_values,
        answers=template.answers, submission=(BENCHMARK, "conformance-v1", {}))


def profile(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema": "web-search/1", "title": "Synthetic conformance fixture — not measurements",
        "protocol": {"top_k": 5, "rounds": 1, "score_round": 0, "concurrency": 1,
                     "server_timeout_ms": 1000, "client_timeout_s": 2, "interval_s": 0,
                     "retry": False, "review": "Synthetic fixture without independent review"},
        "methods": [{"id": METHOD, "label": "Conformance method", "adapter": ADAPTER,
                     "endpoint": "https://conformance.invalid/search", "key_env": KEY,
                     "currency": "USD", "price_per_1000": 1, "billing": "http-200"}],
        "cases": [{"id": "conformance-1", "family": "web", "language": "en",
                   "query": "Synthetic query", "anchors": [TARGET], "benchmark": BENCHMARK,
                   "origin": "Synthetic fixture", "provenance_kind": "curated"}],
    }), encoding="utf-8")
    return path


def http(url, body=None, header=None, timeout=20):
    receipt = {"http_status": 200, "elapsed_ms": 1, "transport_exit": 0, "transport_error": ""}
    if body is None:  # reference precheck
        return receipt, b"reference " * 20
    items = [{"url": TARGET, "title": "Synthetic result", "snippet": "Synthetic snippet"}]
    return receipt, json.dumps({"items": items}).encode()


def test_synthetic_search_instance_runs_through_every_stage(tmp_path, monkeypatch):
    assert set(declared_stages("web-search")) - {"publish"} == set(OFFLINE)
    monkeypatch.setattr(runner, "http_call", http)
    monkeypatch.setattr(runner, "load_runtime_env", lambda: None)
    monkeypatch.setenv(KEY, "fixture")
    method = adapters.Adapter(
        adapters.lexmount_request, response=lambda payload: {"results": payload["items"]},
        output_fields=dict(primary_preview="snippet", extra_text=[], answer=None,
                           page_body=None), name=ADAPTER)
    run, report, submission = tmp_path / "run", tmp_path / "report", tmp_path / "submission.json"
    with registered(adapters.ADAPTERS, ADAPTER, method), \
            registered(BENCHMARKS, BENCHMARK, synthetic_benchmark()):
        webstone("web-search", "run", "--profile", profile(tmp_path / "profile.json"),
                 "--output", run)
        webstone("web-search", "report", "--source", run, "--output", report)
        webstone("web-search", "check", "--output", report)
        webstone("web-search", "export", run, "--benchmark", BENCHMARK, "--output", submission)
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert [(row["method"], row["results"][0]["url"]) for row in rows] == [(METHOD, TARGET)]
    assert "Conformance method" in (report / "en/index.html").read_text(encoding="utf-8")
    exported = json.loads(submission.read_text(encoding="utf-8"))
    assert exported["benchmarks"][0]["id"] == BENCHMARK
    assert [item["id"] for item in exported["benchmarks"][0]["providers"]] == [METHOD]
