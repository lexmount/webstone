"""Non-score-bearing current-retrieval candidates and their hash-bound qualification matrix."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.retrieval_acceptance import (
    build_actual_response,
    digest,
    validate_contract,
)

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates"
MATRIX = json.loads((ROOT / "qualification-matrix.json").read_text(encoding="utf-8"))
SEEDS = json.loads((ROOT / "recompiled-seed-bindings.json").read_text(encoding="utf-8"))
SEED_BY_TASK = {row["task_id"]: row for row in SEEDS["rows"]}
ROWS = {row["task_id"]: row for row in MATRIX["rows"]}


def _candidate(row: dict) -> dict:
    path = REPOSITORY / row["candidate_path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["candidate_sha256"]
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_committed_candidate_binds_its_calls_and_has_no_mutating_request():
    paths = sorted(ROOT.glob("task-*.json"))
    assert paths
    for path in paths:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        calls, contract = candidate["tool_calls"], candidate["retrieval_contract"]
        assert contract["task_id"] == candidate["task_id"], path.name
        validate_contract(contract, calls)
        for call in calls:
            script = call.get("script", "")
            assert not any(verb in script for verb in ("POST", "PUT", "DELETE", "PATCH")), path


def test_matrix_is_hash_bound_and_never_score_bearing():
    assert MATRIX["score_bearing"] is False
    assert MATRIX["profile_bound"] is False
    assert MATRIX["official_scores_modified"] is False
    assert [row["task_id"] for row in MATRIX["rows"]] == MATRIX["population_task_ids"]
    assert MATRIX["ready_90_sha256"] == SEEDS["ready_90_sha256"]
    assert (
        hashlib.sha256((ROOT / "recompiled-seed-bindings.json").read_bytes()).hexdigest()
        == MATRIX["seed_bindings_sha256"]
    )
    for row in MATRIX["rows"]:
        seed = SEED_BY_TASK[row["task_id"]]
        assert row["canonical_seed_calls_sha256"] == seed["canonical_calls_sha256"]
        assert row["task_intent_sha256"] == seed["intent_sha256"]
        assert row["level"] in {"diagnostic_only", "blocked"}
        assert row["eligible_for_qualification"] is False
        if row["level"] == "blocked":
            assert row["candidate_path"] is None
            assert row["contract_sha256"] is None
            assert row["calls_sha256"] is None
            assert row["official_score"] is None
            assert row["blocker"]
            continue
        candidate = _candidate(row)
        assert candidate["task_id"] == candidate["retrieval_contract"]["task_id"] == row["task_id"]
        assert digest(candidate["retrieval_contract"]) == row["contract_sha256"]
        assert digest(candidate["tool_calls"]) == row["calls_sha256"]
        assert row["canonical_prefix_preserved"] is (
            len(candidate["tool_calls"]) >= seed["canonical_action_count"]
            and digest(candidate["tool_calls"][: seed["canonical_action_count"]])
            == seed["canonical_calls_sha256"]
        )
        assert row["blocker"]


def test_candidate_rejects_changed_canonical_or_observation_action():
    candidate = _candidate(ROWS[29])
    for index in (0, -1):
        calls = copy.deepcopy(candidate["tool_calls"])
        calls[index]["candidate_regression_mutation"] = True
        with pytest.raises(ValueError, match="canonical actions"):
            validate_contract(candidate["retrieval_contract"], calls)


@pytest.mark.parametrize("field", ["scope", "completeness"])
def test_current_observation_must_independently_prove_scope_and_completeness(field: str):
    candidate = _candidate(ROWS[29])
    calls = candidate["tool_calls"]
    contract = candidate["retrieval_contract"]
    source = contract["sources"][0]
    records = [{} for _ in calls]
    observation = {"scope": True, "completeness": True, "answer": "independent sample"}

    def run() -> dict:
        records[source["step"] - 1] = {
            "index": source["step"],
            "arguments": calls[source["step"] - 1],
            "status": "success",
            "tool_status": "success",
            "result": json.dumps(observation),
        }
        return build_actual_response(
            contract=contract,
            task_id=29,
            calls=calls,
            records=records,
            run_id="synthetic-run",
            attempt_id="synthetic-attempt",
        )

    green = run()
    assert green["status"] == "supported"
    assert green["actual_response"]["retrieved_data"] == ["independent sample"]
    observation[field] = False
    red = run()
    assert red["status"] == "insufficient"
    assert red["actual_response"] is None


def test_reviewed_v3_evidence_digest_is_required():
    candidate = _candidate(ROWS[66])
    contract = candidate["retrieval_contract"]
    assert contract["schema"].endswith("/3")
    assert contract["semantic_reference"]["browser_semantic_inference"] is False
    changed = copy.deepcopy(contract)
    changed["semantic_reference"]["evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="review evidence"):
        validate_contract(changed, candidate["tool_calls"])
