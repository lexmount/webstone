"""The shared release gate: one ordered check that every track's release passes.

A track contributes its own rules as callbacks; the gate owns their order and the
checks every report shares (language editions, local links and presentation).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from harness.record_io import file_sha256
from harness.report_languages import generated_languages, language_metadata_path
from harness.reporting.links import check as check_links
from harness.reporting.presentation import check_presentation
from harness.reporting.templates import check_page, check_template_identity, recorded_variant
from harness.reporting.versions import check_build_version
from harness.safety import reject_evidence_symlinks


def open_report(root) -> Path:
    """Refuse a linked report tree before any rule reads from it."""
    root = Path(root)
    reject_evidence_symlinks(root)
    return root.resolve()


def read_page(path) -> BeautifulSoup:
    return BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")


def checked_page(path) -> BeautifulSoup:
    """Read one page through the shared comparison and evidence presentation rules."""
    return check_presentation(read_page(path))


def language_editions(root: Path) -> tuple[tuple[str, str], ...]:
    """Name each generated edition and its route prefix; Chinese stays at the root."""
    return tuple((language, "en/" if language == "en" else "")
                 for language in generated_languages(root))


def check_report_identity(root: Path, manifest: dict, track: str, profile_path: Path | None = None,
                          *, allow_unbound_profile: bool = False) -> None:
    """Bind the build, the maintained template and, when selected, the instance profile.

    ``allow_unbound_profile`` keeps reports from before profile binding readable.
    """
    check_build_version(root, manifest)
    check_template_identity(manifest, track)
    if profile_path is None:
        return
    bound = manifest.get("profile_sha256")
    if allow_unbound_profile and not bound:
        return
    if profile_path.is_symlink() or bound != file_sha256(profile_path):
        raise ValueError("report was built from a different instance profile")


def check_template_pages(root: Path, record, template):
    """Every registered page of the recorded variant exists in each edition and fills its role in order.

    Returns the recorded variant so a track can compare it with its bound content.
    """
    variant = recorded_variant(record, template)
    for language, prefix in language_editions(root):
        for page in variant.pages:
            path = root / prefix / page.route
            if not path.is_file():
                raise ValueError(f"Report {language} edition is missing its {page.role} page {page.route}")
            check_page(read_page(path), page)
    return variant


# Release gate

# The page markers that ``check_presentation`` reads. A page without any of them
# has nothing to check, so the gate reads only the bytes of such a page.
_PRESENTATION_MARKERS = (b"data-metric-direction", b"data-significance", b"data-sort-controls",
                         b"data-dataset-inventory", b"data-report-detail")

RELEASE_STAGES = ("identity", "languages", "links", "presentation", "evidence", "visual")


def check_language_editions(root: Path) -> None:
    """Every declared language edition has its home page.

    Reports from before language metadata declare no editions and keep their own checks.
    """
    metadata = language_metadata_path(root)
    if not metadata.is_file() or "languages" not in json.loads(metadata.read_bytes()):
        return
    for language, prefix in language_editions(root):
        if not (root / prefix / "index.html").is_file():
            raise ValueError(f"Generated {language} edition has no home page")


def check_report_presentation(root: Path) -> int:
    """Run the shared comparison and evidence presentation rules over every page."""
    checked = 0
    for page in sorted(root.rglob("*.html")):
        raw = page.read_bytes()
        if any(marker in raw for marker in _PRESENTATION_MARKERS):
            check_presentation(BeautifulSoup(raw.decode("utf-8"), "html.parser"))
            checked += 1
    return checked


@dataclass(frozen=True)
class VisualReceipt:
    """A complete visual check bound to the report; rerun when it is not current."""

    path: Path
    is_current: Callable[[Path, Path], bool]  # (report, receipt)
    check: Callable[[Path, Path], object]  # (report, receipt) renders and writes the receipt


@dataclass(frozen=True)
class ReleaseRules:
    """One track's contributions to the shared release gate.

    ``identity`` and ``evidence`` are the track's own rules. ``languages`` and
    ``links`` default to the shared checks; a track replaces one only when its
    layout differs, and names the replacement's reason where it declares it.
    Presentation defaults to the shared checker. A frozen-template adapter may
    retain verified predecessor markup while checking every current view.
    """

    identity: Callable[[Path], object]
    evidence: Callable[[Path], object] | None = None
    languages: Callable[[Path], object] | None = check_language_editions
    links: Callable[[Path], object] | None = check_links
    visual: VisualReceipt | None = None
    presentation: Callable[[Path], object] | None = None


def validate_release(report, rules: ReleaseRules) -> dict[str, object]:
    """Run the release gate in its fixed order and return each stage's result.

    Order: identity, languages, links, presentation, evidence bindings, visual
    receipt. Presentation always runs; frozen-source adapters retain old HTML
    identities while applying the shared checker to current views.
    """
    root = open_report(report)
    results: dict[str, object] = {"identity": rules.identity(root)}
    if rules.languages is not None:
        results["languages"] = rules.languages(root)
    if rules.links is not None:
        results["links"] = rules.links(root)
    results["presentation"] = (rules.presentation or check_report_presentation)(root)
    if rules.evidence is not None:
        results["evidence"] = rules.evidence(root)
    if rules.visual is not None:
        visual = rules.visual
        current = visual.is_current(root, visual.path)
        results["visual"] = "current" if current else visual.check(root, visual.path)
    return results
