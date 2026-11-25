"""Reports load the one pinned Mermaid release; Web Fetch and WCXB load the vendored copy."""

import hashlib
from pathlib import Path

import pytest

from harness.reporting import mermaid
from harness.reporting.mermaid import (
    MERMAID_LICENSE,
    MERMAID_RUNTIME,
    MERMAID_URL,
    install_mermaid,
    mermaid_file,
    mermaid_script_attributes,
    mermaid_script_source,
    mermaid_urls,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js",
        "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs",
        "//cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js",
        "https://unpkg.com/mermaid@11.4.1/dist/mermaid.min.js",
    ],
)
def test_every_remote_mermaid_url_is_found(url: str) -> None:
    assert mermaid_urls(f"import mermaid from '{url}';") == [url]
    assert mermaid_urls(f'<script src="{url}"></script>') == [url]


def test_local_runtime_and_unrelated_urls_are_accepted() -> None:
    page = "<script src=\"assets/mermaid.min.js\"></script><a href='https://cdn.jsdelivr.net/npm/chart.js'>x</a>"
    assert mermaid_urls(page) == []


def test_installed_runtime_matches_its_pinned_digest(tmp_path: Path) -> None:
    install_mermaid(tmp_path / "assets")
    runtime = (tmp_path / "assets" / MERMAID_RUNTIME).read_bytes()
    assert hashlib.sha256(runtime).hexdigest() == mermaid.MERMAID_SHA256
    license_text = (tmp_path / "assets" / MERMAID_LICENSE).read_text(encoding="utf-8")
    assert license_text.startswith("The MIT License (MIT)")


def test_a_changed_vendored_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    altered = tmp_path / "mermaid.min.js"
    altered.write_bytes(mermaid_file(MERMAID_RUNTIME) + b"\n")
    monkeypatch.setitem(mermaid._SOURCES, MERMAID_RUNTIME, (altered, mermaid.MERMAID_SHA256))
    with pytest.raises(ValueError, match="pinned digest"):
        install_mermaid(tmp_path / "assets")


def test_loader_resolves_the_runtime_beside_the_report_script() -> None:
    assert mermaid_script_source("loader", "scriptURL") == "loader.src=new URL('mermaid.min.js',scriptURL).href"


def test_cdn_loader_names_the_vendored_release_with_integrity() -> None:
    assert f"/mermaid@{mermaid.MERMAID_VERSION}/dist/{MERMAID_RUNTIME}" in MERMAID_URL
    attributes = mermaid_script_attributes("loader")
    assert f"loader.src='{MERMAID_URL}'" in attributes
    assert f"loader.integrity='{mermaid.MERMAID_INTEGRITY}'" in attributes


def test_web_fetch_and_wcxb_scripts_name_no_remote_runtime() -> None:
    from harness.web_fetch import report, wcxb_report

    assert mermaid_urls(report.JS) == []
    assert "new URL('mermaid.min.js',wmbScriptURL)" in report.JS
    assert "mermaid_script_attributes" not in Path(wcxb_report.__file__).read_text(encoding="utf-8")


def test_only_the_owner_names_a_mermaid_url() -> None:
    owner = Path(mermaid.__file__).resolve()
    sources = [
        path
        for directory in ("src/harness", "scripts")
        for path in (ROOT / directory).rglob("*")
        if path.suffix in {".py", ".js", ".mjs", ".html"} and "vendor" not in path.parts
        and path.resolve() != owner
    ]
    assert sources
    offenders = {
        path.relative_to(ROOT).as_posix(): found
        for path in sources
        if (found := mermaid_urls(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}
