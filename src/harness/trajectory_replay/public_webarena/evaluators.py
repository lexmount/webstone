"""The WebArena-Verified evaluator kind for public WebArena Trajectory Replay."""

from __future__ import annotations

import json
from pathlib import Path

from harness.trajectory_replay.core.evaluators import (
    EVALUATOR_KINDS,
    TaskEvaluation,
    TaskEvidence,
)
from harness.trajectory_replay.core.suite import BrowserTask


class WebArenaVerifiedEvaluator:
    """Thin adapter around WebArena-Verified's stable public Python API."""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        try:
            from webarena_verified import WebArenaVerified
        except ImportError as exc:  # pragma: no cover - exercised in integration environments
            raise RuntimeError(
                "webarena-verified is required for WebArena result evaluation"
            ) from exc
        self._implementation = WebArenaVerified(
            config=Path(config_path) if config_path is not None else None
        )

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        config = task.evaluator or {}
        task_id = int(config["task_id"])
        upstream_task = self._implementation.get_task(task_id)
        if int(upstream_task.revision) != int(config["revision"]):
            raise ValueError(
                f"WebArena task {task_id} revision changed: "
                f"expected {config['revision']}, got {upstream_task.revision}"
            )
        if evidence.agent_response is None:
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="agent_response evidence is missing",
            )
        if evidence.network_har_path is None or not evidence.network_har_path.is_file():
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="network.har evidence is missing",
            )
        try:
            har = json.loads(evidence.network_har_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error=f"network.har evidence is invalid: {type(exc).__name__}",
            )
        if not isinstance(har, dict) or not (har.get("log") or {}).get("entries"):
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="network.har evidence contains no entries",
            )
        result = self._implementation.evaluate_task(
            task_id=task_id,
            agent_response=evidence.agent_response,
            network_trace=evidence.network_har_path,
        )
        payload = result.model_dump(mode="json")
        checks = tuple(payload.get("evaluators_results") or [])
        status = str(payload.get("status") or "error")
        return TaskEvaluation(
            status="pass" if status == "success" else "error" if status == "error" else "fail",
            score=float(payload.get("score") or 0.0),
            checks=checks,
            evaluator="webarena_verified",
            error=str(payload.get("error_msg") or ""),
        )


class _ConfigFollowingEvaluator:
    """Rebuild the evaluator when a Rainbond slice reset rewrites the runtime config."""

    def __init__(self, *, config_path: str | Path | None = None, **_: object) -> None:
        self._config_path = config_path
        self._evaluator: WebArenaVerifiedEvaluator | None = None
        self._config: bytes | None = None

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        config = Path(self._config_path).read_bytes() if self._config_path is not None else None
        if self._evaluator is None or config != self._config:
            self._evaluator = WebArenaVerifiedEvaluator(config_path=self._config_path)
            self._config = config
        return self._evaluator.evaluate(task, evidence)


EVALUATOR_KINDS.register("webarena_verified", _ConfigFollowingEvaluator)
