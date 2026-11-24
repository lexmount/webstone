"""Browser Use conformance: a synthetic browser and model through every offline stage.

Runs need a model, browsers and WebArena sites, so the test starts from synthetic
attempts and drives the remaining stages through ``webstone browser-use``: report,
check, package (with its release gate) and export. Publishing needs a Yours site,
so the test stops at the package the publish stage uploads.
"""

from __future__ import annotations

import importlib
import json
import zipfile
from pathlib import Path

import pytest

from harness.browser_use import report_package
from harness.environments.browser import BROWSERS, BrowserAdapter
from harness.record_io import file_sha256
from tests.conformance.helpers import declared_stages, registered, webstone

BROWSER = "conformance-browser"
# A comparison report needs a second participant; the reference browser takes that role.
BASELINE = "chromium"
MODEL = "conformance-model"
TASK = 44
COMMIT = "0" * 40
EFFORT = "low"
EVALUATOR = "1" * 64


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _official_data_checksum() -> str:
    from harness.environments.webarena.runtime import configure_benchmark

    return file_sha256(Path(configure_benchmark().config.test_data_file))


def _selection(root: Path) -> Path:
    """A one-task population: the selection is data, not a code change."""
    return _write(root / "selection.json", {
        "schema": "lex_webarena_verified_pilot/1",
        "metadata": {"benchmark": "Conformance population",
                     "tier_counts": {"easy": 1, "medium": 0, "hard": 0}},
        "tasks": [{"task_id": TASK, "tier": "easy", "estimated_min_steps": 2,
                   "official_hard": True, "rationale": "Synthetic conformance task"}],
    })


def _evidence(root: Path, selection_sha256: str, data_checksum: str) -> Path:
    """Complete synthetic attempts: the baseline passes, the synthetic browser fails twice."""
    evidence = root / "evidence"
    official = {"evaluator_checksum": EVALUATOR, "data_checksum": data_checksum}
    _write(evidence / "manifest.json", {
        "selection_sha256": selection_sha256,
        "protocol": {"model": MODEL, "model_transport_retries": 1, "reasoning_effort": EFFORT,
                     "webstone_commit": COMMIT},
        "environment": {"browser_versions": {BASELINE: {"Browser": "synthetic"}, BROWSER: {"Browser": "synthetic"}},
                        "official_evaluator": official},
    })
    _write(evidence / "run-config.json", {"selection_sha256": selection_sha256,
                                          "reasoning_effort": EFFORT, "webstone_commit": COMMIT})
    rows = []
    for engine, attempt, score in ((BASELINE, 1, 1), (BROWSER, 1, 0), (BROWSER, 2, 0)):
        execution = evidence / f"{engine}-{attempt}"
        status = "success" if score else "failure"
        response = {"execution": attempt}
        paths = {
            "response": str(_write(execution / "response.json", response)),
            "evaluation": str(_write(execution / "evaluation.json", {
                "score": score, "status": status, "evaluator": "AgentResponseEvaluator",
                "error": None if score else "Synthetic expected answer missing",
                "webarena_verified_version": "synthetic",
                "webarena_verified_evaluator_checksum": EVALUATOR,
                "webarena_verified_data_checksum": data_checksum,
                "evaluators_results": [{
                    "evaluator_name": "AgentResponseEvaluator", "score": score,
                    "status": status, "expected": {"answer": "synthetic"},
                    "actual": {"answer": None},
                    "error_msg": None if score else "Synthetic expected answer missing"}]})),
            "events": str(_write(execution / "events.json", [
                {"type": "tool_execution_start",
                 "data": {"tool_call_id": str(attempt), "tool_name": "browser",
                          "arguments": {"action": "click"}}},
                {"type": "tool_execution_end",
                 "data": {"tool_call_id": str(attempt), "status": "success",
                          "execution_time": attempt / 10}}])),
        }
        for label, name in (("network_har", "network.har"), ("agent_trace", "trace.zip")):
            (execution / name).write_bytes(b"synthetic")
            paths[label] = str(execution / name)
        rows.append({"engine": engine, "task_id": TASK, "tier": "easy", "attempt": attempt,
                     "score": score, "status": status, "agent_response": response,
                     "duration_ms": 1000 * attempt,
                     "model_usage": {"total_tokens": 100 * attempt, "request_count": 1,
                                     "request_attempt_count": 1},
                     "paths": paths})
    (evidence / "attempts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return evidence


def test_synthetic_browser_and_model_pass_every_offline_stage(tmp_path: Path,
                                                               monkeypatch) -> None:
    # The visual gate renders every route in a real browser; it is not an offline stage.
    monkeypatch.setattr(report_package, "receipt_is_current", lambda *_: True)
    authority = tmp_path / "authority"
    _write(authority / "research/browser-use-remediation.json",
           {"schema": "lex_browser_use_remediation/1", "items": []})
    selection = _selection(authority)
    data_checksum = _official_data_checksum()
    evidence = _evidence(tmp_path, file_sha256(selection), data_checksum)
    review = _write(authority / "failure-causes.json", {
        "schema": "lex_browser_use_layered_failure_cause_review/2", "cohorts": {}})
    profile = _write(authority / "profile.json", {
        "schema": "lex_browser_use_current_report/2",
        "engines": [BASELINE, BROWSER], "baseline_engine": BASELINE,
        "models": [MODEL], "model_labels": {MODEL: "Conformance Model"},
        "task_selection": {"path": str(selection), "sha256": file_sha256(selection)},
        "selection_sha256": file_sha256(selection),
        "failure_cause_review": {"source_path": str(review)},
        "logical_result": "score authority follows the frozen run manifest",
        "reasoning_effort": EFFORT, "official_data_checksum": data_checksum,
    })
    report, archive = tmp_path / "report", tmp_path / "site.zip"
    export = tmp_path / "submission.json"
    driven = []
    with registered(BROWSERS, BROWSER, BrowserAdapter(BROWSER, "Conformance Browser")):
        webstone("browser-use", "review", "--evidence", evidence, "--selection", selection)
        webstone("browser-use", "report", "--evidence", evidence, "--output", report,
                 "--profile", profile)
        driven.append("report")
        webstone("browser-use", "check", report, "--evidence", evidence, "--profile", profile,
                 "--authority-root", authority, "--require-complete")
        driven.append("check")
        webstone("browser-use", "package", "--report", report, "--evidence", evidence,
                 "--output", archive, "--profile", profile, "--authority-root", authority,
                 "--attestation", tmp_path / "attestation.json",
                 "--visual-receipt", tmp_path / "visual.json")
        driven.append("package")
        webstone("browser-use", "export", evidence, "--output", export)
        driven.append("export")
    assert "Conformance Browser" in (report / "index.html").read_text(encoding="utf-8")
    with zipfile.ZipFile(archive) as packaged:
        assert f"results/{MODEL}-{BROWSER}-{TASK}.html" in packaged.namelist()
    assert BROWSER in export.read_text(encoding="utf-8")
    # A newly declared stage must be driven here. Run needs a model and live sites, and
    # publish uploads the package to a Yours site; neither has an offline mode.
    assert set(declared_stages("browser-use")) - {"run", "publish"} == set(driven)


@pytest.mark.xfail(strict=True, raises=ModuleNotFoundError, reason=(
    "Browser Use has no benchmark extension point: selection.py resolves every task "
    "through the WebArena-Verified package (validate_against_official), report.py "
    "renders WebArena-Verified contracts and export.py submits to webarena-verified. "
    "A second benchmark needs a benchmark registry first; the test above varies only "
    "the task population, which is data."))
def test_synthetic_benchmark_registers_through_an_adapter() -> None:
    importlib.import_module("harness.browser_use.benchmarks")
