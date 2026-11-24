"""Lexbench headless baseline: report build from synthetic retained evidence.

Runs need Docker and real browsers, so this covers the offline stages: reading
retained grader evidence, aggregation and the report. The pages render in the
browser from ``data/model.json``, so the report model and per-case outcomes carry
the comparison values.
"""

from pathlib import Path

from harness.lexbench import report
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    file_inventory,
    omit,
    read_json,
    report_snapshot,
)
from tests.lexbench_report_fixture import build_report_inputs, pin_vendor_assets


def test_lexbench_stages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEXBENCH_REPORT_RUNNER_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    pin_vendor_assets(monkeypatch)
    normalize = Normalizer([tmp_path])
    report.main(build_report_inputs(tmp_path, "minimal"))
    output = tmp_path / "report"
    check_baseline("lexbench", normalize.value({
        "model": read_json(output / "data/model.json"),
        "cases": {path: omit(read_json(output / "data/cases" / path), {"evidence"})
                  for path in file_inventory(output / "data/cases")},
        "report": report_snapshot(output, normalize),
    }))
