"""Offline scenarios for visual input binding and receipt installation."""

import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.browser_use import report_visual
from harness.reporting import visual
from harness.reporting.visual import browser_executable, prepare_check, write_receipt

ROOT = Path(__file__).resolve().parents[1]


def test_invalid_explicit_browser_does_not_select_another(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_USE_VISUAL_BROWSER", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="Configured.*missing"):
        browser_executable()


@pytest.mark.parametrize("kind", ["linked-report", "linked-receipt", "nested-receipt"])
def test_visual_outputs_cannot_replace_or_extend_inputs(tmp_path, kind):
    report = tmp_path / "report"
    report.mkdir()
    receipt = tmp_path / "receipt.json"
    if kind == "linked-report":
        linked = tmp_path / "linked"
        linked.symlink_to(report, target_is_directory=True)
        report = linked
    elif kind == "linked-receipt":
        target = tmp_path / "target"
        target.write_text("original", encoding="utf-8")
        receipt.symlink_to(target)
    else:
        receipt = report / "receipt.json"
    with pytest.raises(ValueError, match="[Ss]ymlink|outside|unlinked"):
        prepare_check(report, receipt)


def test_failed_receipt_installation_retains_previous_bytes(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"previous receipt")

    def fail_replace(source, target):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        write_receipt(receipt, {"states": []})
    assert receipt.read_bytes() == b"previous receipt"
    assert set(tmp_path.iterdir()) == {receipt}


@pytest.mark.parametrize("drift", ["report", "browser", "checker", "launch"])
def test_render_failure_or_input_change_cannot_install_receipt(tmp_path, monkeypatch, drift):
    import playwright.sync_api as playwright_api

    report = tmp_path / "report"
    report.mkdir()
    index = report / "index.html"
    index.write_text("<html>original</html>", encoding="utf-8")
    binary = tmp_path / "browser"
    binary.write_text("original binary", encoding="utf-8")
    monkeypatch.setenv("BROWSER_USE_VISUAL_BROWSER", str(binary))
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"previous receipt")
    monkeypatch.setattr(report_visual, "_routes", lambda _: ["index.html"])
    lifecycle = []

    @contextmanager
    def server(_report):
        try:
            yield "http://127.0.0.1:12345"
        finally:
            lifecycle.append("server closed")

    monkeypatch.setattr(report_visual, "local_report_server", server)

    class Page:
        def route(self, *args):
            pass

        def goto(self, *args, **kwargs):
            pass

        def evaluate(self, script, *args):
            if args:
                return None
            if drift == "report":
                index.write_text("<html>changed</html>", encoding="utf-8")
            if drift == "browser":
                binary.write_text("changed binary", encoding="utf-8")
            return {"overflow": 0, "clipped": [], "vertical": [], "textOverflow": []}

        def close(self):
            pass

    def launch(**kwargs):
        if drift == "launch":
            raise RuntimeError("browser launch failed")
        return SimpleNamespace(
            new_page=lambda **kwargs: Page(), close=lambda: lifecycle.append("browser closed")
        )

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    monkeypatch.setattr(playwright_api, "sync_playwright", playwright)
    if drift == "checker":
        identities = iter(({"source": "original"}, {"source": "changed"}))
        monkeypatch.setattr(report_visual, "checker_sources", lambda _: next(identities))
    with pytest.raises((RuntimeError, ValueError), match="changed|launch failed"):
        report_visual.check(report, receipt)
    assert receipt.read_bytes() == b"previous receipt"
    assert "server closed" in lifecycle
    assert ("browser closed" in lifecycle) is (drift != "launch")


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("http://127.0.0.1:1234/assets/app.js", True),
        ("http://127.0.0.1:4321/assets/app.js", False),
        ("https://example.invalid/script.js", False),
    ],
)
def test_local_rendering_does_not_request_remote_resources(url, allowed):
    from harness.reporting.visual import local_page

    handlers = []
    page = SimpleNamespace(route=lambda pattern, handler: handlers.append(handler))
    browser = SimpleNamespace(new_page=lambda **kwargs: page)
    assert local_page(browser, "http://127.0.0.1:1234") is page
    actions = []
    route = SimpleNamespace(
        request=SimpleNamespace(url=url),
        continue_=lambda: actions.append("continued"),
        abort=lambda: actions.append("aborted"),
    )
    handlers[0](route)
    assert actions == ["continued" if allowed else "aborted"]


def test_receipts_verify_against_kept_checker_copies_not_live_modules(tmp_path):
    receipt = tmp_path / "visual.json"
    checker = tmp_path / "checker.py"
    checker.write_text("first checker", encoding="utf-8")
    names = list(visual.checker_paths(checker))
    kept = visual.keep_sources(receipt, visual.checker_paths(checker))
    checker.write_text("evolved checker", encoding="utf-8")
    assert visual.sources_are_frozen(receipt, kept, names)
    assert not visual.sources_are_frozen(receipt, kept, ["checker.py"])
    copy = receipt.parent / visual.SOURCE_COPIES / (kept["checker.py"] + ".py.txt")
    copy.write_text("tampered", encoding="utf-8")
    assert not visual.sources_are_frozen(receipt, kept, names)
    assert not visual.sources_are_frozen(receipt, {**kept, "checker.py": "0" * 64}, names)


def test_receipts_written_before_kept_copies_resolve_in_the_git_store(tmp_path):
    index = json.loads((visual.FROZEN_SOURCES / "index.json").read_text(encoding="utf-8"))
    stored = {row["path"].rsplit("/", 1)[1]: row["sha256"] for row in index["sources"]}
    recorded = {"report_visual.py": stored["report_visual.py"], "visual.py": stored["visual.py"]}
    assert visual.sources_are_frozen(tmp_path / "visual.json", recorded,
                                     ["report_visual.py", "visual.py"])


def test_git_store_holds_the_listed_historical_bytes():
    index = json.loads((visual.FROZEN_SOURCES / "index.json").read_text(encoding="utf-8"))
    listed = {row["source_artifact"] for row in index["sources"]}
    assert listed == {path.name for path in visual.FROZEN_SOURCES.glob("*.py.txt")}
    for row in index["sources"]:
        raw = (visual.FROZEN_SOURCES / row["source_artifact"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        shown = subprocess.run(["git", "show", f"{row['git_commit']}:{row['path']}"], cwd=ROOT,
                               capture_output=True, check=False)
        if shown.returncode == 0:  # shallow checkouts may lack the commit
            assert shown.stdout == raw


def test_visual_browser_comes_from_the_reference_adapter(tmp_path, monkeypatch):
    from harness.environments.browser import browser_adapter, reference_engine

    adapter = browser_adapter(reference_engine())
    binary = tmp_path / "reference-browser"
    binary.write_text("binary", encoding="utf-8")
    monkeypatch.delenv("BROWSER_USE_VISUAL_BROWSER", raising=False)
    monkeypatch.setenv(adapter.binary_env, str(binary))
    assert browser_executable() == str(binary)
