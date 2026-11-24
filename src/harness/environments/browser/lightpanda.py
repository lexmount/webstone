"""Lightpanda: a single-page CDP browser started from a local binary."""

from __future__ import annotations

from harness.environments.browser import (
    NETWORK_RESET_CDP,
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
    ]


ADAPTER = register_browser(
    BrowserAdapter(
        name="lightpanda",
        label="Lightpanda",
        flags=frozenset({NETWORK_RESET_CDP}),
        local_command=local_command,
    )
)
