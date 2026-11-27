"""Kitesurf: CDP connections to Cloudflare or a pinned container deployment."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, urlsplit

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
    deployment_keys = (
        "KITESURF_CDP_ENDPOINT", "KITESURF_IMAGE", "KITESURF_SOURCE_COMMIT",
        "KITESURF_DEPLOYMENT_ID", "KITESURF_JS_ENGINE",
    )
    if any(values.get(key, "").strip() for key in deployment_keys):
        if any(not values.get(key, "").strip() for key in deployment_keys):
            raise ValueError("Pinned Kitesurf deployment requires " + ", ".join(deployment_keys))
        endpoint = values["KITESURF_CDP_ENDPOINT"].strip()
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"ws", "wss"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("Kitesurf container endpoint must be a credential-free WebSocket URL")
        image = values["KITESURF_IMAGE"].strip()
        revision = values["KITESURF_SOURCE_COMMIT"].strip()
        js_engine = values["KITESURF_JS_ENGINE"].strip()
        if (not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image)
                or "://" in image):
            raise ValueError("Kitesurf container image requires an immutable SHA-256 digest")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Kitesurf container source requires a full Git commit")
        if js_engine not in {"boa", "v8"}:
            raise ValueError("Kitesurf container JavaScript engine must be boa or v8")
        return endpoint, {}, {
            "provider": "self-hosted",
            "browser": "kitesurf",
            "protocol": "cdp",
            "endpoint_contract": "kitesurf-container-devtools-browser/1",
            "deployment_id_sha256": hashlib.sha256(
                values["KITESURF_DEPLOYMENT_ID"].strip().encode()
            ).hexdigest(),
            "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
            "image": image,
            "source_commit": revision,
            "js_engine": js_engine,
        }
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
