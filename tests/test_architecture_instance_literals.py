"""Shared modules do not name a registered method, provider or browser (docs/spec.md, rule 1).

Names come from the registries that own them. The allowlist holds the remaining
occurrences and may only shrink. Regenerate it with `tests/architecture/scan.py`.
"""

from tests.architecture.scan import (
    INSTANCE_LITERAL_ALLOWLIST,
    allowlist_drift,
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
