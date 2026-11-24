from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.metrics import summarize_attempts


def _audit_module():
    script = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/verify_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_metrics", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audited_summary(tmp_path, engines):
    rows = [
        {
            "engine": engine,
            "task_id": "a",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 100 / (position + 1),
            "resource": {"memory_peak_bytes": 200 / (position + 1)},
        }
        for position, engine in enumerate(engines)
    ]
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    summary = summarize_attempts(rows, engines=engines)
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


def _rejects(tmp_path, summary, message):
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _audit_module().verify(tmp_path)


@pytest.mark.parametrize(
    "engines",
    [("chromium",), ("chromium", "moli"), ("moli", "chromium"), ("chromium", "moli", "obscura")],
    ids=["one", "two", "two-other-baseline", "three"],
)
def test_independent_audit_accepts_the_labels_a_real_summary_publishes(tmp_path, engines):
    _audited_summary(tmp_path, engines)

    assert _audit_module().verify(tmp_path)["status"] == "pass"


def _swap_paired(numerator, denominator):
    def change(summary):
        paired = summary["resource_comparison"]["paired_ratios"]
        paired["numerator_engine"], paired["denominator_engine"] = numerator, denominator
    return change


def _tamper(section):
    def change(summary):
        comparison = summary["resource_comparison"]
        if section == "aggregate":
            comparison["by_engine"]["moli"]["duration_ms"]["p95"] = 99
        elif section == "paired":
            comparison["paired_ratios"]["metrics"]["duration_ms"]["values"][0]["numerator"] = 49
        elif section == "metric-set":
            comparison["by_engine"]["moli"].clear()
        elif section == "relative":
            comparison["relative_ratios"]["by_engine"]["obscura"]["duration_ms"]["values"][0][
                "numerator"
            ] = 24
        else:
            comparison["relative_ratios"]["baseline_engine"] = "moli"
    return change


@pytest.mark.parametrize(
    ("engines", "change", "message"),
    [
        (("chromium", "moli"), _tamper("aggregate"), "moli.duration_ms.p95"),
        (("chromium", "moli"), _tamper("paired"), "numerator"),
        (("chromium", "moli"), _tamper("metric-set"), "required metric set"),
        (("chromium", "moli", "obscura"), _tamper("relative"), "numerator"),
        (("chromium", "moli", "obscura"), _tamper("baseline"), "relative ratio baseline"),
        (("chromium", "moli"), _swap_paired("chromium", "moli"), "paired ratio labels"),
        (("chromium", "moli", "obscura"), _swap_paired("moli", "chromium"),
         "paired ratio labels"),
    ],
    ids=["aggregate", "paired-operand", "metric-set", "relative-operand", "relative-baseline",
         "swapped-labels", "paired-label-on-larger-set"],
)
def test_independent_audit_rejects_tampered_summary(tmp_path, engines, change, message):
    summary = _audited_summary(tmp_path, engines)
    change(summary)
    _rejects(tmp_path, summary, message)


def test_independent_audit_rejects_a_smaller_provider_set_than_attempts(tmp_path):
    engines = ("chromium", "moli", "obscura")
    _audited_summary(tmp_path, engines)
    rows = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    _rejects(tmp_path, summarize_attempts(rows[:2], engines=engines[:2]), "required provider set")
