"""The report template contract and the registry of maintained report owners.

The base template fixes the page roles every report has and the ordered slots
on each page. A track template (``TrackTemplate``) fills those roles with
routes and declares its variants as data; benchmarks contribute extra views
through ``SectionHook``, or a variant's whole page set through ``PageSetHook``. Pages mark their role and slots, so a release check can
compare a built report with the template its manifest records.
"""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from harness.adapters import STAGES
from harness.distribution import repository_root
from harness.env_file import local_setting

ROOT = repository_root()

BASE_TEMPLATE = "webstone-report"
BASE_VERSION = 1
# Every report has these page roles; each lists its slots in reading order.
PAGE_ROLES: Mapping[str, tuple[str, ...]] = {
    "home": ("status", "lead", "core-comparison", "summary", "findings", "coverage"),
    "datasets": ("lead", "definition", "inventory", "notes"),
    "methodology": ("lead", "protocol", "notes"),
    "results": ("lead", "inventory", "comparison", "notes"),
    "interpretation": ("lead", "summary", "findings", "notes"),
    "review": ("summary", "notes"),
    "reproduce": ("commands", "notes"),
    "changes": ("history",),
}
# Authored (bound) content may appear only in these slots; every other slot is generated.
CONTENT_SLOTS = frozenset({"lead", "summary", "findings", "notes", "history"})


@dataclass(frozen=True)
class SectionHook:
    """A view that a benchmark or track contributes to one slot of one page role.

    ``key`` names the view so a track can select it; ``render`` takes the
    track's own arguments, since slots carry track-specific data.
    """

    key: str
    role: str
    slot: str
    render: Callable[..., str]

    def __post_init__(self):
        if self.slot not in PAGE_ROLES.get(self.role, ()):
            raise ValueError(f"Section hook {self.key} names an undeclared slot: {self.role}/{self.slot}")


@dataclass(frozen=True)
class PageSetHook:
    """A benchmark view that writes the whole page set of one track variant.

    ``key`` names the variant; ``render`` takes the track's render arguments.
    A track's single render entry selects it, so the benchmark's pages follow
    the same template, manifest record and release check as every other variant.
    """

    key: str
    render: Callable[..., object]


@dataclass(frozen=True)
class Page:
    """One route that fills a page role; a role may span several routes (chapters)."""

    key: str  # stable route class, used by navigation and visual checks
    role: str
    route: str
    title: str  # source-language navigation label; the track catalog translates it
    nav: bool = True  # False places the link in the page footer


@dataclass(frozen=True)
class Variant:
    """A registered selection of pages and labels over one track template."""

    name: str
    version: int
    pages: tuple[Page, ...]
    absent: frozenset[str] = frozenset()  # base roles this variant does not fill
    options: Mapping[str, Any] = field(default_factory=dict)  # the track's slot and label selections

    def __post_init__(self):
        roles = {page.role for page in self.pages}
        if not roles <= PAGE_ROLES.keys() or not self.absent <= PAGE_ROLES.keys():
            raise ValueError(f"Variant {self.name} uses an undeclared page role")
        if roles & self.absent or roles | self.absent != PAGE_ROLES.keys():
            raise ValueError(f"Variant {self.name} must fill or declare absent every page role")
        for name in ("key", "route"):
            values = [getattr(page, name) for page in self.pages]
            if len(values) != len(set(values)):
                raise ValueError(f"Variant {self.name} repeats a page {name}")

    def page(self, key):
        return next(page for page in self.pages if page.key == key)


@dataclass(frozen=True)
class TrackTemplate:
    """The pages a track fills, as one or more registered variants."""

    track: str
    version: int
    variants: tuple[Variant, ...]

    def variant(self, name):
        found = [variant for variant in self.variants if variant.name == name]
        if len(found) != 1:
            raise ValueError(f"Unregistered {self.track} report variant: {name}")
        return found[0]

    def record(self, variant):
        """The template identity a report manifest stores."""
        selected = self.variant(variant)
        return {"base": BASE_TEMPLATE, "base_version": BASE_VERSION, "track_template": self.track,
                "track_version": self.version, "variant": selected.name,
                "variant_version": selected.version}


def compose(role, fills):
    """Render a page body: each filled slot in the base order, marked for the release check."""
    order = PAGE_ROLES[role]
    unknown = set(fills) - set(order)
    if unknown:
        raise ValueError(f"Page role {role} has no slot " + ", ".join(sorted(unknown)))
    return "".join(f'<div data-slot="{slot}">{fills[slot]}</div>' for slot in order if fills.get(slot))


def recorded_variant(record, template):
    """Return the variant a manifest records, refusing a different base or track version."""
    if not isinstance(record, Mapping):
        raise ValueError("Report manifest does not record its template; rebuild it")
    variant = template.variant(record.get("variant"))
    if dict(record) | template.record(variant.name) != dict(record):
        raise ValueError("Report was built from a different template version")
    return variant


def check_page(document, page):
    """A page carries its role, its slots in base order, and authored content only in content slots."""
    main = document.select_one("main")
    if main is None or main.get("data-page-role") != page.role:
        raise ValueError(f"Page {page.route} does not fill its template role {page.role}")
    order = PAGE_ROLES[page.role]
    slots = [node.get("data-slot") for node in main.select("[data-slot]")]
    if any(slot not in order for slot in slots) or slots != sorted(slots, key=order.index) or len(slots) != len(set(slots)):
        raise ValueError(f"Page {page.route} sections differ from the {page.role} template order")
    for node in main.select("[data-authored], [data-authored-figure]"):
        slot = node.find_parent(attrs={"data-slot": True})
        if slot is None or slot["data-slot"] not in CONTENT_SLOTS:
            raise ValueError(f"Page {page.route} places authored content outside a content slot")

# Every registered track inherits these dimensions, including future tracks.
METRIC_CONTRACT = {
    "required": {
        "latency": {
            "unit": "seconds",
            "summaries": ["mean", "p50", "p95"],
            "population": "every participant; declare sample and missing counts",
            "collection": "measure failures as well as successes; retain retry cost separately",
        },
        "cost": {
            "scope": "every participant",
            "pricing": "Source/date/unit/commitment; use USD/1,000 requests where supported.",
            "usage": "Native receipts; distinguish estimates, actual charges and free allocations.",
        },
    },
    "contextual": {
        "cpu": "Decide before calls; measure local or controlled compute, else explain omission.",
        "memory": "Decide before calls; measure local or controlled memory, else explain omission.",
    },
    "unavailable": "Never substitute zero; retain the affected count and an evidenced reason.",
}


# Keys a track entry may declare: its owner and example, and one command per stage verb
# (harness.adapters.STAGES) or per existing registry stage. "build" is the report verb's key
# here; built reports record whole entries, so existing keys are not renamed. A
# profile-selected variant keeps its stage as the suffix: "<variant>_<stage>", such as
# "wcxb_build" or "snapshot_audit_publish".
TEMPLATE_FIELDS = frozenset({
    "renderer", "entrypoint", "tests", "example", "example_env", "example_version",
    "execution_owner", "execution_tests", "variants", "benchmark", "profile",
})
STAGE_KEYS = frozenset({*STAGES, "build", "release_check", "tests", "example", "example_build",
                        "example_check", "revision", "compose"})


def unknown_template_keys(entry):
    """Return keys that are neither a field, a stage nor a ``<variant>_<stage>`` variant."""
    return sorted(key for key in entry if key not in TEMPLATE_FIELDS and key not in STAGE_KEYS
                  and not any(key.endswith("_" + stage) and key != "_" + stage
                              for stage in STAGE_KEYS))


def metric_contract():
    """Return runtime requirements without requiring checkout-only authoring files."""
    return deepcopy(METRIC_CONTRACT)


def resolve_template(track, *, root=ROOT):
    registry = json.loads((root / "configs/report-templates.json").read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported report template registry")
    if track not in registry["tracks"]:
        raise ValueError(
            "Unregistered report track; add its maintained renderer, "
            "runnable example and release checks first"
        )
    result = dict(registry["tracks"][track])
    for key in ("renderer", "entrypoint", "tests", "execution_owner", "execution_tests"):
        if not isinstance(result.get(key), str):
            raise ValueError("Report track needs a registered " + key)
        path = root / result[key]
        exists = path.is_file() or (key in {"tests", "execution_tests"} and path.is_dir())
        if not exists or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Report template has a missing or unsafe " + key)
    if not (result.get("example_version") if result.get("example_env") else result.get("example")):
        raise ValueError("Report template needs a version-pinned example")
    if "metric_contract" in result or "presentation_contract" in result or "execution_contract" in result:
        raise ValueError("A report track cannot replace the shared required metric contract")
    unknown = unknown_template_keys(result)
    if unknown:
        raise ValueError("Report template keys must name a stage verb: " + ", ".join(unknown))
    return {"track": track, **result}


def discover_template(track, *, root=ROOT):
    """Add a private example link for a local reader; report manifests never carry it."""
    template = {
        **resolve_template(track, root=root),
        "metric_contract": metric_contract(),
        "execution_contract": {
            "spec": "docs/benchmark-execution.md",
            "scheduler": "src/harness/benchmark_execution.py",
            "planning": "Declare dependencies, shared resources, call budgets and measured preflight cost.",
            "preflight_check": "uv run --frozen python -m harness.benchmark_execution --plan /absolute/execution-plan.json",
            "admission": "Bound in-flight work and each shared resource; explain serial execution.",
            "durability": "Retain every dispatched call, drain on denial, and verify resume checkpoints.",
            "timing": "Distinguish quality collection, isolated latency and loaded throughput.",
        },
        "presentation_contract": {
            "metric_guide": "Explain each metric, its unit, population and direction next to comparisons.",
            "highlights": "Derive directional highlights from raw values; keep missing values neutral.",
            "details": "One reader summary; technical requests and logs are secondary evidence.",
            "release_checks": "Exercise missing explanations, wrong highlights and broken detail links.",
        },
    }
    name = template.get("example_env")
    if not name:
        return template
    # An optional link must not stop discovery, so an unusable file means it is unset.
    try:
        example = local_setting(name, root / ".env", follow_links=True).strip()
    except ValueError:
        example = ""
    if example and f"version={template['example_version']}" not in example:
        raise ValueError(f"{name} does not pin the registered example version")
    return {**template, "example": example or None}


def template_identity(track):
    """Record the maintained owner without borrowing an example's report inputs."""
    template = resolve_template(track)
    return {key: template[key] for key in ("track", "renderer", "entrypoint")}


def check_template_identity(report, track):
    """Bind the renderer: a moved entry point leaves reports built before the move valid."""
    if not report.get("profile_sha256"):
        return
    template, maintained = report.get("template") or {}, template_identity(track)
    if any(template.get(key) != maintained[key] for key in ("track", "renderer")):
        raise ValueError(f"report bypasses the maintained {track} template")
