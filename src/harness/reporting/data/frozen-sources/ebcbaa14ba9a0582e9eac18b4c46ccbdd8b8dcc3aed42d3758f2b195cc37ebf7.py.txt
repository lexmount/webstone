"""The Web Search track template: which base-template roles each report variant fills, and with which routes.

Authored content selects the variant (``presentation``); every other choice in
the page set and section order comes from the shared base template.
"""

from harness.reporting.templates import Page, TrackTemplate, Variant
from harness.web_search.messages import zh_text

_SHARED_TAIL = (
    ("review", "review", "review.html", "Result audit"),
    ("reproduce", "reproduce", "reproduce.html", "Reproduce"),
    ("changes", "changes", "changes.html", "Changes"),
)


def _pages(rows, footer=()):
    return tuple(Page(key=key, role=role, route=route, title=title, nav=key not in footer)
                 for key, role, route, title in rows)


TEMPLATE = TrackTemplate(track="web-search", version=1, variants=(
    Variant(name="standard", version=1, pages=_pages((
        ("home", "home", "index.html", "Overview"),
        ("dataset", "datasets", "dataset.html", "Queries"),
        ("results", "results", "results.html", "Results"),
        ("interpretation", "interpretation", "interpretation.html", "Findings"),
        ("performance", "interpretation", "performance.html", "Latency diagnostics"),
        ("method", "methodology", "research.html", "Method & market"),
        *_SHARED_TAIL,
    ))),
    Variant(name="comparison", version=1, pages=_pages((
        ("home", "home", "index.html", "Evaluation overview"),
        ("capabilities", "interpretation", "capabilities.html", "API outputs"),
        ("quality", "interpretation", "quality.html", "Retrieval quality"),
        ("performance", "interpretation", "performance.html", "Speed and cost"),
        ("dataset", "datasets", "dataset.html", "Tests and sources"),
        ("results", "results", "results.html", "Query comparison"),
        ("method", "methodology", "research.html", "Method & FAQ"),
        *_SHARED_TAIL,
    ), footer=("method", "review", "reproduce", "changes"))),
))


# Points where a registered benchmark's ``views()`` add sections; each names its call arguments.
HOOK_POINTS = {
    "evidence-panel": "(profile, selected, zh, *, compact=False): one benchmark's scored-evidence panel",
    "level-findings": "(view, panel, level, zh): extra figures for one benchmark at one output level",
}


def variant_name(authored):
    """Authored content selects the comparison variant; every other report is standard."""
    return "comparison" if (authored or {}).get("presentation") == "comparison-report" else "standard"


def reader_pages(authored):
    """The selected variant's pages with their labels in each generated language."""
    return [{"key": page.key, "role": page.role, "route": page.route, "nav": page.nav, "en": page.title,
             "zh": zh_text(page.title, context="navigation")}
            for page in TEMPLATE.variant(variant_name(authored)).pages]
