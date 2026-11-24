"""Web Fetch baseline: ``webstone run``, ``rescore`` and ``report`` on the fixture provider."""

from pathlib import Path
from types import SimpleNamespace

from harness.web_fetch import call_boundary, cli, runner
from harness.web_fetch.report import generate_wcxb, generate_webmainbench
from tests.baselines.snapshot import (
    Normalizer,
    check_baseline,
    file_inventory,
    pick,
    read_json,
    read_jsonl,
    report_snapshot,
    route_digests,
)
from tests.test_webmainbench_full_report import render_full_report
from tests.test_webmainbench_report import sample_report_inputs
from tests.wcxb_fixture import create_report_inputs

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


def check_routes(name: str, report: Path, root: Path) -> None:
    check_baseline(name, {"routes": route_digests(report, Normalizer([root]))})


def test_wcxb_report(tmp_path: Path) -> None:
    inputs = create_report_inputs(tmp_path, english_source=True)
    generate_wcxb(*inputs, include_english=True)
    check_routes("wcxb", inputs[0], tmp_path)


def test_webmainbench_sample_report(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.mkdir()
    dataset, records, summaries, manifests = sample_report_inputs(output)
    review = {"status": "passed", "reviewed_at": "2026-09-14T00:00:00Z", "checks": []}
    generate_webmainbench(
        output, dataset, records, summaries, manifests, review, include_english=True
    )
    check_routes("webmainbench-sample", output, tmp_path)


def test_webmainbench_full_report(tmp_path: Path) -> None:
    render_full_report(tmp_path, "method-binding.json")
    check_routes("webmainbench-full", tmp_path, tmp_path)
