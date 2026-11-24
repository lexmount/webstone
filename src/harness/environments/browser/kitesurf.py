"""Kitesurf: a hosted browser reached over Cloudflare Browser Run CDP."""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from harness.environments.browser import (
    DOCUMENT_OBSERVER,
    DOM_LOGIN_READINESS,
    HOSTED_DEFAULT_CONTEXT,
    NATIVE_LOGIN_FORM_SUBMIT,
    PLAYWRIGHT_HAR_CAPTURE,
    REQUEST_BRIDGE,
    BrowserAdapter,
    register_browser,
)


def hosted_cdp(values: dict[str, str]) -> tuple[str, dict[str, str], dict[str, object]]:
    account_id = values.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = values.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise ValueError(
            "Kitesurf requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"
        )
    endpoint = (
        "wss://api.cloudflare.com/client/v4/accounts/"
        f"{quote(account_id, safe='')}"
        "/browser-run/devtools/browser?browser=kitesurf"
    )
    headers = {"Authorization": f"Bearer {api_token}"}
    public_binding = {
        "provider": "cloudflare-browser-run",
        "browser": "kitesurf",
        "protocol": "cdp",
        "endpoint_contract": (
            "wss://api.cloudflare.com/client/v4/accounts/{account_id}/"
            "browser-run/devtools/browser?browser=kitesurf"
        ),
        "account_id_sha256": hashlib.sha256(account_id.encode()).hexdigest(),
    }
    return endpoint, headers, public_binding


ADAPTER = register_browser(
    BrowserAdapter(
        name="kitesurf",
        label="Kitesurf",
        flags=frozenset(
            {
                NATIVE_LOGIN_FORM_SUBMIT,
                PLAYWRIGHT_HAR_CAPTURE,
                HOSTED_DEFAULT_CONTEXT,
                DOM_LOGIN_READINESS,
                DOCUMENT_OBSERVER,
                REQUEST_BRIDGE,
            }
        ),
        hosted_cdp=hosted_cdp,
    )
)
