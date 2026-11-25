"""Shared modules do not name a registered method, provider or browser (docs/spec.md, rule 1).

Names come from the registries that own them. The allowlist holds the remaining
occurrences and may only shrink. Regenerate it with `tests/architecture/scan.py`.
"""

from tests.architecture.scan import (
    INSTANCE_LITERAL_ALLOWLIST,
    allowlist_drift,
    catalog_instance_text,
    instance_literals,
    read_allowlist,
    registered_names,
)


def test_instance_literals_match_the_shrinking_allowlist():
    added, stale = allowlist_drift(instance_literals(), read_allowlist(INSTANCE_LITERAL_ALLOWLIST))
    assert not added, "Shared code names a registered instance:\n" + "\n".join(added)
    assert not stale, "Remove fixed occurrences from the instance-literal allowlist:\n" + "\n".join(stale)


def test_registered_names_come_from_every_registry():
    names = registered_names()
    # One name from each owner: Web Fetch types and example configs, Web Search adapters,
    # Web Extract methods and replay browsers.
    assert {"firecrawl", "kimi-fetch", "serper", "zyte-product", "lightpanda"} <= names


def test_shared_web_fetch_catalogs_hold_interface_text_only():
    """Measured values and method names belong to a report's bound or frozen content (rule 8)."""
    found = catalog_instance_text()
    assert not found, "Shared Web Fetch catalogs carry report-instance text:\n" + "\n".join(
        f"{catalog}: {text[:120]}" for catalog, texts in found.items() for text in texts)


def test_catalog_check_detects_instance_text():
    from tests.architecture.scan import MEASURED_VALUE, method_name_tokens
    assert {"Moli", "cowagent", "Kitesurf"} <= method_name_tokens()
    for text in ("通过率 99.8%", "1,500 条结果", "共 150 个任务", "核验于 2026-09-11", "版本 1.1.4"):
        assert MEASURED_VALUE.search(text), text
    for text in ("至少 2 个字符", "P95：95% 在此时间内完成", "每段至少达到 60%"):
        assert not MEASURED_VALUE.search(text), text
