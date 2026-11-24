"""Registered Web Fetch report variants: one input adapter per build mode.

Each variant declares the request fields it accepts and requires, the
languages it renders, the dataset kinds that select it and the function that
adapts its saved inputs into a report. ``scripts/build_report.py`` and the
check command select a variant from the profile (``variant`` or its comparison
kind) or one explicit ``--variant``; neither keeps its own list of modes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from harness.distribution import repository_root
from harness.record_io import read_json
from harness.report_languages import SUPPORTED_REPORT_LANGUAGES, resolve_report_language

ROOT = repository_root()


class VariantUsageError(ValueError):
    """A request that the selected variant cannot accept; the command reports it as usage."""


@dataclass(frozen=True)
class BuildRequest:
    """The arguments one report build receives; unset fields are ``None``."""

    profile: Path | None = None
    evidence: Path | None = None
    input: Path | None = None
    output: Path | None = None
    language: str | None = None
    include_english: bool = False
    authority_root: Path | None = None
    wcxb_source: Path | None = None
    wcxb_sample: Path | None = None
    presentation_binding: Path | None = None

    def given(self):
        return {item.name for item in fields(self) if getattr(self, item.name) not in (None, False)}

    def profile_data(self):
        return read_json(self.profile) if self.profile else {}


@dataclass(frozen=True)
class ReportVariant:
    name: str
    summary: str
    build: Callable[[BuildRequest], object]
    accepts: frozenset[str]
    requires: frozenset[str] = frozenset()
    languages: tuple[str, ...] = SUPPORTED_REPORT_LANGUAGES
    # Dataset comparison kinds whose evidence this variant renders.
    comparison_kinds: frozenset[str] = frozenset()
    # Renders an evidence directory of a selecting kind with its own benchmark view.
    render_evidence: Callable[..., Path] | None = None
    # Verifies a report this variant built; ``None`` uses the certified release check.
    check: Callable[..., object] | None = None
    # Certified evidence must match the repository's canonical task inputs.
    canonical_inputs: bool = True
    # The build writes a reliability record that packaging requires to be complete.
    reliability_record: bool = True
    # The track-template variant (page set) the build renders; defaults to the variant's own name.
    renders: str | None = None

    @property
    def template_variant(self) -> str:
        return self.renders or self.name

    def validate(self, request: BuildRequest):
        extra = request.given() - self.accepts
        if extra:
            raise VariantUsageError(f'Report variant {self.name} does not accept: '
                                    + ', '.join('--' + name.replace('_', '-') for name in sorted(extra)))
        missing = self.requires - request.given()
        if missing:
            raise VariantUsageError(f'Report variant {self.name} requires: '
                                    + ', '.join('--' + name.replace('_', '-') for name in sorted(missing)))

    def language(self, output: Path, request: BuildRequest) -> str:
        try:
            return resolve_report_language(output, request.language, supported=self.languages)
        except ValueError as error:
            raise VariantUsageError(str(error)) from error


VARIANTS: dict[str, ReportVariant] = {}


def register(variant: ReportVariant) -> ReportVariant:
    if variant.name in VARIANTS:
        raise ValueError('Duplicate report variant: ' + variant.name)
    taken = {kind for other in VARIANTS.values() for kind in other.comparison_kinds}
    if taken & variant.comparison_kinds:
        raise ValueError('A comparison kind selects one report variant')
    VARIANTS[variant.name] = variant
    return variant


def variant(name: str) -> ReportVariant:
    if name not in VARIANTS:
        raise VariantUsageError('Unregistered Web Fetch report variant: ' + str(name))
    return VARIANTS[name]


def variant_for_kind(kind: str | None) -> ReportVariant:
    """The variant whose evidence a dataset comparison kind describes; certified by default."""
    return next((item for item in VARIANTS.values() if kind in item.comparison_kinds), VARIANTS['certified'])


def select(request: BuildRequest, explicit: str | None = None) -> ReportVariant:
    """Select the variant from the profile, or the one named explicitly when they agree."""
    profile = request.profile_data()
    declared = profile.get('variant')
    selected = (variant(declared) if declared in VARIANTS
                else variant_for_kind(profile.get('comparison_kind')) if request.profile else None)
    if explicit is not None:
        if selected is not None and selected.name != explicit:
            raise VariantUsageError(f'The profile selects report variant {selected.name}, not {explicit}')
        selected = variant(explicit)
    if selected is None:
        raise VariantUsageError('Select an external report instance with --profile, or name a --variant')
    selected.validate(request)
    return selected


def build(request: BuildRequest, explicit: str | None = None):
    from harness.reporting.templates import resolve_template
    selected = select(request, explicit)
    resolve_template('webfetch')
    return selected.build(request)


# Input adapters. Each imports its build owner lazily, so selecting a variant
# never loads the others.

def _certified(request):
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.storage import (
        current_evidence_dir,
        require_canonical_inputs,
        require_current_report_profile,
        require_evidence,
    )
    selected = variant_for_kind(request.profile_data().get('comparison_kind'))
    profile = request.profile_data()
    evidence = require_evidence(request.evidence or current_evidence_dir())
    if selected.canonical_inputs and not profile.get('frozen_inputs'):
        canonical = (request.profile.parent if (request.profile.parent / 'dataset.json').is_file()
                     else ROOT / 'evaluation')
        require_canonical_inputs(evidence, canonical)
    require_current_report_profile(evidence, request.profile)
    declared_output = profile.get('report_output')
    if request.output is None and not declared_output:
        raise VariantUsageError('a custom --profile requires --output or report_output in the profile')
    output = request.output or ROOT / declared_output
    return build_report(evidence, output, include_english=request.include_english,
                        language=selected.language(output, request),
                        authored_interpretation=True, profile_path=request.profile)


def _webmainbench_sample(source, output, **options):
    from harness.web_fetch.report_build import build_webmainbench_report
    return build_webmainbench_report(source, output, **options)


def _wcxb(request):
    from harness.web_fetch.report_build import build_wcxb_report
    from harness.web_fetch.wcxb_dataset import selected_wcxb_input
    corpus, sample = selected_wcxb_input(request.wcxb_source, request.wcxb_sample, profile=request.profile)
    output = request.output or ROOT / 'reports/wcxb'
    binding = json.loads(request.profile.with_name('report-content-binding.json').read_text(encoding='utf-8'))
    return build_wcxb_report(request.evidence, corpus, sample, request.profile_data(), output,
                             content_binding=binding, authority_root=ROOT,
                             include_english=request.include_english,
                             language=VARIANTS['wcxb'].language(output, request))


def _webmainbench_full(request):
    from harness.web_fetch.report_build import build_webmainbench_full_report
    output = request.output or ROOT / 'reports/webmainbench-full'
    VARIANTS['webmainbench-full'].language(output, request)
    return build_webmainbench_full_report(request.evidence, output, authority_root=request.authority_root)


def _local_audit(request):
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.storage import require_evidence
    evidence = require_evidence(request.evidence)
    return build_report(evidence, request.output, include_english=request.include_english,
                        language=VARIANTS['local-audit'].language(request.output, request),
                        local_audit=True, show_local_status_banners=True,
                        presentation_binding=request.presentation_binding)


def _diagnostic(request):
    from harness.web_fetch.report_build import build_diagnostic_cohorts
    if not request.evidence and not request.profile:
        raise VariantUsageError('diagnostic build requires --output and evidence or a bound profile')
    VARIANTS['diagnostic-cohorts'].language(request.output, request)
    return build_diagnostic_cohorts(request.evidence, request.output, profile=request.profile)


def _snapshot_audit(request):
    from harness.web_fetch.published_snapshot import build_snapshot_audit
    VARIANTS['published-snapshot-audit'].language(request.output, request)
    return build_snapshot_audit(request.evidence, request.output, profile=request.profile)


def _public_research(request):
    from harness.web_fetch.report_build import build_public_benchmark_research
    return build_public_benchmark_research(
        request.input, request.output, include_english=request.include_english,
        language=VARIANTS['public-benchmark-research'].language(request.output, request))


def _saved_matrix(request):
    from harness.web_fetch.report_saved_matrix import build_saved_matrix
    if request.language not in (None, 'zh'):
        raise VariantUsageError('The saved-matrix variant currently supports Chinese output only')
    return build_saved_matrix(request.input, request.output)


def _check_webmainbench_sample(root, *, dataset=None, records=None, authority_root=None, profile_path=None, **_):
    from harness.web_fetch.webmainbench_report_check import check_sample_report
    arguments = () if dataset is None else (dataset, records)
    check_sample_report(root, *arguments, authority_root=authority_root, profile_path=profile_path)
    return ('PASS: WebMainBench sample matrix, scores, retries, declared-language routes '
            'and reference assets verified')


def _check_saved_matrix(root, *, source_spec=None, **_):
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    return json.dumps(check_saved_matrix(root, spec_path=source_spec), ensure_ascii=False)


def _refresh(review_only):
    def refresh(request):
        from harness.web_fetch.report_build import refresh_local_presentation
        return refresh_local_presentation(request.output, request.input, review_only=review_only)
    return refresh


_LANGUAGE = frozenset({'output', 'language', 'include_english'})
register(ReportVariant(
    'certified', 'Certified cohort from a bound instance profile', _certified,
    accepts=_LANGUAGE | {'profile', 'evidence'}, requires=frozenset({'profile'})))
register(ReportVariant(
    'webmainbench-sample', 'Certified preselected WebMainBench sample', _certified,
    accepts=_LANGUAGE | {'profile', 'evidence'}, requires=frozenset({'profile'}),
    comparison_kinds=frozenset({'webmainbench_frozen_html_to_markdown'}),
    render_evidence=_webmainbench_sample, check=_check_webmainbench_sample,
    canonical_inputs=False, reliability_record=False))
register(ReportVariant(
    'wcxb', 'Independently certified WCXB frozen-page cohort', _wcxb,
    accepts=_LANGUAGE | {'profile', 'evidence', 'wcxb_source', 'wcxb_sample'},
    requires=frozenset({'profile', 'evidence'}),
    comparison_kinds=frozenset({'wcxb_frozen_html_to_markdown'})))
register(ReportVariant(
    'webmainbench-full', 'Separately certified complete WebMainBench cohort', _webmainbench_full,
    accepts=frozenset({'evidence', 'output', 'language', 'authority_root'}),
    requires=frozenset({'evidence'}), languages=('zh',)))
register(ReportVariant(
    'local-audit', 'Explicitly local, non-publishable audit of bound evidence', _local_audit,
    accepts=_LANGUAGE | {'evidence', 'presentation_binding'},
    requires=frozenset({'evidence', 'output'})))
register(ReportVariant(
    'diagnostic-cohorts', 'Diagnostic cohort model rendered with the maintained template', _diagnostic,
    accepts=frozenset({'profile', 'evidence', 'output', 'language'}), requires=frozenset({'output'}),
    languages=('zh',)))
register(ReportVariant(
    'published-snapshot-audit', 'Score audit of a published report snapshot', _snapshot_audit,
    accepts=frozenset({'profile', 'evidence', 'output', 'language'}),
    requires=frozenset({'profile', 'evidence', 'output'}), languages=('zh',)))
register(ReportVariant(
    'public-benchmark-research', 'Public-benchmark research companion', _public_research,
    accepts=_LANGUAGE | {'input'}, requires=frozenset({'input', 'output'})))
register(ReportVariant(
    'saved-matrix', 'Hash-bound saved multi-suite results; no provider calls or rescoring',
    _saved_matrix, accepts=frozenset({'input', 'output', 'language'}),
    requires=frozenset({'input', 'output'}), languages=('zh',), check=_check_saved_matrix))
register(ReportVariant(
    'refresh-presentation', 'Refresh an existing local report from its hash-bound result binding',
    _refresh(False), accepts=frozenset({'input', 'output'}), requires=frozenset({'input', 'output'}),
    renders='local-audit'))
register(ReportVariant(
    'refresh-review', 'Refresh coverage and review details from bound supplemental reviews',
    _refresh(True), accepts=frozenset({'input', 'output'}), requires=frozenset({'input', 'output'}),
    renders='local-audit'))
