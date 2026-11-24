"""Obscura: a single-page CDP browser started from a local binary."""

from __future__ import annotations

from harness.environments.browser import (
    CONTEXT_REQUEST_COOKIE_SHARING,
    VIEWPORT_OBSERVER,
    BrowserAdapter,
    register_browser,
)


def local_command(
    binary: str, port: int, profile: str, host_resolver_rules: str | None
) -> list[str]:
    return [
        str(binary),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--storage-dir",
        profile,
        "--allow-private-network",
    ]


ADAPTER = register_browser(
    BrowserAdapter(
        name="obscura",
        label="Obscura",
        flags=frozenset({CONTEXT_REQUEST_COOKIE_SHARING, VIEWPORT_OBSERVER}),
        local_command=local_command,
    )
)
