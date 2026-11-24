"""The Browser Trajectory Replay track template: the base-template roles each replay report fills.

Each replay benchmark's report is a registered variant: ``catalog`` is the
WebArena-Verified catalog replay, ``catalog-acceptance`` its full-catalog
acceptance package, ``curated`` the public-web suite and ``public`` the public
WebArena recording replay with its own profile, target and denominator. The
page order is the navigation order; task and result detail routes belong to the
role of the page that links them. ``languages`` lists the editions a variant
renders; a Chinese-only variant states that limitation on every page.
"""

from harness.reporting.templates import PAGE_ROLES, Page, TrackTemplate, Variant

# The curated and public reports keep their established flat file names.
CURATED_STEM = "trajectory-replay-evaluation-latest"
PUBLIC_STEM = "webarena-trajectory-replay-latest"


def _variant(name, rows, **options):
    """``rows`` are (key, role, route, title[, nav]) tuples in navigation order."""
    pages = tuple(Page(key, role, route, title, *nav) for key, role, route, title, *nav in rows)
    return Variant(name=name, version=1, pages=pages,
                   absent=frozenset(PAGE_ROLES) - {page.role for page in pages}, options=options)


def _flat(stem, rows):
    return tuple((key, role, stem + suffix + ".html", title) for key, role, suffix, title in rows)


TEMPLATE = TrackTemplate(track="trajectory-replay", version=1, variants=(
    _variant("catalog", (
        ("index", "home", "index.html", "Summary"),
        ("interpretation", "interpretation", "interpretation.html", "Interpretation"),
        ("dataset", "datasets", "dataset.html", "Dataset"),
        ("results", "results", "results.html", "Results analysis"),
        ("environment", "methodology", "environment.html", "Environment & method"),
        ("review", "review", "review.html", "Review history"),
    ), languages=("zh", "en")),
    _variant("catalog-acceptance", (
        ("index", "home", "index.html", "Results"),
        ("dataset", "datasets", "dataset.html", "Dataset"),
        ("method", "methodology", "method.html", "Method and boundaries"),
        ("contract", "datasets", "contract.html", "Evaluation contract", False),
        ("evidence", "results", "evidence.html", "Evidence viewer", False),
    ), languages=("zh",)),
    _variant("curated", _flat(CURATED_STEM, (
        ("index", "home", "", "Summary"),
        ("suite", "datasets", "-suite", "Suite"),
        ("method", "methodology", "-method", "Environment and method"),
        ("analysis", "results", "-analysis", "Per-task analysis"),
        ("appendix", "reproduce", "-appendix", "Reproduction and appendix"),
    )), languages=("zh",)),
    _variant("public", _flat(PUBLIC_STEM, (
        ("index", "home", "", "Summary"),
        ("results", "results", "-results", "Result analysis"),
        ("suite", "datasets", "-suite", "Suite"),
        ("research", "methodology", "-research", "Selection rationale"),
        ("method", "methodology", "-method", "Environment and method"),
        ("appendix", "reproduce", "-appendix", "Reproduction and appendix"),
    )), languages=("zh",)),
))
