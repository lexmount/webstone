import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import pytest
from webarena_verified.core.utils.checksum import compute_evaluator_checksum

from harness.trajectory_replay.webarena_dataset import (
    OBSERVATION_PROTOCOL,
    _effective_correction_bindings,
    _has_stale_correction_binding,
    _official_evaluator_names,
    _validate_observations,
    build_verified_trajectory_dataset,
)
from harness.trajectory_replay.webarena_plan import (
    replay_calls_binding_sha256,
    replay_site_hosts,
)


def test_committed_trajectories_match_their_task_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    selection = json.loads(
        (root / "evaluation/browser-use/webarena-verified-pilot.json").read_text(encoding="utf-8")
    )
    dataset = json.loads(
        (root / "evaluation/trajectory-replay/webarena-verified/trajectories.json").read_text(
            encoding="utf-8"
        )
    )

    expected_ids = {int(item["task_id"]) for item in selection["tasks"]}
    actual_ids = {int(item["task_id"]) for item in dataset["trajectories"]}
    required = len(expected_ids)

    assert required > 0
    assert (
        dataset["selection_sha256"]
        == hashlib.sha256(
            (root / "evaluation/browser-use/webarena-verified-pilot.json").read_bytes()
        ).hexdigest()
    )
    assert expected_ids == actual_ids
    assert dataset["counts"] == {
        "verified_tasks": required,
        "verified_trajectories": required,
    }
    for item in dataset["trajectories"]:
        verification = item["verification"]
        assert verification["browsers"] == [dataset["promotion_policy"]["qualification_browser"]]
        assert verification["process_status"] == "pass"
        assert verification["evaluator_status"] == "pass"
        assert verification["model_calls"] == 0


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def test_stale_correction_binding_is_task_local() -> None:
    correction = _effective_correction_bindings()[448]
    current = {
        "contract_correction_id": correction[0],
        "contract_correction_source_sha256": correction[1],
        "corrected_task_sha256": correction[2],
    }
    assert _has_stale_correction_binding(current, 448) is False
    assert _has_stale_correction_binding({**current, "corrected_task_sha256": "old"}, 448)
    assert _has_stale_correction_binding({}, 44) is False


def _materialize_autonomous_sources(seeds: Path) -> dict[str, object]:
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["source"].setdefault("official_data_checksum", "data-checksum")
    authority_rows: dict[str, list[dict[str, object]]] = {}
    for task in payload["tasks"]:
        for candidate in task.get("candidates") or []:
            if candidate.get("candidate_kind") == "expert_repair":
                continue
            candidate.setdefault("method", {"model": "model", "engine": "chromium"})
            candidate.setdefault("source_execution", 1)
            candidate.setdefault("source_run_id", "source-run")
            candidate.setdefault("action_count", len(candidate.get("tool_calls") or []))
            candidate.setdefault("official_score", 1.0)
            candidate.setdefault("clean", True)
            candidate.setdefault("recorded_response", {})
            events = []
            for index, arguments in enumerate(candidate.get("tool_calls") or [], start=1):
                call_id = f"call-{index}"
                events.extend(
                    [
                        {
                            "type": "tool_execution_start",
                            "data": {
                                "tool_call_id": call_id,
                                "tool_name": "BrowserTool",
                                "arguments": arguments,
                            },
                        },
                        {
                            "type": "tool_execution_end",
                            "data": {"tool_call_id": call_id, "status": "success"},
                        },
                    ]
                )
            source_payloads = {
                "events": events,
                "response": candidate["recorded_response"],
                "evaluation": {
                    "task_id": task["task_id"],
                    "status": "success",
                    "score": 1.0,
                },
            }
            source = candidate.setdefault("source", {})
            for kind, value in source_payloads.items():
                path = _write(
                    seeds.parent / f"source-{task['task_id']}-{candidate['id']}-{kind}.json",
                    value,
                )
                source[f"{kind}_path"] = str(path)
                source[f"{kind}_sha256"] = _sha256(path)
            model = str(candidate["method"]["model"])
            authority_rows.setdefault(model, []).append(
                {
                    "engine": candidate["method"]["engine"],
                    "task_id": task["task_id"],
                    "attempt": candidate["source_execution"],
                    "run_id": candidate["source_run_id"],
                    "score": 1.0,
                    "paths": {
                        kind: source[f"{kind}_path"]
                        for kind in ("events", "response", "evaluation")
                    },
                }
            )
    source_evidence = {}
    for model, rows in authority_rows.items():
        root = seeds.parent / f"authority-{model}"
        manifest = _write(
            root / "manifest.json",
            {
                "protocol": {"model": model},
                "selection_sha256": payload["source"]["selection_sha256"],
            },
        )
        attempts = root / "attempts.jsonl"
        attempts.parent.mkdir(parents=True, exist_ok=True)
        attempts.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        source_evidence[model] = {
            "root": str(root),
            "attempts_sha256": _sha256(attempts),
            "manifest_sha256": _sha256(manifest),
        }
    payload["source"]["evidence"] = source_evidence
    seeds.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _profile(tmp_path: Path, task_ids: list[int]) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    selection = _write(
        repo / "evaluation/browser-use/selection.json",
        {"tasks": [{"task_id": task_id} for task_id in task_ids]},
    )
    selection_sha256 = _sha256(selection)
    repairs = _write(
        repo / "evaluation/trajectory-replay/repairs.json",
        {
            "schema": "repairs",
            "repairs": [
                {
                    "task_id": task_id,
                    "candidate_id": f"expert-{task_id}",
                    "source": {"author": "reviewed"},
                    "repair_basis": {"root_cause": "wrong route selection"},
                    "tool_calls": [{"action": "navigate", "url": "http://host"}],
                    "expected_response": {"status": "SUCCESS"},
                }
                for task_id in task_ids
            ],
        },
    )
    profile = _write(
        repo / "evaluation/trajectory-replay/webarena-verified/current-run.json",
        {
            "schema": "lex_webarena_trajectory_replay_profile/1",
            "task_selection": {
                "path": "evaluation/browser-use/selection.json",
                "task_count": len(task_ids),
                "sha256": selection_sha256,
            },
            "seed_inventory": {
                "expert_repairs_path": "evaluation/trajectory-replay/repairs.json",
                "expert_repairs_sha256": _sha256(repairs),
                "required_ready_tasks": len(task_ids),
                "canonical_trajectories_per_task": 1,
            },
            "methods": [
                {"id": "chromium", "engine": "chromium"},
                {"id": "moli", "engine": "moli"},
            ],
            "qualification": {
                "reference_engine": "chromium",
                "required_task_count": len(task_ids),
            },
            "runs_per_task": 1,
            "resources": {"cpus": 2.0, "memory": "2g", "sample_interval_ms": 100},
            "execution": {
                "environment_pool_count": 1,
                "method_parallelism": 1,
                "task_parallelism": 1,
                "reset_before_every_task": True,
                "reset_before_every_method": True,
                "teardown_between_methods": True,
                "trusted_source_site_hosts": ["host.docker.internal"],
            },
            "scoring": {"model_calls": 0},
        },
    )
    return profile, selection_sha256, _sha256(repairs)


def _receipt(
    seeds: Path,
    profile: Path,
    *,
    task_ids: list[int],
    attempts: list[dict[str, object]],
    run_id: str = "run",
    execution_scope: str = "qualification",
) -> dict[str, object]:
    attempts = json.loads(json.dumps(attempts))
    seed_payload = _materialize_autonomous_sources(seeds)
    seed_tasks = {int(item["task_id"]): item for item in seed_payload["tasks"]}
    sessions = []
    physical_executions = []
    for attempt in attempts:
        attempt["schema"] = "lex_webarena_trajectory_replay_attempt/1"
        attempt["physical_execution"] = 1
        session_id = f"session-{attempt['engine']}"
        attempt["browser_session_id"] = session_id
        session = next((item for item in sessions if item["id"] == session_id), None)
        if session is None:
            session = {
                "id": session_id,
                "engine": attempt["engine"],
                "site_access_host": "gateway",
                "task_ids": [],
            }
            sessions.append(session)
        session["task_ids"].append(attempt["task_id"])
        task = seed_tasks[int(attempt["task_id"])]
        candidate = next(
            item for item in task["candidates"] if item["id"] == attempt["source_candidate_id"]
        )
        trace_arguments = candidate["tool_calls"]
        attempt["source_tool_calls_sha256"] = _json_sha256(candidate["tool_calls"])
        attempt["executed_tool_calls_sha256"] = _json_sha256(trace_arguments)
        attempt["source_site_hosts"] = sorted(replay_site_hosts(candidate["tool_calls"]))
        attempt["runtime_site_host"] = "gateway"
        attempt["replay_calls_binding_sha256"] = replay_calls_binding_sha256(
            trace_arguments, allowed_hosts=replay_site_hosts(trace_arguments)
        )
        attempt["evaluation_input_sha256"] = _json_sha256(candidate["recorded_response"])
        payloads = {
            "trace": [
                {
                    "index": index,
                    "arguments": arguments,
                    "status": "success",
                    "result": (
                        "Navigated to: http://host\n--- Page Snapshot ---\nPage: example\n"
                        if arguments.get("action") == "navigate"
                        else "Page: example\nInteractive elements: 0\n---\nobserved"
                    ),
                }
                for index, arguments in enumerate(trace_arguments, start=1)
            ],
            "network_har": {"log": {"entries": []}},
            "evaluation": {
                "task_id": attempt["task_id"],
                "status": "success",
                "score": 1.0,
                "webarena_verified_version": version("webarena-verified"),
                "webarena_verified_evaluator_checksum": compute_evaluator_checksum(),
                "webarena_verified_data_checksum": seed_payload["source"]["official_data_checksum"],
                "evaluators_results": [
                    {
                        "evaluator_name": evaluator,
                        "status": "success",
                        "score": 1.0,
                        "actual": (
                            candidate["recorded_response"]
                            if evaluator == "AgentResponseEvaluator"
                            else [{"method": "POST", "url": "http://site/action"}]
                        ),
                    }
                    for evaluator in _official_evaluator_names()[int(attempt["task_id"])]
                ],
            },
        }
        if attempt.get("contract_correction_id"):
            payloads["evaluation"]["replay_contract_correction"] = {
                "correction_id": attempt["contract_correction_id"],
                "source_sha256": attempt["contract_correction_source_sha256"],
                "corrected_task_sha256": attempt["corrected_task_sha256"],
            }
        evidence = {}
        for kind, payload in payloads.items():
            path = _write(
                seeds.parent / f"{run_id}-{attempt['engine']}-{attempt['task_id']}-{kind}.json",
                payload,
            )
            evidence[kind] = {"path": str(path), "sha256": _sha256(path)}
        attempt["evidence"] = evidence
        physical_executions.append(
            {
                "engine": attempt["engine"],
                "task_id": attempt["task_id"],
                "physical_execution": 1,
                "status": "complete",
                "evidence": evidence,
            }
        )
    return {
        "schema": "lex_webarena_trajectory_replay_suite/1",
        "execution_scope": execution_scope,
        "status": "complete",
        "profile_sha256": _sha256(profile),
        "source_seed_manifest_sha256": _sha256(seeds),
        "task_ids": task_ids,
        "engines": ["chromium"],
        "runtime": {
            "webstone_commit": "webstone-commit",
            "cowagent_commit": "cowagent-commit",
            "resources": {"cpus": 2.0, "memory": "2g", "sample_interval_ms": 100},
            "browser_images": {"chromium": {"id": "chromium-image"}},
            "site_images": {"gitlab": {"id": "gitlab-image"}},
        },
        "method_teardown": {"chromium": True},
        "sessions": sessions,
        "physical_executions": physical_executions,
        "isolation": {
            "environment_pool_count": 1,
            "method_parallelism": 1,
            "task_parallelism": 1,
            "reset_before_every_task": True,
            "reset_before_every_method": True,
            "teardown_between_methods": True,
        },
        "run_id": run_id,
        "attempts": attempts,
    }


_AUTONOMOUS = {
    "method": {"model": "model", "engine": "chromium"},
    "source_execution": 1,
    "source": {},
}


def _expert(repair_sha256: str) -> dict[str, object]:
    return {
        "candidate_kind": "expert_repair",
        "tool_calls": [{"action": "navigate", "url": "http://host"}],
        "source": {
            "kind": "expert_repair_spec",
            "repair_spec_sha256": repair_sha256,
            "details": {"author": "reviewed"},
        },
        "repair_basis": {"root_cause": "wrong route selection"},
    }


def _fixture(tmp_path: Path, task_id: int, candidate=None, *, expert=False):
    """Profile, one-task seed manifest and a receipt builder for its canonical candidate."""
    profile, selection_sha256, repair_sha256 = _profile(tmp_path, [task_id])
    candidate_id = f"expert-{task_id}" if expert else f"candidate-{task_id}"
    seeds = _write(tmp_path / "seeds.json", {
        "schema": "lex_browser_use_replay_seeds/2",
        "source": {"selection_sha256": selection_sha256},
        "expert_repairs": {"repair_spec_sha256": repair_sha256},
        "tasks": [{
            "task_id": task_id,
            "canonical_candidate_id": candidate_id,
            "replay_status": "ready",
            "candidates": [{
                "id": candidate_id,
                "action_count": 1,
                "tool_calls": [{"action": "snapshot"}],
                "recorded_response": {"status": "SUCCESS"},
                **(_expert(repair_sha256) if expert else _AUTONOMOUS),
                **(candidate or {}),
            }],
        }],
    })

    def receipt(run_id="run", scope="qualification", **attempt) -> dict[str, object]:
        row = {"task_id": task_id, "source_candidate_id": candidate_id, "engine": "chromium",
               "process_status": "pass", "evaluator_status": "pass",
               "completed_action_count": 1, **attempt}
        return _receipt(seeds, profile, task_ids=[task_id], attempts=[row], run_id=run_id,
                        execution_scope=scope)

    return profile, seeds, repair_sha256, receipt


def _rejects(tmp_path: Path, seeds: Path, profile: Path, payload, message: str) -> None:
    receipt = _write(tmp_path / "receipt.json", payload)
    with pytest.raises(ValueError, match=message):
        build_verified_trajectory_dataset(seeds, [receipt], profile_path=profile)


def _rewrite_evidence(attempt: dict, kind: str, value: object) -> Path:
    path = Path(attempt["evidence"][kind]["path"])
    path.write_text(json.dumps(value), encoding="utf-8")
    attempt["evidence"][kind]["sha256"] = _sha256(path)
    return path


def test_qualification_receipt_rejects_missing_attempt_identifier(tmp_path: Path) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 1)
    payload = receipt()
    payload["attempts"][0]["task_id"] = None
    _rejects(tmp_path, seeds, profile, payload, "invalid attempt task_id")


@pytest.mark.parametrize("reset_per_task", [True, False])
def test_promotion_requires_candidate_to_pass_reference_qualification(
    tmp_path: Path, reset_per_task: bool
) -> None:
    profile, seeds, _, receipt = _fixture(
        tmp_path, 44, {"tool_calls": [{"action": "navigate", "url": "http://host"}]}
    )
    profile_payload = json.loads(profile.read_text(encoding="utf-8"))
    profile_payload["execution"]["reset_before_every_task"] = reset_per_task
    profile_payload["execution"]["retry_failed_task"] = False
    _write(profile, profile_payload)
    receipts = []
    for name, attempt in (
        ("failed-run-44", {"process_status": "fail", "evaluator_status": "not_run",
                           "completed_action_count": 0}),
        ("run-44", {}),
    ):
        payload = receipt(run_id=name, **attempt)
        payload["isolation"]["reset_before_every_task"] = reset_per_task
        receipts.append(_write(tmp_path / f"{name}.json", payload))

    result = build_verified_trajectory_dataset(seeds, receipts, profile_path=profile)

    assert result["counts"] == {"verified_tasks": 1, "verified_trajectories": 1}
    verification = result["trajectories"][0]["verification"]
    assert verification["model_calls"] == 0
    assert verification["browsers"] == ["chromium"]
    assert verification["contract"] == {"kind": "official_task_contract"}
    assert verification["receipt_run_ids"] == ["run-44"]
    assert result["promotion_policy"]["measured_browsers_are_not_promotion_gates"] is True
    assert result["semantics"]["browser_use_agent_visibility"] == "hidden"
    assert result["provenance"]["observation_protocol"] == OBSERVATION_PROTOCOL


@pytest.mark.parametrize("row", [
    {"arguments": {"action": "snapshot"}, "observation_status": "error", "result": "Page: x"},
    {"arguments": {"action": "snapshot"}, "result": "[Snapshot error: context destroyed]"},
    {
        "arguments": {"action": "navigate"},
        "result": "Navigated\n--- Page Snapshot ---\n[Snapshot error: gone]",
    },
    {"arguments": {"action": "snapshot"}, "result": ""},
    {"arguments": {"action": "navigate"}, "result": "Navigated successfully"},
])
def test_qualification_rejects_unavailable_observations(row):
    with pytest.raises(ValueError, match="qualification"):
        _validate_observations([{"status": "success", **row}])


def test_qualification_keeps_literal_page_content_and_successful_observations():
    _validate_observations([
        {
            "arguments": {"action": "snapshot"},
            "result": "Page: example\n---\n[Snapshot error: quoted page content]",
        },
        {
            "arguments": {"action": "navigate"},
            "result": "Navigated\n--- Page Snapshot ---\nPage: example",
        },
        {"arguments": {"action": "click"}, "result": "Clicked"},
    ])


def test_promotion_rejects_incomplete_method_task_matrix(tmp_path: Path) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 1)
    payload = receipt()
    payload["attempts"] = []
    _rejects(tmp_path, seeds, profile, payload, "complete method-task matrix")


def test_promotion_supports_hash_bound_expert_repair(tmp_path: Path) -> None:
    correction = _effective_correction_bindings()[448]
    profile, seeds, repair_sha256, receipt = _fixture(tmp_path, 448, expert=True)
    path = _write(tmp_path / "receipt.json", receipt(
        contract_correction_id=correction[0],
        contract_correction_source_sha256=correction[1],
        corrected_task_sha256=correction[2],
    ))

    trajectory = build_verified_trajectory_dataset(seeds, [path], profile_path=profile)[
        "trajectories"
    ][0]
    assert trajectory["source"]["kind"] == "expert_repair_spec"
    assert trajectory["source"]["repair_spec_sha256"] == repair_sha256
    assert trajectory["verification"]["contract"] == {
        "kind": "corrected_replay_contract",
        "correction_id": correction[0],
        "correction_source_sha256": correction[1],
        "corrected_task_sha256": correction[2],
    }


def test_promotion_accepts_qualification_shard_but_rejects_nonisolated_receipt(
    tmp_path: Path,
) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 1, expert=True)
    shard = _write(tmp_path / "shard.json", receipt(scope="qualification_shard"))
    result = build_verified_trajectory_dataset(seeds, [shard], profile_path=profile)
    assert result["counts"]["verified_tasks"] == 1

    nonisolated = receipt()
    nonisolated["attempts"] = []
    nonisolated["isolation"]["method_parallelism"] = 2
    _rejects(tmp_path, seeds, profile, nonisolated, "required isolation")


def test_promotion_rejects_receipt_claim_when_evaluation_evidence_failed(tmp_path: Path) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 1)
    payload = receipt()
    _rewrite_evidence(payload["attempts"][0], "evaluation",
                      {"task_id": 1, "status": "fail", "score": 0})
    _rejects(tmp_path, seeds, profile, payload, "passing score")


@pytest.mark.parametrize("degraded_observation", [False, True])
def test_promotion_rejects_trace_that_differs_from_canonical_actions(
    tmp_path: Path, degraded_observation: bool
) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 1)
    payload = receipt()
    attempt = payload["attempts"][0]
    different_arguments = (
        [{"action": "snapshot"}]
        if degraded_observation
        else [{"action": "navigate", "url": "https://example.com"}]
    )
    _rewrite_evidence(attempt, "trace", [{
        "index": 1, "arguments": different_arguments[0], "status": "success",
        "result": "[Snapshot error: context destroyed]",
    }])
    attempt["executed_tool_calls_sha256"] = _json_sha256(different_arguments)
    attempt["replay_calls_binding_sha256"] = replay_calls_binding_sha256(
        different_arguments, allowed_hosts=replay_site_hosts(different_arguments)
    )
    expected = "snapshot error" if degraded_observation else "canonical replay actions"
    _rejects(tmp_path, seeds, profile, payload, expected)


def test_promotion_rejects_missing_official_evaluator(tmp_path: Path) -> None:
    profile, seeds, _, receipt = _fixture(tmp_path, 44)
    payload = receipt()
    attempt = payload["attempts"][0]
    evaluation = json.loads(
        Path(attempt["evidence"]["evaluation"]["path"]).read_text(encoding="utf-8")
    )
    evaluation["evaluators_results"] = evaluation["evaluators_results"][:1]
    _rewrite_evidence(attempt, "evaluation", evaluation)
    _rejects(tmp_path, seeds, profile, payload, "canonical input")
