"""Chromium: the reference browser, measured from a container or a local binary."""

from __future__ import annotations

from harness.environments.browser import (
    CONTEXT_REQUEST_COOKIE_SHARING,
    HOST_RESOLVER_RULES,
    MULTIPLE_PAGES,
    NETWORK_RESET_CDP,
    REFERENCE,
    STORAGE_CDP,
    BrowserAdapter,
    register_browser,
)


def local_command(
    binary: str, port: int, profile: str, host_resolver_rules: str | None
) -> list[str]:
    command = [
        str(binary),
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        # Respect the explicitly configured HTTP fixture origins. An
        # automatic scheme upgrade changes exact evaluator URL identity.
        "--disable-features=HttpsUpgrades,HttpsFirstBalancedMode,HttpsFirstModeIncognito",
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "about:blank",
    ]
    if host_resolver_rules:
        if not isinstance(host_resolver_rules, str) or "\n" in host_resolver_rules:
            raise ValueError("Chromium host resolver rules must be one line")
        command.insert(-1, f"--host-resolver-rules={host_resolver_rules}")
    return command


ADAPTER = register_browser(
    BrowserAdapter(
        name="chromium",
        label="Chromium",
        flags=frozenset(
            {
                MULTIPLE_PAGES,
                NETWORK_RESET_CDP,
                STORAGE_CDP,
                CONTEXT_REQUEST_COOKIE_SHARING,
                HOST_RESOLVER_RULES,
                REFERENCE,
            }
        ),
        image="lex-webstone-chromium:local",
        cdp_port=9223,
        local_command=local_command,
        binary_env="CHROMIUM_BINARY",
        local_executables=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "google-chrome",
            "chromium",
            "chromium-browser",
        ),
    )
)
