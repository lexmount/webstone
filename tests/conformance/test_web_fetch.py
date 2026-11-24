"""Web Fetch conformance: a synthetic provider type and benchmark through every stage.

The synthetic provider answers in this process, so ``run`` collects and scores without
network or credentials; ``score``, ``report``, ``check`` and ``export`` then run on the
saved run through ``webstone webfetch``. A direct run is diagnostic evidence, so
``package`` must refuse it; publishing needs a Yours site and is not driven.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.web_fetch.benchmarks import BENCHMARKS, LiveSuite
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.providers.base import PROVIDERS, Provider
from tests.baselines.test_web_fetch import pin_environment
from tests.conformance.helpers import declared_stages, registered, webstone

PROVIDER_TYPE = "conformance_fetch"
METHOD = "conformance-method"
BENCHMARK = "conformance-benchmark"
DRIVEN = ("run", "score", "report", "check", "package", "export")
MARKER = "Synthetic conformance content"


class ConformanceProvider(Provider):
    """Returns the case's expected marker for one case and fails the other."""

    @staticmethod
    def native_answer_selection(selection):
        return {"schema_version": 2, "content_pointer": ["content"], "content_format": "text",
                "result_fields": {"title": {"pointer": ["title"]}}}

    def native_route(self, case) -> NativeRoute:
        return NativeRoute("conformance", "synthetic_conformance", ("content", "title"))

    def fetch(self, case, timeout, *, native_route_ledger=None) -> ProviderResult:
        if case.id.endswith("failure"):
            return ProviderResult(ok=False, content="", status_code=502, final_url=case.url,
                                  error_type="synthetic_failure", error_message="Synthetic failure")
        return ProviderResult(ok=True, content=f"# {case.name}\n\n{MARKER}", status_code=200,
                              final_url=case.url, title=case.name)


def synthetic_benchmark(root: Path) -> LiveSuite:
    case = {"name": "Synthetic case", "category": "synthetic", "track": "web",
            "task_kind": "body", "task_prompt": "Return the synthetic content.",
            "expected_any": [MARKER], "ground_truth_main_content": MARKER}
    dataset = root / "dataset.json"
    dataset.write_text(json.dumps({
        "schema_version": 1, "id": BENCHMARK, "name": "Synthetic conformance cases",
        "tool": "web_fetch",
        "cases": [dict(case, id="conformance-body", url="https://example.com/conformance"),
                  dict(case, id="conformance-failure", url="https://example.com/failure")],
        "coverage_plan": {"web": [METHOD]},
        "comparison": {"publication_state": "synthetic_example_not_for_release"},
    }), encoding="utf-8")
    providers = root / "providers.json"
    providers.write_text(json.dumps({"schema_version": 1, "providers": {
        METHOD: {"type": PROVIDER_TYPE}}}), encoding="utf-8")
    suite = type("ConformanceSuite", (LiveSuite,), {
        "name": BENCHMARK, "default_dataset": dataset, "default_provider_config": providers})
    return suite()


@pytest.fixture
def synthetic_instance(tmp_path, monkeypatch):
    pin_environment(monkeypatch)
    entry = (__name__, ConformanceProvider.__name__)
    with registered(PROVIDERS, PROVIDER_TYPE, entry), \
            registered(BENCHMARKS, BENCHMARK, synthetic_benchmark(tmp_path)):
        run = tmp_path / "run"
        webstone("webfetch", "run", "--benchmark", BENCHMARK, "--providers", METHOD,
                 "--delay", "0", "--output", run)
        yield run


def test_synthetic_fetch_instance_runs_through_every_stage(synthetic_instance, tmp_path):
    assert set(declared_stages("webfetch")) - {"publish"} == set(DRIVEN)
    run = synthetic_instance
    webstone("webfetch", "score", run, "--benchmark", BENCHMARK,
             "--dataset", tmp_path / "dataset.json")
    webstone("webfetch", "report", run)
    with pytest.raises(ValueError, match="Diagnostic evidence"):
        webstone("webfetch", "package", run / "report", tmp_path / "package.zip")
    webstone("webfetch", "export", run, "--output", tmp_path / "submission.json")
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert {(row["provider"], row["case_id"], row["ok"]) for row in rows} == {
        (METHOD, "conformance-body", True), (METHOD, "conformance-failure", False)}
    assert METHOD in (run / "report/en/index.html").read_text(encoding="utf-8")
    exported = json.loads((tmp_path / "submission.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in exported["benchmarks"][0]["providers"]] == [METHOD]


@pytest.mark.xfail(strict=True, raises=FileNotFoundError, reason=(
    "The check recognizes a synthetic report only by provider_type == 'fixture' "
    "(web_fetch/report_check.py check_revision, web_fetch/report_methods.py); any other "
    "registered provider type is checked against the published adoption inventory."))
def test_check_accepts_a_synthetic_provider_type(synthetic_instance, tmp_path):
    webstone("webfetch", "check", synthetic_instance / "report", "--authority-root", tmp_path)
