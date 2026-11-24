"""The versioned call record of a Web Extract run, validated where stages read a run.

``pipeline.py`` writes and loads the per-call JSON files, but its bytes are bound into
run contracts (``pipeline_sha256``), so validation lives here and the report, export and
certification stages read runs through ``load_run`` below. Files written before schemas
were named carry no ``schema`` field and read as version 1.
"""

from __future__ import annotations

from pathlib import Path

from harness.record_schema import NUMBER, TEXT, RecordSchema, validate_records
from harness.web_extract import pipeline

# One effective call per method and case. Each call file is itself the evidence the run
# manifest binds by digest, and the call records its duration in seconds; the delivered
# outcome is judged from ``http_status`` and ``compatibility`` when scoring.
CALL_RECORD = RecordSchema(
    "webextract.call_record", 1,
    {"method": "method", "case": "case_id", "elapsed": None, "evidence": None,
     "verdict": None},
    {"state": TEXT},
    optional={"http_status": (int, type(None)), "elapsed_seconds": (*NUMBER, type(None)),
              "compatibility": TEXT, "normalized_fields": (dict, type(None)),
              "error": (str, type(None))},
)


def validate_calls(rows: list, source: str = "calls") -> list[dict]:
    return validate_records(rows, CALL_RECORD, source=source)


def load_run(run: Path):
    """The run's contract and its call records, verified by the pipeline and validated here."""
    contract, rows = pipeline.load_run(run)
    return contract, validate_calls(rows, f"{Path(run).name} calls")
