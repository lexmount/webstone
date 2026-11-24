from __future__ import annotations

import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from functools import partial
from importlib import import_module
from typing import Any

from lexbench_webfetch.models import EvalCase

from harness.adapters import Capabilities, Registry
from harness.call_diagnostics import emit
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.network_dns import public_dns_context, validate_dns_mode
from harness.web_fetch.native_routes import (
    encode_http_target_url,
    normalized_http_parameters,
    url_input_slot,
)
from harness.http_client import HttpResponse, inner_timeout_budget
from harness.web_fetch.retrieval_hints import NATIVE_RETRIEVAL_HINTS


def terminate_owned_process_tree(process):
    """Stop an owned POSIX session, including detached descendant groups.

    If the process tree cannot be observed, leave the worker alive so the
    caller can quarantine unresolved work instead of claiming it stopped.
    """
    if process is None or process.poll() is not None:
        return True
    if os.name != 'posix':
        return False
    try:
        observed = subprocess.run(
            ['ps', '-axo', 'pid=,ppid=,pgid='], capture_output=True,
            text=True, check=True, timeout=.5,
        )
        rows = [tuple(map(int, line.split())) for line in observed.stdout.splitlines()]
        if any(len(row) != 3 for row in rows):
            return False
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    parents = {pid: parent for pid, parent, _ in rows}
    groups = {pid: group for pid, _, group in rows}
    if groups.get(process.pid) != process.pid:
        return process.poll() is not None
    owned = {process.pid}
    while True:
        descendants = {pid for pid, parent in parents.items() if parent in owned}
        expanded = owned | descendants
        if expanded == owned:
            break
        owned = expanded
    owned_groups = {groups[pid] for pid in owned}
    if os.getpgrp() in owned_groups:
        return False
    # Descendants may call setsid (Playwright does this for Chromium). Stop
    # their groups first, then the original worker, preserving its diagnostics.
    for group in sorted(owned_groups, key=lambda value: value == process.pid):
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
    # SIGKILL delivery is asynchronous. Confirm the owned groups are quiescent
    # before releasing the next call; zombies cannot perform physical work.
    deadline = time.monotonic() + .5
    while True:
        try:
            observed = subprocess.run(
                ['ps', '-axo', 'pgid=,stat='], capture_output=True,
                text=True, check=True, timeout=.5,
            )
            survivors = [parts for line in observed.stdout.splitlines()
                         if len(parts := line.split()) == 2
                         and int(parts[0]) in owned_groups
                         and not parts[1].startswith('Z')]
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        if not survivors:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)


# A killed worker exits promptly; one that outlives this grace was not stopped.
PROCESS_EXIT_GRACE_SECONDS = 1.0


class ProcessCleanupError(RuntimeError):
    """An owned worker survived a best-effort kill and may still do work."""


def await_owned_process_exit(process, grace=PROCESS_EXIT_GRACE_SECONDS):
    """Reap a worker after ``terminate_owned_process_tree`` without hanging.

    The kill is best effort: when the process tree cannot be observed the
    worker is left running. Bound the reap and raise so the runner
    quarantines the provider instead of blocking the call forever.
    """
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        raise ProcessCleanupError(
            f'Worker process {process.pid} is still running after a best-effort kill'
        ) from None


class Provider(ABC):
    """One Web Fetch method; conforms to ``harness.adapters.MethodAdapter``.

    Shared code calls only the hooks declared here and reads declared
    capability flags instead of branching on provider types.
    """

    allow_failure_retries = True

    # Capability flags read by shared evidence checks:
    #   malformed_envelope_is_evidence  a non-object JSON response selects no answer
    #   raw_answer_bytes                answers come from response bytes, not a JSON envelope
    #   raw_stdout_evidence             the retained raw response is the worker's stdout
    #   raw_http_evidence               byte answers are the retained HTTP response, not stdout
    #   legacy_markdown_output_label    older evidence named the route "markdown";
    #                                   replay its frozen request interface instead
    #   local_network                   calls use this host's network, so a comparison
    #                                   needs one declared public DNS environment
    #   fixed_native_build_comparison   two pinned builds may form a fixed-native
    #                                   comparison cohort
    #   http_402_is_account_capacity    HTTP 402 means the account ran out of capacity,
    #                                   including evidence saved before that error type
    #   ignores_minimum_text_fragments  the call never sends completion text fragments
    capability_flags: frozenset[str] = frozenset()

    def normalize_native_http_body(self, body: dict[str, Any], case) -> bool:
        """Replace this method's task input and per-call tokens with placeholders.

        Returns False when the request does not carry the frozen task URL where
        the method declares it. The default reads a ``url`` or ``urls`` field.
        """
        return url_input_slot(body, case)

    @classmethod
    def launches_local_browser(cls, config: dict[str, Any]) -> bool:
        """Whether this configuration starts a browser on this host."""
        return False

    # Per-call diagnostics sink assigned by the runner; None when not debugging.
    call_diagnostics = None

    @classmethod
    def capabilities(cls) -> Capabilities:
        return Capabilities(flags=cls.capability_flags)

    def implementation_identity(self, *, refresh: bool = False) -> dict[str, Any]:
        """Identity established by ``validate``; adapters that compute it override.

        Empty when the adapter records no implementation identity.
        """
        if refresh or not isinstance(getattr(self, '_implementation_identity', None), dict):
            if errors := self.validate():
                raise ValueError(f'{self.name} identity/configuration error: ' + ', '.join(errors))
        return dict(getattr(self, '_implementation_identity', None) or {})

    def recorded_implementation_identity(self) -> dict[str, Any] | None:
        """Identity already established outside timed calls, never recomputed here."""
        identity = getattr(self, '_implementation_identity', None)
        return identity if isinstance(identity, dict) else None

    def capacity_credits_for_cases(self, cases, *, runs: int, max_failure_retries: int) -> int | None:
        """Account credits an exact case scope may consume; None without a case-derived bound."""
        return None

    def preflight_capacity(self, declared_calls: int, timeout: float = 10, *,
                           required_credits: int | None = None) -> None:
        """Fail before a cohort whose declared calls exceed account capacity."""

    @staticmethod
    def native_answer_selection(selection):
        """Pre-call answer selection policy; see ``native_projection.NativeSelection``.

        Its defining module's bytes are part of the frozen policy identity.
        """
        raise ValueError('Unsupported native answer selection policy: ' + str(selection.provider_type))

    # Public configuration diagnostics only. Credentials, commands and local
    # paths remain covered by the full configuration hash, never displayed.
    # Adapters may extend this declaration for their own safe options.
    native_configuration_fields = (
        'max_bytes', 'max_age', 'max_age_hours', 'only_main_content', 'include_images',
        'extract_depth', 'text_verbosity', 'include_resource_links', 'rich_links_limit',
        'wait_until', 'block_private_networks', 'dns_mode', 'log_level', 'formats',
    )

    def native_configured_parameters(self):
        """Return the adapter's explicitly public configuration diagnostics."""
        return {key: deepcopy(self.config[key])
                for key in self.native_configuration_fields if key in self.config}

    def native_route(self, case):
        """Declare native transport, interface and candidate answer fields."""
        raise ValueError('Unsupported fixed-native interface: ' + self.type)

    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.type = str(config.get("type", name))
        self._native_mode = bool(config.get('uniform_native_parameters'))
        self.http_timeout_budget = partial(inner_timeout_budget, native_default=self._native_mode)
        self.request_arguments = (self._native_request_arguments if self._native_mode
                                  else self.diagnostic_http_arguments)

    def check_native_call(self, case: EvalCase) -> None:
        """Permit native task outputs, never case-specific retrieval assistance."""
        if bool(self.config.get('uniform_native_parameters')) != self._native_mode:
            raise ValueError('Native execution mode changed after provider initialization')
        if not self._native_mode:
            return
        if case.retrieval_hints and not (case.track == "dynamic" and case.retrieval_hints == NATIVE_RETRIEVAL_HINTS):
            raise ValueError("fixed native calls prohibit per-case retrieval hints")
        if case.track not in {"web", "dynamic", "challenge", "risk_control", "articles", "document", "image", "video", "file"}:
            raise ValueError("fixed native calls require a declared task route")
        if case.track == "document" and case.task_kind == "resource":
            raise ValueError("fixed native calls require a declared task route")
        if case.task_kind not in {"body", "structure", "resource", "file"}:
            raise ValueError("fixed native calls require a declared native output kind")

    def capture_native_request(self, case, *, transport, parameters, endpoint=None, interface=None, native_route_ledger=None):
        ledger = native_route_ledger
        if ledger is not None:
            ledger.observe(self, case, transport=transport, parameters=parameters,
                           endpoint=endpoint, interface=interface)

    def native_http_request(self, case, callback, endpoint, *, native_route_ledger=None, **kwargs):
        """Check and observe the actual call, even without an attached ledger.

        Adapters share ``native_http_arguments`` for construction. Rebuilding
        here is intentional: callers can still alter its mutable arguments or
        call this boundary directly. The ledger separately binds the request
        to the contract frozen before the cohort.
        """
        ledger = native_route_ledger
        if bool(self.config.get('uniform_native_parameters')) != self._native_mode:
            raise ValueError('Native execution mode changed after provider initialization')
        if self._native_mode:
            native_endpoint, native_arguments = self.native_http_arguments(case, kwargs.get('timeout'))
            if endpoint != native_endpoint or kwargs != native_arguments:
                if ledger is not None:
                    ledger.reject(self, case, 'Native adapter arguments differ from its canonical request builder')
                raise ValueError('Native adapter arguments differ from its canonical request builder')
            endpoint, kwargs = native_endpoint, native_arguments
        mode = self.config.get('dns_mode', 'system')
        with public_dns_context(mode, kwargs.get('timeout', 30)) as traces:
            if ledger is not None:
                self.capture_native_request(case, transport='http', endpoint=endpoint, native_route_ledger=ledger,
                                            parameters=normalized_http_parameters(self, case, kwargs))
            try:
                response = callback(endpoint, **kwargs)
            finally:
                emit('http_dns_resolution', dns_mode=mode, dns_trace=traces)
        if isinstance(response, HttpResponse):
            response.dns_metadata = {'dns_mode': mode, 'dns_trace': traces}
        return response

    def native_http_arguments(self, case, timeout):
        raise ValueError('Provider has no fixed-native HTTP request builder')

    def _native_request_arguments(self, case, timeout):
        # Retain one canonical builder for both freeze and transport checks.
        return self.native_http_arguments(case, timeout)

    def diagnostic_http_arguments(self, case, timeout):
        return self.native_http_arguments(case, timeout)

    def native_http_options(self, body, headers, timeout):
        body = deepcopy(body)
        if 'url' in body:
            body['url'] = encode_http_target_url(body['url'])
        if 'urls' in body:
            body['urls'] = [encode_http_target_url(url) for url in body['urls']]
        return {'method': 'POST', 'json_body': body, 'headers': headers,
                'timeout': timeout, 'max_bytes': int(self.config.get('max_bytes', 20_000_000))}

    @property
    def min_interval_seconds(self) -> float:
        """Minimum cooldown from one response completion to the next request start."""
        return max(0.0, float(self.config.get("min_interval_seconds", 0)))

    @abstractmethod
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        raise NotImplementedError

    def validate(self) -> list[str]:
        try:
            validate_dns_mode(self.config.get('dns_mode', 'system'))
        except ValueError as error:
            return [str(error)]
        return []

    def cancel(self) -> None:
        """Best-effort cancellation; non-cancellable calls are quarantined."""

    @property
    def cancellation_cleanup_seconds(self) -> float:
        """Bounded post-deadline grace for a cancellable call to stop.

        This is cleanup time, not answer latency. Adapters must opt in only
        when ``cancel`` actively stops their underlying work.
        """
        return 0.0

    def begin_call(self) -> None:
        """Reset cancellation state before the timed worker can start."""


# Provider type -> (module, class): the one place a type is declared. A bare module
# name is a module of this package; a dotted name is imported as given, so an
# adapter can live outside it. Modules load on demand because adapters import this
# module and some carry their own optional dependencies.
PROVIDERS: Registry[tuple[str, str]] = Registry("Web Fetch provider type")
for _type, _entry in {
    "brightdata_unlocker": ("hosted_fetch", "BrightDataUnlockerProvider"),
    "browser_use_fetch": ("hosted_fetch", "BrowserUseFetchProvider"),
    "browserbase_fetch": ("hosted_fetch", "BrowserbaseFetchProvider"),
    "deepseek_fetch": ("hosted_fetch", "DeepSeekFetchProvider"),
    "kimi_fetch": ("hosted_fetch", "KimiFetchProvider"),
    "zai_reader": ("hosted_fetch", "ZaiReaderProvider"),
    "claude_web_fetch": ("claude_fetch", "ClaudeFetchProvider"),
    "crawl4ai_cloud": ("hosted_fetch", "Crawl4aiCloudProvider"),
    "headless_html": ("headless", "HeadlessProvider"),
    "headless_markdown": ("headless", "HeadlessProvider"),
    "moli_webfetch": ("moli", "MoliWebFetchProvider"),
    "http": ("http", "HttpProvider"),
    "cowagent_builtin": ("cowagent", "CowAgentBuiltinProvider"),
    "lexmount": ("lexmount", "LexmountProvider"),
    "lexmount_cn": ("lexmount", "LexmountProvider"),
    "firecrawl": ("firecrawl", "FirecrawlProvider"),
    "tavily_extract": ("tavily", "TavilyExtractProvider"),
    "exa_contents": ("exa", "ExaContentsProvider"),
    "cloudflare_markdown": ("cloudflare", "CloudflareMarkdownProvider"),
    "cloudflare_content": ("cloudflare", "CloudflareContentProvider"),
    "subprocess_jsonl": ("subprocess_jsonl", "SubprocessJsonlProvider"),
    "fixture": ("fixture", "FixtureProvider"),
}.items():
    PROVIDERS.register(_type, _entry)


def provider_class(provider_type: str) -> type[Provider]:
    """Load the adapter class registered for a provider type."""
    module, class_name = PROVIDERS.get(provider_type)
    qualified = module if "." in module else f"harness.web_fetch.providers.{module}"
    return getattr(import_module(qualified), class_name)


def provider_capabilities(provider_type) -> Capabilities:
    """Declared capabilities of a saved record's provider type; none when unregistered."""
    if not isinstance(provider_type, str) or provider_type not in PROVIDERS:
        return Capabilities()
    return provider_class(provider_type).capabilities()


# Retired local adapters whose saved evidence still declares a guarded local network.
RETIRED_LOCAL_NETWORK_TYPES = frozenset(
    {"openclaw_builtin", "deepseek_harness_builtin", "langchain_webbase", "crawl4ai"})


def native_local_network(provider_type) -> bool:
    """Whether a provider type's native calls use this host's network."""
    return (provider_type in RETIRED_LOCAL_NETWORK_TYPES
            or provider_capabilities(provider_type).has("local_network"))


def guards_local_network(provider_type, config: dict[str, Any]) -> bool:
    """Whether a configured method needs the declared public DNS preflight."""
    if native_local_network(provider_type):
        return True
    return provider_type in PROVIDERS and provider_class(provider_type).launches_local_browser(config)


def build_provider(name: str, config: dict[str, Any]) -> Provider:
    provider_type = config.get("type")
    if not isinstance(provider_type, str) or provider_type not in PROVIDERS:
        raise ValueError(f"unknown provider type: {provider_type!r}")
    return provider_class(provider_type)(name, config)
