"""Web Search baseline: the synthetic example's scored calls, report build and check."""

from pathlib import Path

import pytest

from harness.web_search.example import example
from harness.web_search.report import check
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    pick,
    read_jsonl,
    report_snapshot,
)

RECORD_FIELDS = ("id", "case_id", "method", "round", "elapsed_ms", "http_status", "error",
                 "results", "cost", "metrics")


@pytest.mark.parametrize("variant", ["default", "comparison"])
def test_web_search_stages(tmp_path: Path, variant: str) -> None:
    normalize = Normalizer([tmp_path])
    example(tmp_path, comparison=variant == "comparison")
    check(tmp_path / "report")
    name = "web-search" if variant == "default" else f"web-search-{variant}"
    check_baseline(name, normalize.value({
        "records": [pick(row, RECORD_FIELDS)
                    for row in read_jsonl(tmp_path / "fixture/results.jsonl")],
        "report": report_snapshot(tmp_path / "report", normalize),
    }))
