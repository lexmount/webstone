"""Browser Trajectory Replay conformance: a synthetic browser and benchmark through every stage.

A real replay needs BrowserTool and deployed WebArena sites, so the synthetic benchmark's
registered run writes synthetic replay results. Every other stage then runs on them
through ``webstone trajectory-replay``: report, check, package (with its release gate)
and export. Publishing needs a Yours site, so the test stops at the package.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from harness.environments.browser import BROWSERS, BrowserAdapter
from harness.record_io import canonical_json_sha256, file_sha256
from harness.trajectory_replay import webarena_report_package
from harness.trajectory_replay.cli import BENCHMARK_RUNS
from harness.trajectory_replay.webarena_review import REVIEW_SCHEMA
from tests.conformance.helpers import declared_stages, registered, webstone

BROWSER = "conformance-browser"
BENCHMARK = "conformance-benchmark"
# A comparison report needs a second participant; the reference browser takes that role.
BASELINE = "chromium"
TASK = 7
ACTIONS = [{"action": "snapshot"}]


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def synthetic_run(argv: list[str]) -> None:
    """The synthetic benchmark's run: frozen task inputs and one replay per method."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--methods", required=True)
    args = parser.parse_args(argv)
    task = {"task": {"intent": "Open the synthetic page", "sites": ["reddit"]},
            "evaluation_input": {"task_type": "NAVIGATE"}}
    dataset = _write(args.output / "dataset.json", {"trajectories": [{
        "task_id": TASK, **task, "tool_calls": ACTIONS,
        "source": {"kind": "authoritative_successful_browser_use_execution"}}]})
    attempts = []
    for method in args.methods.split(","):
        trace = _write(args.output / f"{method}-trace.json", [
            {"action": "snapshot", "status": "pass", "result": {"text": "Synthetic page"}}])
        attempts.append({
            "participant_id": method, "engine": method, "task_id": TASK, "status": "pass",
            "process_status": "pass", "evaluator_status": "pass", "action_count": 1,
            "completed_action_count": 1, "duration_ms": 100,
            "planned_actions": ACTIONS, "planned_actions_sha256": canonical_json_sha256(ACTIONS),
            "task_contract_sha256": canonical_json_sha256(task),
            "reference_validity": {"status": "valid"}, "resource": {},
            "evidence": {"trace": {"path": str(trace), "sha256": file_sha256(trace)}}})
    results = _write(args.output / "results.json", {
        "attempts": attempts, "physical_executions": attempts,
        "dataset_sha256": file_sha256(dataset)})
    _write(args.output / "review.json", {
        "schema": REVIEW_SCHEMA, "bindings": {"effective_results_sha256": file_sha256(results)},
        "cause_groups": [], "coverage": {"failed_results": 0, "verified_results": 0}})


def test_synthetic_browser_and_benchmark_pass_every_stage(tmp_path: Path, monkeypatch) -> None:
    # The visual gate renders every route in a real browser; it is not an offline stage.
    monkeypatch.setattr(webarena_report_package, "receipt_is_current", lambda *_: True)
    run, report, archive = tmp_path / "run", tmp_path / "report", tmp_path / "site.zip"
    export = tmp_path / "submission.json"
    driven = []
    with (registered(BROWSERS, BROWSER, BrowserAdapter(BROWSER, "Conformance Browser")),
          registered(BENCHMARK_RUNS, BENCHMARK, synthetic_run)):
        webstone("trajectory-replay", "run", "--benchmark", BENCHMARK, "--output", run,
                 "--methods", f"{BASELINE},{BROWSER}")
        driven.append("run")
        dataset = run / "dataset.json"
        profile = _write(tmp_path / "profile.json", {
            "methods": [{"id": BASELINE, "engine": BASELINE, "label": "Chromium"},
                        {"id": BROWSER, "engine": BROWSER, "label": "Conformance Browser"}],
            "qualification": {"reference_engine": BASELINE},
            "dataset": {"path": str(dataset), "sha256": file_sha256(dataset)},
            "execution": {"method_parallelism": 1, "task_parallelism": 1,
                          "reset_before_every_task": False, "reset_before_every_method": True,
                          "teardown_between_methods": True, "retry_failed_task": False,
                          "site_stack": "synthetic"},
        })
        webstone("trajectory-replay", "report", "--results", run / "results.json",
                 "--profile", profile, "--dataset", dataset, "--review", run / "review.json",
                 "--output", report)
        driven.append("report")
        webstone("trajectory-replay", "check", report, "--profile", profile)
        driven.append("check")
        webstone("trajectory-replay", "package", "--report", report,
                 "--evidence", run / "results.json", "--output", archive, "--profile", profile,
                 "--review", run / "review.json", "--attestation", tmp_path / "attestation.json",
                 "--visual-receipt", tmp_path / "visual.json")
        driven.append("package")
        webstone("trajectory-replay", "export", run, "--output", export)
        driven.append("export")
    assert "Conformance Browser" in (report / "index.html").read_text(encoding="utf-8")
    with zipfile.ZipFile(archive) as packaged:
        assert any(BROWSER in name for name in packaged.namelist())
    assert BROWSER in export.read_text(encoding="utf-8")
    # A newly declared stage must be driven here; publish uploads to a Yours site and has
    # no offline mode.
    assert set(declared_stages("trajectory-replay")) - {"publish"} == set(driven)
