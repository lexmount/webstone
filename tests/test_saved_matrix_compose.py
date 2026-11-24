"""Compose separately produced runs into one comparison and expose offline revision."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.web_fetch.cli import EXAMPLES, main
from lexbench_webfetch.scoring import score_attempt

ROOT = Path(__file__).resolve().parents[1]


def webstone(*arguments):
    with pytest.raises(SystemExit) as exit_info:
        main([str(value) for value in arguments])
    return exit_info.value.code


def run(tmp_path, provider, dataset=EXAMPLES / "fixture-dataset.json", name=None):
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({"schema_version": 1, "providers": {
        "alpha": {"type": "fixture"}, "beta": {"type": "fixture", "fail_cases": ["fixture-failure"]}}}),
        encoding="utf-8")
    output = tmp_path / "runs" / (name or provider)
    assert webstone("run", "--provider-config", config, "--providers", provider, "--dataset", dataset,
                    "--delay", 0, "--no-report", "--output", output) == 0
    return output


def test_separate_runs_compose_into_one_comparison_report(tmp_path):
    first, second = run(tmp_path, "alpha"), run(tmp_path, "beta")
    spec = tmp_path / "comparison.json"
    assert webstone("compose", first, second, "--output", spec, "--title", "Two methods") == 0
    written = json.loads(spec.read_text(encoding="utf-8"))
    assert [p["id"] for p in written["providers"]] == ["alpha", "beta"]
    assert all(not Path(s["results"]["path"]).is_absolute() for s in written["sources"])
    report = tmp_path / "report"
    subprocess.run([sys.executable, str(ROOT / "scripts/build_report.py"), "--saved-matrix", str(spec),
                    "--output", str(report)], cwd=ROOT, check=True, capture_output=True)
    matrix = json.loads((report / "matrix.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in matrix["providers"]] == ["alpha", "beta"]
    assert {(row["provider"], row["passed"]) for row in matrix["rows"]} == {
        ("alpha", True), ("beta", True), ("beta", False)}


def test_compose_rejects_different_datasets_without_suite_grouping(tmp_path):
    changed = json.loads((EXAMPLES / "fixture-dataset.json").read_text(encoding="utf-8"))
    changed["cases"][0]["name"] = "Changed name"
    dataset = tmp_path / "changed-dataset.json"
    dataset.write_text(json.dumps(changed), encoding="utf-8")
    first, second = run(tmp_path, "alpha"), run(tmp_path, "beta", dataset=dataset)
    with pytest.raises(SystemExit, match="different frozen datasets"):
        main(["compose", str(first), str(second), "--output", str(tmp_path / "spec.json")])
    assert not (tmp_path / "spec.json").exists()


def test_compose_rejects_a_run_without_results(tmp_path):
    first, second = run(tmp_path, "alpha"), run(tmp_path, "beta")
    (second / "results.jsonl").unlink()
    with pytest.raises(SystemExit, match="missing results.jsonl"):
        main(["compose", str(first), str(second), "--output", str(tmp_path / "spec.json")])


def test_revise_command_rescores_a_saved_run_offline(tmp_path, capsys):
    raw = b"Required article text. CAPTCHA"
    (tmp_path / "answer.txt").write_bytes(raw)
    case = {"id": "a", "url": "https://example.test/", "required_facts": [
        {"id": "body", "alternatives": ["Required article text"]}], "forbidden_any": ["CAPTCHA"]}
    row = {"attempt_id": "p__a", "provider": "p", "case_id": "a", "ok": True,
           "artifact_path": "answer.txt", "metrics": score_attempt(case, raw, ok=True)}
    (tmp_path / "dataset.json").write_text(json.dumps({"cases": [case], "comparison": {}}), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    changed = {**{k: v for k, v in case.items() if k != "forbidden_any"}, "diagnostic_any": ["CAPTCHA"]}
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"cases": [changed], "rule_revision": True,
        "authority": {"description": "Declared correction"},
        "regression_ids": ["declared-forbidden-token-fails", "unforbidden-captcha-documentation-passes"]}),
        encoding="utf-8")
    assert webstone("revise", tmp_path, "--config", config) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["provider_calls_added"] == 0
    assert receipt["task_verdict_changes"] == [{"attempt_id": "p__a", "before": False, "after": True}]
