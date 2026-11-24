"""Trajectory Replay baseline: report build and check from synthetic replay results.

Replays need BrowserTool and deployed WebArena sites, so this covers the offline
stages: logical results, the report and its check.
"""

from pathlib import Path

from harness.trajectory_replay.webarena_report_check import check
from tests.baselines.snapshot import Normalizer, check_baseline, read_json, report_snapshot
from tests.trajectory_replay.test_webarena_report import build_replay_fixture


def test_trajectory_replay_stages(tmp_path: Path, monkeypatch) -> None:
    normalize = Normalizer([tmp_path])
    output, *_ = build_replay_fixture(tmp_path, monkeypatch, failed=True)
    check(output)
    check_baseline("trajectory-replay", normalize.value({
        "results": read_json(output / "data/results.json"),
        "metrics": read_json(output / "data/metrics.json"),
        "report": report_snapshot(output, normalize),
    }))
