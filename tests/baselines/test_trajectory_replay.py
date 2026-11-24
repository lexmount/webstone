"""Trajectory Replay baseline: report build and check from synthetic replay results.

Replays need BrowserTool and deployed WebArena sites, so this covers the offline
stages: logical results, the report and its check.
"""

from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.publication import stage_report
from harness.trajectory_replay.webarena_report_check import check
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    read_json,
    report_snapshot,
    route_digests,
)
from tests.public_webarena_fixture import write_report_fixture
from tests.trajectory_replay.curated_report_fixture import build_curated_report
from tests.trajectory_replay.test_webarena_report import (
    STEP_DURATION_SAMPLES,
    build_replay_fixture,
)


def test_trajectory_replay_stages(tmp_path: Path, monkeypatch) -> None:
    normalize = Normalizer([tmp_path])
    output, *_ = build_replay_fixture(tmp_path, monkeypatch, failed=True)
    check(output)
    check_baseline("trajectory-replay", normalize.value({
        "results": read_json(output / "data/results.json"),
        "metrics": read_json(output / "data/metrics.json"),
        "report": report_snapshot(output, normalize),
    }))


def check_routes(name: str, report: Path, root: Path) -> None:
    check_baseline(name, {"routes": route_digests(report, Normalizer([root]))})


def test_passing_replay_report(tmp_path: Path, monkeypatch) -> None:
    output, *_ = build_replay_fixture(tmp_path, monkeypatch, failed=False)
    check_routes("trajectory-replay-pass", output, tmp_path)


def test_replay_report_with_indexed_steps(tmp_path: Path, monkeypatch) -> None:
    output, *_ = build_replay_fixture(tmp_path, monkeypatch, step_samples=STEP_DURATION_SAMPLES)
    check_routes("trajectory-replay-step-durations", output, tmp_path)


@pytest.mark.parametrize(
    ("name", "failures"),
    [
        ("baseline-leads", {"moli": ("dynamic-list", "modal-form")}),
        ("comparison-leads", {"chromium": ("modal-form",)}),
        ("tied", {"chromium": ("modal-form",), "moli": ("dynamic-list",)}),
    ],
)
def test_curated_replay_report(tmp_path: Path, name: str, failures: dict) -> None:
    build_curated_report(tmp_path / "report", ("chromium", "moli"), failures)
    check_routes(f"trajectory-replay-curated-{name}", tmp_path / "report", tmp_path)


def test_public_webarena_report(tmp_path: Path) -> None:
    source, _ = write_report_fixture(tmp_path)
    stage_report(source, tmp_path / "staged")
    check_routes("public-webarena", tmp_path / "staged", tmp_path)
