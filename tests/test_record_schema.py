import json

import pytest

from harness.record_schema import RecordSchema, RecordSchemaError, read_records, validate_records
from harness.web_fetch.models import (
    CALL_RECORD, FROZEN_CALL_RECORD, FROZEN_RESULT, FROZEN_SCORED_RESULT, SCORED_RESULT,
    measured_frozen_result, score_frozen_result,
)

LIVE = {"attempt_id": "p__c__r001", "provider": "p", "case_id": "c", "started_at": "2026-01-01T00:00:00Z",
        "elapsed_ms": 12.5, "artifact_path": "artifacts/p/c/run-001.txt", "ok": True, "metrics": {}}


def test_historical_records_without_a_schema_read_as_the_first_version(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(LIVE) + "\n\n", encoding="utf-8")
    assert read_records(path, SCORED_RESULT) == [LIVE]


def test_a_record_naming_a_known_version_uses_it():
    newer = SCORED_RESULT.extend("webfetch.scored_result", 2, verdict_reason=(str,))
    record = {**LIVE, "schema": newer.id, "verdict_reason": "passed"}
    assert validate_records([LIVE, record], SCORED_RESULT, newer) == [LIVE, record]
    with pytest.raises(RecordSchemaError, match="lacks 'verdict_reason'"):
        validate_records([{**LIVE, "schema": newer.id}], SCORED_RESULT, newer)


def test_unknown_schema_missing_fields_and_wrong_types_are_rejected():
    with pytest.raises(RecordSchemaError, match="schema must be a string"):
        validate_records([{**LIVE, "schema": []}], CALL_RECORD)
    with pytest.raises(RecordSchemaError, match="unsupported schema"):
        validate_records([{**LIVE, "schema": "webfetch.call_record/9"}], CALL_RECORD)
    with pytest.raises(RecordSchemaError, match="lacks 'metrics'"):
        validate_records([{key: value for key, value in LIVE.items() if key != "metrics"}], SCORED_RESULT)
    with pytest.raises(RecordSchemaError, match="'elapsed_ms' has type bool"):
        validate_records([{**LIVE, "elapsed_ms": True}], CALL_RECORD)
    with pytest.raises(RecordSchemaError, match="'status_code' has type str"):
        validate_records([{**LIVE, "status_code": "200"}], CALL_RECORD)
    with pytest.raises(RecordSchemaError, match="lacks 'physical_attempt'"):
        validate_records([{"provider": "p", "track_id": "c", "elapsed_ms": 1, "output_sha256": "x",
                           "ok": False, "attempt_id": "a"}], FROZEN_CALL_RECORD)


def test_every_schema_binds_each_common_role():
    with pytest.raises(ValueError, match="bind every common role"):
        RecordSchema("partial", 1, {"method": "provider"})


def test_tagged_frozen_results_round_trip_through_scoring():
    row = {"schema": FROZEN_RESULT.id, "provider": "p", "track_id": "c", "elapsed_ms": 3,
           "artifact_path": "artifacts/p/c.md", "ok": True, "attempt_id": "p__c__r001", "output_sha256": "x"}
    validate_records([row], FROZEN_RESULT)
    scored = score_frozen_result(row, {"overall": {}})
    assert validate_records([scored], FROZEN_RESULT, FROZEN_SCORED_RESULT) == [scored]
    assert scored["schema"] == FROZEN_SCORED_RESULT.id
    assert measured_frozen_result(scored) == row
    untagged = {key: value for key, value in row.items() if key != "schema"}
    assert measured_frozen_result(score_frozen_result(untagged, {})) == untagged
