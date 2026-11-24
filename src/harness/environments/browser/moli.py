"""Moli: a layout-publishing CDP browser, measured from a container or a local binary."""

from __future__ import annotations

from harness.environments.browser import (
    CONTEXT_REQUEST_COOKIE_SHARING,
    LAYOUT_PUBLICATION,
    MULTIPLE_PAGES,
    NETWORK_RESET_CDP,
    STORAGE_CDP,
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
        "--layout",
        "--font",
        "--profile-dir",
        profile,
    ]


ADAPTER = register_browser(
    BrowserAdapter(
        name="moli",
        label="Moli",
        flags=frozenset(
            {
                MULTIPLE_PAGES,
                NETWORK_RESET_CDP,
                STORAGE_CDP,
                CONTEXT_REQUEST_COOKIE_SHARING,
                LAYOUT_PUBLICATION,
            }
        ),
        image="lex-webstone-moli:local",
        image_env="WEBSTONE_MOLI_IMAGE",
        cdp_port=9222,
        version_command=("moli", "--version"),
        local_command=local_command,
    )
)
