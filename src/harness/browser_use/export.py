"""Export a scored Browser Use run as a WebArena-Verified result submission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.browser_use.publication import read_public_json, trajectory
from harness.browser_use.report import _logical_results
from harness.record_io import load_jsonl, read_json
from harness.record_schema import validate_records
from harness.results.export import (
    distribution,
    percent,
    provider,
    rounded,
    scored_result_schema,
    submission,
)

BENCHMARK, DEFINITION = "webarena-verified", "standard"
# One logical result per model, browser and task as the report selects it; ``passed`` is
# the official evaluator's full score.
SCORED_RESULT = scored_result_schema("browseruse.scored_result", 1, model=(str,),
                                     action_seconds=(list,))


def _scored(row: dict[str, Any]) -> dict[str, Any]:
    actions = [float(step["duration_seconds"])
               for execution in row.get("_source_executions") or [row]
               for step in trajectory(read_public_json(
                   Path((execution.get("paths") or {}).get("events", ""))) or [])
               if isinstance(step.get("duration_seconds"), (int, float))]
    return {"schema": SCORED_RESULT.id, "method": str(row["engine"]), "model": row["model"],
            "case": str(row["task_id"]), "elapsed_ms": float(row.get("duration_ms") or 0),
            "evidence": str((row.get("paths") or {}).get("events") or ""),
            "passed": float(row["score"]) == 1, "action_seconds": actions}


def export(run_dir: Path | str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    run = Path(run_dir)
    protocol = read_json(run / "manifest.json").get("protocol") or {}
    rows = load_jsonl(run / "attempts.jsonl", skip_blank=True)
    if not rows:
        raise ValueError("The run has no scored attempts")
    for row in rows:
        row["model"] = str(row.get("model") or protocol.get("model") or "")
        if not row["model"]:
            raise ValueError("An attempt does not identify its model")
    records = validate_records([_scored(row) for row in _logical_results(rows, protocol)],
                               SCORED_RESULT, source="attempts.jsonl")
    tasks = {record["case"] for record in records}
    configurations = sorted({(record["model"], record["method"]) for record in records})
    several_models = len({model for model, _ in configurations}) > 1
    providers = []
    for model, engine in configurations:
        own = [r for r in records if (r["model"], r["method"]) == (model, engine)]
        task = distribution(r["elapsed_ms"] / 1000 for r in own)
        action = distribution(value for r in own for value in r["action_seconds"])
        passed = sum(r["passed"] for r in own)
        providers.append(provider(
            f"{engine}-{model}" if several_models else engine,
            {"successRate": percent(passed, len(own)),
             "taskMean": rounded(task["mean"], 2), "taskP50": rounded(task["p50"], 2),
             "taskP95": rounded(task["p95"], 2), "actionMean": rounded(action["mean"], 2),
             "actionP50": rounded(action["p50"], 2), "actionP95": rounded(action["p95"], 2)},
            mode="Browser Use agent", description=f"{engine} browser driven by {model}",
            passed=passed,
            samples={"task": task["n"], "taskMissing": len(tasks) - len(own),
                     "action": action["n"]},
        ))
    return submission(
        benchmark=BENCHMARK, definition=DEFINITION, task_count=len(tasks), providers=providers,
        receipt=receipt, track="Browser Use",
        notes=["Task time sums every execution of a task, including a retry; the final "
               "execution decides success.",
               "Action time is the duration of each BrowserTool call."],
        scope=(f"Evaluates {len(tasks)} WebArena-Verified tasks selected by the run, scored by "
               "the official evaluator."),
    )
