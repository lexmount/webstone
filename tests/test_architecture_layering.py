"""Shared packages import no track, and a track imports no other track (docs/spec.md, rule 2).

The allowlist holds the remaining violations and may only shrink: a new violation and an
entry that no longer occurs both fail. Regenerate it with `tests/architecture/scan.py`.
"""

from tests.architecture.scan import (
    LAYERING_ALLOWLIST,
    allowlist_drift,
    benchmark_module_imports,
    imported_modules,
    layering_violations,
    modules,
    read_allowlist,
)


def test_layering_matches_the_shrinking_allowlist():
    added, stale = allowlist_drift(layering_violations(), read_allowlist(LAYERING_ALLOWLIST))
    assert not added, "Shared code imports a track, or a track imports another track:\n" + "\n".join(added)
    assert not stale, "Remove fixed violations from the layering allowlist:\n" + "\n".join(stale)


def test_scan_sees_lazy_and_package_level_imports(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        "from harness import web_fetch\n"
        "def later():\n"
        "    import harness.web_search.runner\n"
        "    from harness.web_extract import pipeline\n",
        encoding="utf-8",
    )
    assert imported_modules(source, set(modules())) == {
        "harness.web_fetch", "harness.web_search.runner", "harness.web_extract.pipeline",
    }


def test_allowlist_drift_fails_in_both_directions():
    added, stale = allowlist_drift({"a": ["x", "y"]}, {"a": ["x"], "b": ["z"]})
    assert (added, stale) == (["a -> y"], ["b -> z"])


def test_shared_web_extract_modules_reach_benchmarks_through_adapters():
    assert benchmark_module_imports() == {}
