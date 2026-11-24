"""WebArena site adapters: label, stateful flag, login and reset behaviour per site.

Every site-specific step of the WebArena environments lives here, so the replay
tracks and Browser Use read declared site behaviour instead of branching on a
site name. Hosts, ports and labels come from ``topology.json``; this module adds
the behaviour that data cannot express. Adding a site adds one topology entry and
one adapter below.

Two login protocols exist and both stay per site: ``form_login`` signs in through
the visible form of a plain Playwright page (the public human-recording replay),
and ``session_login`` prepares an authenticated BrowserTool session with the
official auto-login headers (the catalog replay and Browser Use).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness.adapters import Registry
from harness.environments.webarena.topology import TOPOLOGY, site_credentials

# How a freshly started stateful container receives its external URL.
ENTRYPOINT_INIT = "entrypoint"  # the image entrypoint runs the one env-ctrl init
EXPLICIT_INIT = "explicit"  # the harness runs the official init with the external URL


@dataclass(frozen=True)
class SessionLogin:
    """One site's login step inside an authenticated BrowserTool session."""

    service: Any
    context: Any
    page: Any
    base_url: str
    credentials: dict[str, str] | None
    publish_layout: Callable[[Any], None]
    wait_for_login_ready: Callable[..., None]


@dataclass(frozen=True)
class SiteAdapter:
    name: str
    # Plain Playwright form login: (page, base URL, username, password).
    form_login: Callable[[Any, str, str, str], None] | None = None
    # Authenticated BrowserTool session login; ``None`` means the site needs none.
    session_login: Callable[[SessionLogin], None] | None = None
    # Official auto-login header, and whether it is set on the whole browser context.
    auth_header: str | None = None
    context_auth_header: bool = False
    # The official evaluator signs in with the auto-login header for this site.
    header_login: bool = False
    url_init: str | None = None
    # The entrypoint's own init attempt ignores the external URL and must finish first.
    entrypoint_init_noop: bool = False
    # The public reset receipt of an explicit init records the container port.
    explicit_init_records_port: bool = False
    # Magento: search dependency readiness and the longer init command budget.
    magento: bool = False
    # The container declares a Docker health check that must turn healthy.
    container_healthcheck: bool = False
    # Official amd64 PHP-FPM image that needs QEMU on an ARM Docker host.
    amd64_php: bool = False
    # A remote cohort reset proves an empty baseline table for this site.
    reset_baseline_check: bool = False
    # The admin is served over HTTPS behind a proxy and loads Magento's script loader.
    admin_https: bool = False
    # Setup first verifies the downloaded data archives named by the delivery contract.
    data_archives: bool = False

    @property
    def topology(self) -> dict[str, Any]:
        return TOPOLOGY["sites"][self.name]

    @property
    def stateful(self) -> bool:
        return bool(self.topology["mutable"])

    def label(self, language: str = "en") -> str:
        return str(self.topology["label"][language])


def _clear_and_reload(page: Any) -> None:
    page.evaluate("localStorage.clear(); sessionStorage.clear()")
    page.reload(wait_until="domcontentloaded")


# Shopping ---------------------------------------------------------------------


def _shopping_form_login(page: Any, base_url: str, username: str, password: str) -> None:
    page.goto(
        base_url + "/customer/account/login/",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    email = page.get_by_label("Email", exact=True)
    if email.count():
        email.fill(username)
        page.get_by_label("Password", exact=True).fill(password)
        with page.expect_navigation(wait_until="commit", timeout=120_000):
            page.get_by_role("button", name="Sign In", exact=True).click(
                no_wait_after=True, timeout=30_000
            )
        # Do not race the submitted form's redirect with the canonical
        # authenticated-route check below.
    page.goto(base_url + "/wishlist/", wait_until="domcontentloaded", timeout=60_000)
    if "/customer/account/login" in page.url:
        raise RuntimeError("WebArena Shopping login failed")


def _shopping_session_login(login: SessionLogin) -> None:
    page = login.page
    page.goto(f"{login.base_url}/customer/account", wait_until="commit")
    _clear_and_reload(page)
    login.publish_layout(page)
    login.wait_for_login_ready(page, ".block-dashboard-info, .box-information")


# Shopping Admin ---------------------------------------------------------------


def _shopping_admin_form_login(page: Any, base_url: str, username: str, password: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    user_field = page.get_by_placeholder("user name")
    if user_field.count():
        user_field.fill(username)
        page.get_by_placeholder("password").fill(password)
        with page.expect_navigation(wait_until="commit", timeout=120_000):
            page.get_by_role("button", name="Sign in", exact=False).click(
                no_wait_after=True, timeout=30_000
            )
        # Magento's admin shell may keep subresources pending after the
        # authenticated redirect.  Re-open the canonical URL instead of
        # tying login success to the old form locator's navigation wait.
        page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    if page.get_by_placeholder("user name").count():
        raise RuntimeError("WebArena Shopping Admin login failed")


def _shopping_admin_session_login(login: SessionLogin) -> None:
    page = login.page
    page.goto(login.base_url, wait_until="commit")
    _clear_and_reload(page)
    login.publish_layout(page)
    login.wait_for_login_ready(page, ".page-title")


# Reddit (Postmill) ------------------------------------------------------------

POSTMILL_AUTH_HEADER = "X-Postmill-Auto-Login"


def _postmill_header() -> dict[str, str]:
    creds = site_credentials("reddit")
    return {POSTMILL_AUTH_HEADER: f"{creds['username']}:{creds['password']}"}


def bootstrap_postmill_session(context: Any, base_url: str) -> None:
    """Authenticate once, without loading subresources or following redirects.

    The image's header authenticator migrates (and destroys) the previous
    session on every authenticated request. A persistent context header makes
    concurrent thumbnail requests race with CSRF form sessions. The context's
    API client shares the browser cookie jar but does not execute page content.
    Use the configured, host-accessible origin, not a browser-only DNS alias.
    """
    response = context.request.get(
        base_url,
        headers=_postmill_header(),
        max_redirects=0,
        max_retries=0,
        timeout=90_000,
    )
    try:
        if not 200 <= response.status < 300:
            raise RuntimeError(
                f"Postmill session bootstrap requires a direct 2xx response; got {response.status}"
            )
        if not any(
            cookie.get("name") == "PHPSESSID" and cookie.get("value")
            for cookie in context.cookies(base_url)
        ):
            raise RuntimeError("Postmill session bootstrap did not establish a session cookie")
    finally:
        response.dispose()


def bootstrap_postmill_browser_session(context: Any, page: Any, base_url: str) -> None:
    """Establish Postmill auth through the browser when API cookies are not shared."""

    page.set_extra_http_headers(_postmill_header())
    try:
        response = page.goto(base_url, wait_until="commit", timeout=90_000)
    finally:
        page.set_extra_http_headers({})
    if (
        response is None
        or not 200 <= response.status < 300
        or response.request.redirected_from is not None
    ):
        status = response.status if response is not None else "no response"
        raise RuntimeError(
            f"Postmill browser bootstrap requires a direct 2xx response; got {status}"
        )
    if not any(
        cookie.get("name") == "PHPSESSID" and cookie.get("value")
        for cookie in context.cookies(base_url)
    ):
        raise RuntimeError("Postmill browser bootstrap did not establish a session cookie")


def _reddit_form_login(page: Any, base_url: str, username: str, password: str) -> None:
    page.goto(base_url + "/login", wait_until="domcontentloaded", timeout=60_000)
    user_field = page.get_by_label("Username")
    if user_field.count():
        user_field.fill(username)
        page.get_by_label("Password").fill(password)
        with page.expect_navigation(wait_until="commit", timeout=120_000):
            page.get_by_role("button", name="Log in", exact=True).click(
                no_wait_after=True, timeout=30_000
            )
        # Supersede the completed form redirect with a deterministic
        # authenticated route instead of reading from the stale form.
    post_login_url = page.url
    page.goto(
        f"{base_url}/user/{username}/account",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    if "/login" in page.url:
        try:
            messages = page.locator(
                "[role='alert'], .alert, .form-error, .invalid-feedback"
            ).all_inner_texts()
        except Exception as exc:
            messages = [f"diagnostic capture failed: {type(exc).__name__}"]
        raise RuntimeError(
            "WebArena Reddit login failed; "
            f"post_login_url={post_login_url!r}; messages={messages[:3]!r}"
        )


def _reddit_session_login(login: SessionLogin) -> None:
    page = login.page
    if getattr(login.service, "_supports_context_request_cookie_sharing", True):
        bootstrap_postmill_session(login.context, login.base_url)
    else:
        bootstrap_postmill_browser_session(login.context, page, login.base_url)
    page.goto(login.base_url, wait_until="commit")
    _clear_and_reload(page)
    login.publish_layout(page)
    login.wait_for_login_ready(page, "button", exact_text=login.credentials["username"])


# GitLab -----------------------------------------------------------------------


def _gitlab_form_login(page: Any, base_url: str, username: str, password: str) -> None:
    page.goto(base_url + "/users/sign_in", wait_until="domcontentloaded")
    if page.locator("#user_login").count():
        user_field = page.locator("#user_login")
        user_field.fill(username)
        page.locator("#user_password").fill(password)
        page.get_by_role("button", name="Sign in", exact=False).click(
            no_wait_after=True, timeout=30_000
        )
        user_field.wait_for(state="detached", timeout=120_000)
    if page.locator("#user_login").count():
        raise RuntimeError("WebArena GitLab login failed")


def _gitlab_session_login(login: SessionLogin) -> None:
    page = login.page
    page.goto(f"{login.base_url}/users/sign_in", wait_until="commit")
    _clear_and_reload(page)
    login.publish_layout(page)
    page.get_by_test_id("username-field").fill(login.credentials["username"])
    login.publish_layout(page)
    page.get_by_test_id("password-field").fill(login.credentials["password"])
    login.publish_layout(page)
    sign_in = page.get_by_test_id("sign-in-button")
    if getattr(login.service, "_native_login_form_submit", False):
        login.service._submit_login_form(page, sign_in)
    else:
        sign_in.click()
    page.wait_for_url("**/", timeout=90_000)


def _no_session_login(login: SessionLogin) -> None:
    """Read-only sites need no authenticated session."""


_ADAPTERS = (
    SiteAdapter(
        "shopping_admin",
        form_login=_shopping_admin_form_login,
        session_login=_shopping_admin_session_login,
        auth_header="X-M2-Admin-Auto-Login",
        context_auth_header=True,
        header_login=True,
        url_init=ENTRYPOINT_INIT,
        magento=True,
        amd64_php=True,
        admin_https=True,
    ),
    SiteAdapter(
        "shopping",
        form_login=_shopping_form_login,
        session_login=_shopping_session_login,
        auth_header="X-M2-Customer-Auto-Login",
        context_auth_header=True,
        url_init=ENTRYPOINT_INIT,
        magento=True,
        amd64_php=True,
    ),
    SiteAdapter(
        "reddit",
        form_login=_reddit_form_login,
        session_login=_reddit_session_login,
        auth_header=POSTMILL_AUTH_HEADER,
        url_init=EXPLICIT_INIT,
        entrypoint_init_noop=True,
        amd64_php=True,
        reset_baseline_check=True,
    ),
    SiteAdapter(
        "gitlab",
        form_login=_gitlab_form_login,
        session_login=_gitlab_session_login,
        url_init=EXPLICIT_INIT,
        explicit_init_records_port=True,
        container_healthcheck=True,
        reset_baseline_check=True,
    ),
    SiteAdapter("map", session_login=_no_session_login, data_archives=True),
    SiteAdapter("wikipedia", session_login=_no_session_login),
)

WEBARENA_SITES: Registry[SiteAdapter] = Registry("WebArena site")
for _adapter in _ADAPTERS:
    WEBARENA_SITES.register(_adapter.name, _adapter)
if {adapter.name for adapter in _ADAPTERS} != set(TOPOLOGY["sites"]):
    raise RuntimeError("every WebArena topology site needs exactly one site adapter")

# Canonical site order used by deployment receipts and image locks.
SITE_NAMES: tuple[str, ...] = tuple(adapter.name for adapter in _ADAPTERS)
STATEFUL_SITES: tuple[str, ...] = tuple(adapter.name for adapter in _ADAPTERS if adapter.stateful)
SHARED_SITES: tuple[str, ...] = tuple(
    adapter.name for adapter in _ADAPTERS if not adapter.stateful
)


def site_adapter(site: Any) -> SiteAdapter:
    """Return the adapter for a site name or ``WebArenaSite`` member."""
    return WEBARENA_SITES.get(str(getattr(site, "value", site)))


def site_declares(site: Any, field: str) -> bool:
    """Whether a site's adapter sets ``field``; sites without an adapter declare nothing."""
    name = str(getattr(site, "value", site))
    return name in WEBARENA_SITES and bool(getattr(WEBARENA_SITES.get(name), field))


def sites_with(**declared: Any) -> tuple[str, ...]:
    """Site names whose adapter fields equal ``declared``, in canonical order."""
    return tuple(
        adapter.name
        for adapter in _ADAPTERS
        if all(getattr(adapter, key) == value for key, value in declared.items())
    )


def site_labels(language: str = "en") -> dict[str, str]:
    """Display labels in topology order."""
    return {name: WEBARENA_SITES.get(name).label(language) for name in TOPOLOGY["sites"]}
