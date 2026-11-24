"""Browser Use baseline: logical results and report built from synthetic attempts.

Runs need a model, browsers and WebArena sites, so this covers the offline stages:
selecting one logical result per browser and task, aggregation and the report.
"""

from pathlib import Path

from tests.baselines.snapshot import Normalizer, check_baseline, read_jsonl, report_snapshot
from tests.browser_use.test_report import build_browser_use_fixture


def outlined(route: str) -> bool:
    """Skip the frozen pilot's per-task dataset pages except the measured task 44."""
    return "datasets/" not in route or route.endswith(("index.html", "/44.html"))


def test_browser_use_stages(tmp_path: Path, monkeypatch) -> None:
    normalize = Normalizer([tmp_path])
    output, _, _ = build_browser_use_fixture(tmp_path, monkeypatch, "en", failed=True)
    check_baseline("browser-use", normalize.value({
        "results": read_jsonl(output / "data/results.jsonl"),
        "report": report_snapshot(output, normalize, outlined),
    }))
