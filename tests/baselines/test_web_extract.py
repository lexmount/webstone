"""Web Extract baseline: each synthetic example mode's scored evidence, report and check."""

from pathlib import Path

import pytest

from harness.web_extract.example import create
from harness.web_extract.report import check
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    file_inventory,
    read_json,
    report_snapshot,
)

MODES = ("compatibility", "full-cohort-progress", "field-extraction-cohort",
         "field-extraction-suite")


@pytest.mark.parametrize("mode", MODES)
def test_web_extract_stages(tmp_path: Path, mode: str) -> None:
    normalize = Normalizer([tmp_path])
    report = tmp_path / "report"
    home = Path(create(report, mode=mode))
    check(report, profile=home / "profile.json", home=home)
    # Scored per-call evidence and aggregate metrics are the report's public data files.
    scored = {
        path: read_json(report / path)
        for path in file_inventory(report, (".json",))
        if {"public-evidence", "data"} & set(path.split("/")[:-1])
    }
    check_baseline(f"web-extract-{mode}", normalize.value({
        "scored": scored,
        "report": report_snapshot(report, normalize),
    }))
