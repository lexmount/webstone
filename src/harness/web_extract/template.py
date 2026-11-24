"""The Web Extract track template: the base-template roles each report variant fills.

The report content's ``mode`` selects a variant; every variant renders through
the one ``report.page`` shell, which places each page's body and authored
introduction into the slots of its role.
"""

from harness.reporting.templates import PAGE_ROLES, Page, TrackTemplate, Variant

# Route, role and source-language navigation label of every reader page.
_READER_PAGES = (
    ("index", "home", "Overview"),
    ("dataset", "datasets", "Dataset"),
    ("results", "results", "Results"),
    ("protocol", "methodology", "Protocol"),
    ("candidates", "methodology", "Candidates"),
    ("research", "interpretation", "Market research"),
    ("accounts", "methodology", "Accounts and pricing"),
    ("review", "review", "Result review"),
    ("interpretation", "interpretation", "Interpretation"),
)
# The generated slot that holds each role's rendered body.
BODY_SLOTS = {
    "home": "core-comparison",
    "datasets": "inventory",
    "methodology": "protocol",
    "results": "inventory",
    "interpretation": "findings",
    "review": "notes",
}
# Authored page introductions lead the page unless a variant places them after the home comparison.
AFTER_COMPARISON = {"index": "summary"}


def _pages(rows):
    return tuple(Page(key=key, role=role, route=key + ".html", title=title)
                 for key, role, title in rows)


def _variant(name, rows, intro_slots=None, **options):
    """``notice`` names the evidence-state catalog message (and relabels results as results and
    failures); ``external_navigation`` lets navigation name other reports and resolves links
    from the site root."""
    pages = _pages(rows)
    return Variant(name=name, version=1, pages=pages,
                   absent=frozenset(PAGE_ROLES) - {page.role for page in pages},
                   options={"intro_slots": intro_slots or {}, **options})


_MACHINE_SCORED = "frozen_product_page_extraction_machine_scoring_failed_calls_retai"
TEMPLATE = TrackTemplate(track="web-extract", version=1, variants=(
    _variant("product", _READER_PAGES),
    _variant("product-progress", _READER_PAGES, AFTER_COMPARISON, notice=_MACHINE_SCORED),
    _variant("record-cohort", _READER_PAGES, AFTER_COMPARISON, notice="records_notice"),
    _variant("suite", _READER_PAGES[:1], notice=_MACHINE_SCORED, external_navigation=True),
))
# Report content modes (the manifest ``mode``) and the variant each renders.
MODE_VARIANTS = {
    "compatibility": "product",
    "full-cohort-progress": "product-progress",
    "field-extraction-cohort": "record-cohort",
    "field-extraction-suite": "suite",
}
def variant_for_mode(mode):
    return TEMPLATE.variant(MODE_VARIANTS.get(mode, "product"))


def intro_follows_comparison(route, mode):
    """Whether this variant places the page's authored intro after its core comparison."""
    return route in variant_for_mode(mode).options["intro_slots"]


def intro_slot(route, mode):
    """The slot that holds a page's authored introduction in this variant.

    An intro leads its page in the role's first content slot unless the variant
    places it after the core comparison.
    """
    variant = variant_for_mode(mode)
    placed = variant.options["intro_slots"].get(route)
    if placed:
        return placed
    role = variant.page(route).role
    slots = PAGE_ROLES[role]
    return "lead" if "lead" in slots else next(slot for slot in slots if slot != BODY_SLOTS[role])
