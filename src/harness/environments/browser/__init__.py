"""Browser adapters, container launch and CDP readiness shared by browser evaluation tracks.

Each browser engine is one adapter module in this package, registered in
``BROWSERS``. Shared code reads an adapter's declared launch command, container
image, CDP port, capability flags and hosted-CDP hook; it never branches on an
engine name. Adding a browser adds one adapter module and a profile entry.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from harness.adapters import Capabilities, Registry
from harness.environments.webarena.topology import CONTAINER_HOST_ALIAS

# Capability flags a browser adapter may declare. Shared code reads these, never names.
MULTIPLE_PAGES = "multiple_pages"  # opens extra pages in one context
NETWORK_RESET_CDP = "network_reset_cdp"  # clears network state over CDP
STORAGE_CDP = "storage_cdp"  # clears storage over CDP
CONTEXT_REQUEST_COOKIE_SHARING = "context_request_cookie_sharing"  # API requests share cookies
NATIVE_LOGIN_FORM_SUBMIT = "native_login_form_submit"  # login forms submit natively
PLAYWRIGHT_HAR_CAPTURE = "playwright_har_capture"  # HAR comes from Playwright events, not CDP
HOSTED_DEFAULT_CONTEXT = "hosted_default_context"  # drives the one provider-owned context
DOM_LOGIN_READINESS = "dom_login_readiness"  # login markers are polled in the DOM
DOCUMENT_OBSERVER = "document_observer"  # document text is observed under a process timer
LAYOUT_PUBLICATION = "layout_publication"  # geometry needs an explicit CDP layout publication
VIEWPORT_OBSERVER = "viewport_observer"  # viewport is observed through its own CDP probe
HOST_RESOLVER_RULES = "host_resolver_rules"  # honours deployment host overrides on launch
REQUEST_BRIDGE = "request_bridge"  # reaches deployment hosts through a local request bridge
# The reference browser: the default replay engine and the only qualification browser.
REFERENCE = "reference"
# The complete CDP surface CowAgent's BrowserTool drives in a frozen remote cohort.
FULL_CDP = (MULTIPLE_PAGES, NETWORK_RESET_CDP, STORAGE_CDP)

# Launch one local browser: (binary, CDP port, profile directory, host resolver rules).
LocalCommand = Callable[[str, int, str, "str | None"], list[str]]
# Hosted CDP: credential values -> (endpoint, headers, credential-free public binding).
HostedCdp = Callable[[dict[str, str]], "tuple[str, dict[str, str], dict[str, object]]"]


@dataclass(frozen=True)
class BrowserAdapter:
    """One browser engine: identity, launch, container and declared capabilities."""

    name: str
    label: str
    flags: frozenset[str] = frozenset()
    # Container measurement: image tag, an environment variable that overrides it,
    # and the CDP port inside the container.
    image: str | None = None
    image_env: str | None = None
    cdp_port: int | None = None
    # Command that prints the version inside the container image.
    version_command: tuple[str, ...] | None = None
    local_command: LocalCommand | None = None
    hosted_cdp: HostedCdp | None = None
    values: dict[str, Any] = field(default_factory=dict)

    def capabilities(self) -> Capabilities:
        return Capabilities(self.flags, dict(self.values))

    def has(self, *flags: str) -> bool:
        return set(flags) <= self.flags

    def configured_image(self) -> str | None:
        if self.image is None:
            return None
        return os.environ.get(self.image_env, self.image) if self.image_env else self.image

    def implementation_identity(self) -> dict[str, Any]:
        return {"engine": self.name, "image": self.configured_image()}

    def validate(self) -> list[str]:
        if self.local_command is None and self.hosted_cdp is None and self.image is None:
            return [f"{self.name}: no launch command, container image or hosted endpoint"]
        return []


BROWSERS: Registry[BrowserAdapter] = Registry("browser engine")


def register_browser(adapter: BrowserAdapter) -> BrowserAdapter:
    return BROWSERS.register(adapter.name, adapter)


def browser_adapter(engine: str) -> BrowserAdapter:
    return BROWSERS.get(engine)


def engines_with(*flags: str) -> tuple[str, ...]:
    """Registered engines that declare every flag, in registry order."""
    return tuple(name for name, adapter in BROWSERS.items() if adapter.has(*flags))


def reference_engine() -> str:
    """The one registered engine that declares ``REFERENCE``."""
    engines = engines_with(REFERENCE)
    if len(engines) != 1:
        raise RuntimeError("exactly one browser adapter must declare the reference capability")
    return engines[0]


def container_engines() -> tuple[str, ...]:
    """Engines with a container image; the default measured set, in registry order."""
    return tuple(name for name, adapter in BROWSERS.items() if adapter.image is not None)


def local_engines() -> tuple[str, ...]:
    """Engines started from a local binary."""
    return tuple(name for name, adapter in BROWSERS.items() if adapter.local_command is not None)


def configured_browser_images() -> dict[str, str]:
    return {name: str(BROWSERS.get(name).configured_image()) for name in container_engines()}


def engine_cdp_port(engine: str) -> int:
    port = BROWSERS.get(engine).cdp_port
    if port is None:
        raise ValueError(f"{engine} has no container CDP port")
    return port


def engine_label(engine: str) -> str:
    """Display name of a registered engine; unregistered IDs keep a readable form."""
    if engine in BROWSERS:
        return BROWSERS.get(engine).label
    return engine.replace("_", " ").strip().title()


def rotated_engine_order(engines: tuple[str, ...], task_index: int) -> tuple[str, ...]:
    """Rotate call order by task so every registered browser takes every position."""
    offset = task_index % len(engines)
    return engines[offset:] + engines[:offset]


def external_websocket_url(endpoint: str, advertised: str) -> str:
    endpoint_parts = urllib.parse.urlsplit(endpoint)
    websocket_parts = urllib.parse.urlsplit(advertised)
    scheme = "wss" if endpoint_parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit(
        (scheme, endpoint_parts.netloc, websocket_parts.path, websocket_parts.query, "")
    )


def wait_cdp(url: str, timeout_seconds: float = 60, *, container: Any = None) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/json/version", timeout=2) as response:
                if response.status == 200:
                    payload = json.load(response)
                    advertised = str(payload.get("webSocketDebuggerUrl") or "")
                    if advertised:
                        return external_websocket_url(url, advertised)
                    raise RuntimeError("CDP version response omitted webSocketDebuggerUrl")
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    logs = ""
    if container is not None:
        try:
            container.reload()
            logs = container.logs(tail=30).decode("utf-8", errors="replace")
        except Exception:
            pass
    detail = f"; container logs:\n{logs}" if logs else ""
    raise RuntimeError(f"CDP endpoint did not become ready: {last_error}{detail}")


def published_endpoint(container: Any, *, engine: str, cdp_port: int) -> str:
    ports = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = ports.get(f"{cdp_port}/tcp")
    if not bindings or not bindings[0].get("HostPort"):
        raise RuntimeError(f"{engine}: Docker did not publish CDP port")
    return f"http://127.0.0.1:{bindings[0]['HostPort']}"


def start_browser_container(
    client: Any,
    *,
    engine: str,
    image: str,
    cdp_port: int,
    run_id: str,
    cpus: float | None,
    memory: str,
    network: str,
) -> Any:
    name = f"lex-webstone-{run_id}-{engine}"
    options: dict[str, Any] = {
        "name": name,
        "detach": True,
        "ports": {f"{cdp_port}/tcp": ("127.0.0.1", None)},
        "labels": {
            "lex.webstone.run": run_id,
            "lex.webstone.engine": engine,
        },
        "mem_limit": memory,
        "pids_limit": 512,
        "shm_size": "512m",
        "security_opt": ["no-new-privileges"],
        "network": network,
        "extra_hosts": {CONTAINER_HOST_ALIAS: "host-gateway"},
    }
    if cpus is not None:
        if cpus <= 0:
            raise ValueError("cpus must be positive when a CPU limit is requested")
        options["nano_cpus"] = int(cpus * 1_000_000_000)
    return client.containers.run(image, **options)


def validate_resource_controls(info: dict[str, Any]) -> None:
    required = {
        "CpuCfsQuota": "CPU CFS quota",
        "CpuCfsPeriod": "CPU CFS period",
        "MemoryLimit": "memory limit",
        "PidsLimit": "PID limit",
    }
    unsupported = [label for key, label in required.items() if info.get(key) is False]
    if unsupported:
        raise RuntimeError(
            "Docker daemon cannot enforce measured resource controls: "
            + ", ".join(unsupported)
            + ". Fix cgroup delegation before qualification or formal measurement."
        )


def cleanup_browser_runtime(
    browsers: dict[str, Any],
    playwright: Any,
    containers: dict[str, Any],
    networks: dict[str, Any],
) -> None:
    for browser in browsers.values():
        try:
            browser.close()
        except Exception:
            pass
    try:
        playwright.stop()
    except Exception:
        pass
    for container in containers.values():
        try:
            container.remove(force=True)
        except Exception:
            pass
    for network in networks.values():
        try:
            network.remove()
        except Exception:
            pass


# Adapters register themselves on import; each module names only its own engine.
from harness.environments.browser import (  # noqa: E402
    chromium,  # noqa: F401
    kitesurf,  # noqa: F401
    lightpanda,  # noqa: F401
    moli,  # noqa: F401
    obscura,  # noqa: F401
)
