"""The Browser Use track template: the base-template roles its report fills, and with which routes.

The page order is the navigation order. Per-task, per-result and per-browser
detail routes belong to the role of the page that links them. The report has no
separate interpretation page: its home page carries the generated reading.
"""

from harness.reporting.templates import Page, TrackTemplate, Variant

TEMPLATE = TrackTemplate(track="browser-use", version=1, variants=(
    Variant(name="standard", version=1, absent=frozenset({"interpretation"}), pages=tuple(
        Page(key=key, role=role, route=route, title=title)
        for key, role, route, title in (
            ("home", "home", "index.html", "Browser Use"),
            ("datasets", "datasets", "datasets/index.html", "Dataset"),
            ("methodology", "methodology", "methodology/index.html", "Methodology"),
            ("analysis", "results", "analysis/index.html", "Results analysis"),
            ("review", "review", "review/index.html", "Review"),
            ("fixes", "review", "fixes/index.html", "Fix log"),
            ("adoption", "methodology", "adoption/index.html", "Browsers tested"),
            ("reproduce", "reproduce", "reproduce/index.html", "Environment and reproduction"),
            ("changes", "changes", "changes/index.html", "Changes"),
            ("accounts", "methodology", "accounts/index.html", "Accounts and setup"),
        )
    )),
))
VARIANT = "standard"
