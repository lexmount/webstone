"""Task-result evaluators shared by every Trajectory Replay benchmark.

A task names its evaluator kind in ``task.evaluator["kind"]``; benchmark adapters
register the kinds they need in ``EVALUATOR_KINDS``. The track's runner scores
every attempt through ``EvaluatorRegistry`` without naming a kind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness.adapters import Registry
from harness.trajectory_replay.core.executor import judge_outputs
from harness.trajectory_replay.core.suite import BrowserTask


@dataclass(frozen=True)
class TaskEvidence:
    outputs: dict[str, str]
    process_passed: bool
    agent_response: dict[str, Any] | list[Any] | str | None = None
    network_har_path: Path | None = None
    # Executed action records, for evaluators that read current-run observations.
    trace: tuple[dict[str, Any], ...] = ()
    # Checks already made on the live page before it closed.
    page_checks: tuple[dict[str, Any], ...] = ()
    run_id: str = ""
    attempt_id: str = ""


@dataclass(frozen=True)
class TaskEvaluation:
    status: str
    score: float
    checks: tuple[dict[str, Any], ...]
    evaluator: str
    error: str = ""
    # The evaluator's full result, persisted by the runner as attempt evidence.
    record: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.status == "pass" and self.score == 1.0


class TaskEvaluator(Protocol):
    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation: ...


class ExactOutputsEvaluator:
    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        checks = tuple(judge_outputs(evidence.outputs, task.assertions))
        passed = bool(checks) and all(check.get("passed") is True for check in checks)
        return TaskEvaluation(
            status="pass" if passed else "fail",
            score=1.0 if passed else 0.0,
            checks=checks,
            evaluator="exact_outputs",
            error="" if passed else _format_exact_failures(checks),
        )


def _format_exact_failures(checks: tuple[dict[str, Any], ...]) -> str:
    return "; ".join(
        f"{check['output']} exact mismatch: expected {check['expected']!r}, got {check['actual']!r}"
        for check in checks
        if not check.get("passed")
    )


# Each factory receives the run's evaluator options, such as ``config_path``;
# a factory ignores the options it does not use.
EVALUATOR_KINDS: Registry[Callable[..., TaskEvaluator]] = Registry("replay evaluator")
EVALUATOR_KINDS.register("exact_outputs", lambda **_: ExactOutputsEvaluator())


class EvaluatorRegistry:
    """Score attempts with the registered evaluator their task names."""

    def __init__(self, *, config_path: str | Path | None = None, **options: Any) -> None:
        self._options: dict[str, Any] = {"config_path": config_path, **options}
        self._evaluators: dict[str, TaskEvaluator] = {}

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        kind = str((task.evaluator or {}).get("kind") or "")
        if kind not in EVALUATOR_KINDS:
            raise ValueError(f"unsupported evaluator kind: {kind!r}")
        if kind not in self._evaluators:
            self._evaluators[kind] = EVALUATOR_KINDS.get(kind)(**self._options)
        return self._evaluators[kind].evaluate(task, evidence)
