"""Export scored Browser Trajectory Replay results as a WebArena-Verified result submission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.record_io import read_json
from harness.record_schema import validate_records
from harness.results.export import percent, provider, rounded, scored_result_schema, submission
from harness.trajectory_replay.report_inputs import validated_results
from harness.trajectory_replay.webarena_report import _load_artifact, _stat

BENCHMARK, DEFINITION = "webarena-verified", "standard"
# One effective replay result per method and task; ``passed`` requires both the replay
# and the official evaluator to pass. Action times come from the physical executions.
SCORED_RESULT = scored_result_schema("trajectoryreplay.scored_result", 1, nullable=("elapsed",),
                                     status=(str,))


def _method(row: dict[str, Any]) -> str:
    return str(row.get("participant_id") or row["engine"])


def _scored(row: dict[str, Any]) -> dict[str, Any]:
    elapsed = row.get("duration_ms")
    return {"schema": SCORED_RESULT.id, "method": _method(row), "case": str(row["task_id"]),
            "elapsed_ms": float(elapsed) if isinstance(elapsed, (int, float)) else None,
            "evidence": str(((row.get("evidence") or {}).get("trace") or {}).get("sha256") or ""),
            "passed": row["status"] == "pass", "status": row["status"]}


def export(run_dir: Path | str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    results = validated_results(read_json(Path(run_dir) / "results.json"))
    records = validate_records([_scored(row) for row in results["attempts"]], SCORED_RESULT,
                               source="results.json")
    if not records:
        raise ValueError("The results hold no replay attempts")
    actions: dict[str, list[float]] = {}
    for execution in results.get("physical_executions") or []:
        trace = _load_artifact((execution.get("evidence") or {}).get("trace")) or []
        actions.setdefault(_method(execution), []).extend(
            float(step["duration_ms"]) for step in trace
            if isinstance(step.get("duration_ms"), (int, float)))
    tasks = {record["case"] for record in records}
    providers = []
    for method in dict.fromkeys(record["method"] for record in records):
        own = [record for record in records if record["method"] == method]
        task = _stat([r["elapsed_ms"] for r in own if r["elapsed_ms"] is not None])
        action = _stat(actions.get(method, []))
        passed = sum(r["passed"] for r in own)
        providers.append(provider(
            method,
            {"successRate": percent(passed, len(own)),
             **{f"task{key.title()}": rounded(None if task[key] is None else task[key] / 1000, 2)
                for key in ("mean", "p50", "p95")},
             **{f"action{key.title()}": rounded(None if action[key] is None
                                                else action[key] / 1000, 2)
                for key in ("mean", "p50", "p95")}},
            mode="Fixed BrowserTool trajectory replay",
            description=f"{method} replaying the qualified trajectories", passed=passed,
            samples={"task": task["n"], "taskMissing": len(own) - task["n"],
                     "action": action["n"]},
        ))
    return submission(
        benchmark=BENCHMARK, definition=DEFINITION, task_count=len(tasks), providers=providers,
        receipt=receipt, track="Browser Trajectory Replay",
        notes=["Each method replays the same qualified BrowserTool actions; success requires the "
               "replay and the official evaluator to pass.",
               "Action time is the duration of each replayed BrowserTool call."],
        scope=(f"Replays {len(tasks)} WebArena-Verified tasks with fixed qualified trajectories, "
               "not autonomous agent runs."),
    )
