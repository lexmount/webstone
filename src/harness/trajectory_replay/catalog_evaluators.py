"""Evaluator kinds of the WebArena-Verified catalog replay.

``retrieval_acceptance`` derives a RETRIEVE task's answer from current-run
observations under its frozen contract. ``catalog_acceptance`` scores an answer
and the network trace with WebArena-Verified's official evaluator, through a
registered replay correction when one applies, together with that correction's
live-page assertions. Both are registered in the track's evaluator registry; the
run supplies the configured benchmark as the ``benchmark`` evaluator option.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from harness.trajectory_replay.core.evaluators import (
    EVALUATOR_KINDS,
    TaskEvaluation,
    TaskEvidence,
)
from harness.trajectory_replay.core.suite import BrowserTask
from harness.trajectory_replay.retrieval_acceptance import build_actual_response
from harness.trajectory_replay.webarena_corrections import evaluate_corrected_contract


def catalog_task(
    task_id: int, kind: str, *, tool_calls: Sequence[dict[str, Any]] = (), **config: Any
) -> BrowserTask:
    """The replay task view an evaluator receives for one catalog task."""
    return BrowserTask(
        task_id=str(task_id),
        title="",
        tool_calls=tuple(tool_calls),
        assertions=(),
        tags=(),
        difficulty="",
        ground_truth={},
        source={},
        evaluator={"kind": kind, "task_id": task_id, **config},
    )


class RetrievalAcceptanceEvaluator:
    """Accept a RETRIEVE answer only from observations the frozen contract names."""

    def __init__(self, **_: object) -> None:
        pass

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        config = task.evaluator or {}
        acceptance = build_actual_response(
            contract=config.get("contract"),
            task_id=int(config["task_id"]),
            calls=list(task.tool_calls),
            records=list(evidence.trace),
            run_id=evidence.run_id,
            attempt_id=evidence.attempt_id,
        )
        supported = acceptance["actual_response"] is not None
        return TaskEvaluation(
            status="pass" if supported else "unsupported",
            score=1.0 if supported else 0.0,
            checks=tuple(acceptance["sources"]),
            evaluator="retrieval_acceptance",
            error="" if supported else str(acceptance.get("reason") or ""),
            record=acceptance,
        )


class CatalogAcceptanceEvaluator:
    """Official WebArena-Verified scoring plus a correction's live-page assertions."""

    def __init__(self, *, benchmark: Any = None, **_: object) -> None:
        self._benchmark = benchmark

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        if self._benchmark is None:
            raise ValueError("catalog acceptance requires the run's benchmark option")
        config = task.evaluator or {}
        correction = config.get("correction")
        result = (
            evaluate_corrected_contract(
                self._benchmark,
                correction,
                agent_response=evidence.agent_response,
                network_trace=evidence.network_har_path,
            )
            if correction is not None
            else self._benchmark.evaluate_task(
                task_id=int(config["task_id"]),
                agent_response=evidence.agent_response,
                network_trace=evidence.network_har_path,
            )
        )
        record = result.model_dump(mode="json")
        record["replay_assertions"] = list(evidence.page_checks)
        if correction is not None:
            record["contract_correction_id"] = correction.correction_id
            record["corrected_task_sha256"] = correction.corrected_task_sha256
        score = float(record.get("score") or 0)
        passed = score == 1 and all(item.get("status") == "pass" for item in evidence.page_checks)
        return TaskEvaluation(
            status="pass" if passed else "fail",
            score=score,
            checks=evidence.page_checks,
            evaluator="catalog_acceptance",
            record=record,
        )


EVALUATOR_KINDS.register("retrieval_acceptance", RetrievalAcceptanceEvaluator)
EVALUATOR_KINDS.register("catalog_acceptance", CatalogAcceptanceEvaluator)
