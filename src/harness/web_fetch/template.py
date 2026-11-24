"""The Web Fetch track template: which base-template roles each report variant fills, and with which routes.

Build variants (``report_variants``) name the template variant they render.
Every page writer marks its route's role from these tables, and the release
check compares a built report with the variant its manifest records.
"""

from pathlib import Path

from harness.reporting.release_check import language_editions, read_page
from harness.reporting.templates import Page, TrackTemplate, Variant, check_page, recorded_variant, resolve_template


def _pages(rows, footer=()):
    return tuple(Page(key=key, role=role, route=route, title=title, nav=key not in footer)
                 for key, role, route, title in rows)


# The page set every evidence-directory build writes (certified, audits, diagnostics, saved matrices).
STANDARD = (
    ("home", "home", "index.html", "Overview"),
    ("datasets", "datasets", "datasets/index.html", "Dataset"),
    ("methodology", "methodology", "methodology/index.html", "Methodology"),
    ("analysis", "results", "analysis/index.html", "Analysis"),
    ("review", "review", "review/index.html", "Review record"),
    ("guide", "interpretation", "guide/index.html", "Interpretation"),
    ("adoption", "methodology", "adoption/index.html", "Methods"),
    ("reproduce", "reproduce", "reproduce/index.html", "Reproduction"),
    ("changes", "changes", "changes/index.html", "Changelog"),
    ("accounts", "methodology", "accounts/index.html", "Access & setup"),
)
# A complete-cohort report with case and evidence explorers and no review page.
COMPLETE_COHORT = (
    ("home", "home", "index.html", "Overview"),
    ("dataset", "datasets", "dataset/index.html", "Dataset"),
    ("analysis", "results", "analysis/index.html", "Analysis"),
    ("results", "results", "results/index.html", "Results"),
    ("cases", "results", "cases/index.html", "Case results"),
    ("viewer", "results", "viewer/index.html", "Evidence viewer"),
    ("guide", "interpretation", "guide/index.html", "Interpretation"),
    ("methodology", "methodology", "methodology/index.html", "Method"),
    ("reproduce", "reproduce", "reproduce/index.html", "Reproduction"),
    ("history", "changes", "history/index.html", "Changes"),
)
_SCORE_AUDIT = (("audit", "review", "audit/index.html", "Score audit"),)
_EXPLORERS = ("results", "cases", "viewer")

TEMPLATE = TrackTemplate(track="webfetch", version=1, variants=(
    *(Variant(name=name, version=1, pages=_pages(STANDARD)) for name in (
        "certified", "webmainbench-sample", "wcxb", "local-audit", "diagnostic-cohorts",
        "public-benchmark-research", "saved-matrix")),
    Variant(name="published-snapshot-audit", version=1,
            pages=_pages(STANDARD + _SCORE_AUDIT, footer=("audit",))),
    Variant(name="webmainbench-full", version=1, pages=_pages(COMPLETE_COHORT, footer=_EXPLORERS),
            absent=frozenset({"review"})),
))

def page_role(route, *, complete_cohort=False):
    """The role a route fills in its page set, or ``None`` for a detail route."""
    rows = COMPLETE_COHORT if complete_cohort else STANDARD + _SCORE_AUDIT
    route = route[3:] if route.startswith("en/") else route
    return next((role for _, role, path, _ in rows if path == route), None)


def role_attribute(route, **options):
    role = page_role(route, **options)
    return f' data-page-role="{role}"' if role else ""


def report_template(variant):
    """The template identity a report manifest records: owner, base, track and variant versions."""
    return {**resolve_template("webfetch"), **TEMPLATE.record(variant)}


def check_template_pages(root: Path, manifest, *, authority_root=None):
    """A built report matches its recorded variant: identity, page roles and slot order."""
    record = manifest.get("template")
    if not isinstance(record, dict) or "variant" not in record:
        raise ValueError("Report manifest differs from the maintained template: no recorded variant; rebuild it")
    variant = recorded_variant(record, TEMPLATE)
    expected = {**(resolve_template("webfetch", root=authority_root) if authority_root else
                   resolve_template("webfetch")), **TEMPLATE.record(variant.name)}
    if manifest["template"] != expected:
        raise ValueError("Report manifest differs from the maintained template")
    for _, prefix in language_editions(root):
        for page in variant.pages:
            check_page(read_page(root / prefix / page.route), page)
    return variant
