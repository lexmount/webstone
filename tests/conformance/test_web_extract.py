"""Web Extract conformance: a synthetic method and record benchmark through every stage.

The synthetic method owns its transport and answers from the frozen page in this
process, so the certified ``run`` collects and scores without network or credentials.
``score``, ``report``, ``check`` and ``export`` then run through ``webstone web-extract``;
``package`` reaches its release gate, which refuses synthetic evidence. Publishing needs a
Yours site and is not driven.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.adapters import BenchmarkAdapter, Capabilities
from harness.web_extract.benchmark_adapter import adapter
from harness.web_extract.dataset import BENCHMARKS, EXPORTED_EVALUATOR, ExtractBenchmark
from harness.web_extract.export import SUBMISSIONS
from harness.web_extract.pipeline import METHODS, ExtractMethod, digest, read_json, save_json
from tests.conformance import extract_task
from tests.conformance.helpers import declared_stages, registered, webstone
# Registrations live in this process, so collection runs on threads, not spawned workers.
from tests.web_extract.conftest import stubbed_serial_collection  # noqa: F401

pytestmark = pytest.mark.usefixtures("stubbed_serial_collection")

METHOD = "conformance-method"
BENCHMARK = extract_task.NAME
DRIVEN = ("run", "score", "report", "check", "package", "export")
PAGES = {"one": "Ada", "two": "Grace"}


def synthetic_request(method, html, source_url, fixture_url, **options):
    """A finished call record: the method reads the frozen page's heading itself."""
    names = re.findall(r"<h1>(.*?)</h1>", html)
    return {"state": "response_received", "http_status": 200, "elapsed_seconds": 0.01,
            "response": {"json": {"name": names}},
            "request": {"input": "html", "html_sha256": digest(html.encode()),
                        "schema": options.get("schema"), "source_url": source_url}}


def synthetic_method() -> ExtractMethod:
    return ExtractMethod(name=METHOD, keys=(), input="html", request=synthetic_request,
                         project=lambda payload, preserve: payload.get("json"),
                         custom_schema=True)


def definition(root: Path) -> Path:
    cases = []
    for case_id, name in PAGES.items():
        raw = f"<html><body><h1>{name}</h1></body></html>".encode()
        page = root / f"{case_id}.html"
        page.write_bytes(raw)
        values = {"name": [name]}
        cases.append({
            "id": case_id, "url": f"https://source.example/{case_id}", "domain": "profile",
            "site": "source", "input": str(page), "input_sha256": digest(raw), "values": values,
            "schema": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "array", "items": {"type": "string"}}}},
            "qualification": {"input_sha256": digest(raw), "reference_sha256": digest(
                json.dumps(values, sort_keys=True, ensure_ascii=False).encode())},
        })
    path = root / "cohort.json"
    save_json(path, {"schema": "source-qualified-extraction-cohort/1", "id": BENCHMARK,
                     "name": "Synthetic conformance records", "repository": "example/conformance",
                     "input_mode": "snapshot", "cases": cases,
                     "source_versions": {"annotations": "1" * 40},
                     "selection": {"seed": "frozen-before-calls"},
                     "source_license": "Synthetic fixture only"})
    return path


def report_inputs(root: Path, home: Path, plan: Path, run: Path) -> tuple[Path, Path]:
    """The record-cohort report instance: its participants, evidence runs and prices."""
    content, profile = root / "content.json", root / "profile.json"
    save_json(content, {
        "mode": "field-extraction-cohort", "title": "Synthetic conformance cohort",
        "dataset_manifest": str(home / "dataset/manifest.json"),
        "participants": {METHOD: {"label": "Conformance method"}},
        "evidence_runs": [{"run": str(run), "plan": str(plan),
                           "plan_sha256": digest(plan.read_bytes())}],
        "snapshot_base": "https://example.org/frozen",
        "prices": {METHOD: {"usd_per_1000": "$0 synthetic", "terms": {"zh": "合成数据"},
                            "verified_at": "2026-01-01",
                            "sources": ["https://example.org/pricing"]}},
    })
    dataset = read_json(home / "profile.json")["dataset"]
    save_json(profile, {"local_example": True,
                        "dataset": {"commit": dataset["commit"], "cases": dataset["cases"]},
                        "research_report": {"content": {"sha256": digest(content.read_bytes())}}})
    return content, profile


def test_synthetic_extract_instance_runs_through_every_stage(tmp_path, monkeypatch):
    assert set(declared_stages("web-extract")) - {"publish"} == set(DRIVEN)
    home, plan = tmp_path / "home", tmp_path / "plan.json"
    run = home / "runs" / METHOD  # the exporter finds the home two levels up
    benchmark = ExtractBenchmark(name=BENCHMARK, module=extract_task.__name__,
                                 capabilities=Capabilities(frozenset({EXPORTED_EVALUATOR})))
    with registered(METHODS, METHOD, synthetic_method()), \
            registered(BENCHMARKS, BENCHMARK, benchmark), \
            registered(SUBMISSIONS, BENCHMARK, (BENCHMARK, {"pageMacroF1": ("page_hit_macro", "f1")})):
        webstone("web-extract", "--home", home, "prepare", "--benchmark", BENCHMARK,
                 "--source", definition(tmp_path))
        hooks = adapter(home)
        assert isinstance(hooks, BenchmarkAdapter) and hooks.name == BENCHMARK
        # Collection requires a bound execution preflight, measured on its own plan.
        webstone("web-extract", "--home", home, "plan", "--methods", METHOD,
                 "--output", tmp_path / "preflight-plan.json")
        webstone("web-extract", "--home", home, "preflight", "--profile",
                 tmp_path / "preflight-plan.json", "--method", METHOD, "--run-dir",
                 tmp_path / "preflight", "--assessment-output", tmp_path / "assessment.json",
                 "--sample-size", "1")
        webstone("web-extract", "--home", home, "plan", "--methods", METHOD,
                 "--execution-assessment", tmp_path / "assessment.json", "--output", plan)
        webstone("web-extract", "run", "--home", home, "--profile", plan, "--method", METHOD,
                 "--run-dir", run)
        webstone("web-extract", "--home", home, "score", run, "--output", tmp_path / "scores.json")
        content, profile = report_inputs(tmp_path, home, plan, run)
        report = tmp_path / "report"
        webstone("web-extract", "--home", home, "report", "--content", content,
                 "--profile", profile, "--output", report, "--language", "zh")
        webstone("web-extract", "--home", home, "check", report, "--profile", profile)
        # A local example profile marks synthetic evidence, which the release gate refuses.
        save_json(tmp_path / "visual.json", {})
        with pytest.raises(ValueError, match="Synthetic record evidence cannot be a formal release"):
            webstone("web-extract", "--home", home, "package", report, "--profile", profile,
                     "--output", tmp_path / "report.zip",
                     "--visual-receipt", tmp_path / "visual.json")
        webstone("web-extract", "export", run, "--output", tmp_path / "submission.json")
    assert "Conformance method" in (report / "zh/index.html").read_text(encoding="utf-8")
    assert hooks.populations() == [{"id": BENCHMARK, "cases": 2, "fields": ["name"]}]
    exported = json.loads((tmp_path / "submission.json").read_text(encoding="utf-8"))
    assert exported["benchmarks"][0]["id"] == BENCHMARK
    (item,) = exported["benchmarks"][0]["providers"]
    assert item["id"] == METHOD and item["scores"]["pageMacroF1"] == 1
