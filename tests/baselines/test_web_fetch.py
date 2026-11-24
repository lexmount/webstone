"""Web Fetch baseline: ``webstone run``, ``rescore`` and ``report`` on the fixture provider."""

from pathlib import Path
from types import SimpleNamespace

from harness.web_fetch import call_boundary, cli, runner
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    file_inventory,
    pick,
    read_json,
    read_jsonl,
    report_snapshot,
)

RECORD_FIELDS = (
    "attempt_id", "provider", "case_id", "run", "ok", "status_code", "error_type",
    "error_message", "artifact_path", "elapsed_ms", "metrics",
)


def pin_environment(monkeypatch) -> None:
    """Pin the clock and host that the run records."""
    clock = SimpleNamespace(monotonic=lambda: 0.0, perf_counter=lambda: 0.0, sleep=lambda _: None)
    monkeypatch.setattr(runner, "time", clock)
    monkeypatch.setattr(call_boundary, "time", clock)
    monkeypatch.setattr(runner, "utc_now", lambda: "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(runner, "platform", SimpleNamespace(platform=lambda: "baseline-platform"))
    monkeypatch.setattr(runner.socket, "gethostname", lambda: "baseline-host")
    monkeypatch.setattr(runner.sys, "version", "3.11.0 (baseline)")
    monkeypatch.delenv("WEBSTONE_REPORT_LANGUAGE", raising=False)


def webstone(*arguments: str) -> None:
    """Run one ``webstone`` command in this process and require success."""
    try:
        cli.main(list(arguments))
    except SystemExit as exit_:
        assert exit_.code in (0, None), f"webstone {arguments[0]} exited with {exit_.code}"


def test_web_fetch_stages(tmp_path: Path, monkeypatch) -> None:
    pin_environment(monkeypatch)
    run = tmp_path / "run"
    normalize = Normalizer([tmp_path])

    webstone("run", "--delay", "0", "--output", str(run))
    records = [pick(row, RECORD_FIELDS) for row in read_jsonl(run / "results.jsonl")]
    summary = read_json(run / "summary.json")

    webstone("score", str(run), "--dataset", str(cli.EXAMPLES / "fixture-dataset.json"))
    rescored = [pick(row, RECORD_FIELDS) for row in read_jsonl(run / "results.jsonl")]
    assert rescored == records, "rescore changed the scored records of an unchanged run"
    assert read_json(run / "summary.json") == summary

    webstone("report", str(run))
    check_baseline("web-fetch", normalize.value({
        "run_files": [path for path in file_inventory(run) if not path.startswith("report/")],
        "records": records,
        "summary": summary,
        "report": report_snapshot(run / "report", normalize),
    }))
