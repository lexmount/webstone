"""The Web Fetch report model: the one validated input the renderer accepts.

Every build variant (``report_variants``) adapts its saved evidence into this
model. The renderer reads participants, report-only suites, result rows and
render options from it; nothing reaches the renderer through module state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields

from harness.record_io import canonical_json_sha256
from harness.web_fetch.models import report_family


@dataclass(frozen=True)
class Participant:
    """A declared method; a participant without results still gets a route."""

    id: str
    label: str


@dataclass(frozen=True)
class Suite:
    """A report-only grouping of cases; execution tracks and acceptance stay unchanged."""

    id: str
    label: str
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class RenderOptions:
    """Which routes a build writes and which state notices it must show."""

    include_english: bool = False
    authored_interpretation: bool = True
    presentation_only: bool = False
    show_local_status_banners: bool = True
    detail_attempt_ids: frozenset[str] | None = None
    dataset_case_ids: frozenset[str] | None = None
    frozen_scores: bool = False
    english_routes: tuple[str, ...] | None = None
    diagnostic_view_paths: tuple[str, ...] = ()

    def __post_init__(self):
        for value, name in ((self.english_routes, 'English'), (self.detail_attempt_ids, 'detail'),
                            (self.dataset_case_ids, 'dataset')):
            if value is not None and not self.presentation_only:
                raise ValueError(f'Scoped {name} refresh requires presentation_only=True')


@dataclass(frozen=True)
class ReportModel:
    """Frozen dataset, logical result rows, batch manifests and their presentation."""

    dataset: Mapping
    records: list
    manifests: Sequence = ()
    participants: tuple[Participant, ...] = ()
    suites: tuple[Suite, ...] = ()
    options: RenderOptions = field(default_factory=RenderOptions)
    # A final saved-results dataset renders only with the projection hash its
    # source validation admitted; serialized summaries cannot admit themselves.
    source_admission: str | None = None

    def __post_init__(self):
        if not isinstance(self.dataset, Mapping) or not isinstance(self.dataset.get('cases'), list):
            raise ValueError('Report model needs a dataset with a case list')
        if not isinstance(self.records, list) or not all(isinstance(row, Mapping) for row in self.records):
            raise ValueError('Report model needs a list of result rows')
        self.require_admission()
        identities = [participant.id for participant in self.participants]
        if len(identities) != len(set(identities)):
            raise ValueError('Report model repeats a participant')
        if self.suites:
            case_ids = {case.get('id') for case in self.dataset['cases']}
            grouped = [case_id for suite in self.suites for case_id in suite.case_ids]
            if (len({suite.id for suite in self.suites}) != len(self.suites)
                    or len(grouped) != len(set(grouped)) or set(grouped) != case_ids):
                raise ValueError('Report suites must partition every case exactly once')
            # The renderer ranks a suite by each case's report family, so they must agree.
            family = {case_id: suite.id for suite in self.suites for case_id in suite.case_ids}
            if any((case.get('report_family') or report_family(case)) != family[case.get('id')]
                   for case in self.dataset['cases']):
                raise ValueError("Report suites must match each case's report family")
        unknown = (set(self.options.detail_attempt_ids) - {row['attempt_id'] for row in self.records}
                   if self.options.detail_attempt_ids else ())
        if unknown:
            raise ValueError('Unknown detail refresh attempts: ' + ', '.join(sorted(unknown)))
        unknown = set(self.options.dataset_case_ids or ()) - {case['id'] for case in self.dataset['cases']}
        if unknown:
            raise ValueError('Unknown dataset refresh cases: ' + ', '.join(sorted(unknown)))

    def require_admission(self):
        """Final saved results render only while they still match their admitted projection."""
        if (self.dataset.get('comparison', {}).get('publication_state') == 'final_saved_results'
                and self.source_admission != canonical_json_sha256([self.dataset, self.records])):
            raise ValueError('Final saved results require source-backed admission')

    @property
    def labels(self) -> dict[str, str]:
        return {participant.id: participant.label for participant in self.participants}


def report_model(dataset, records, manifests=(), *, participants=(), suites=(), source_admission=None,
                 **options) -> ReportModel:
    """Build a validated model from saved JSON shapes.

    ``participants`` are ``{"id", "label"}`` objects and ``suites`` are
    ``{"id", "label", "case_ids"}`` objects, as saved matrices store them.
    """
    known = {item.name for item in fields(RenderOptions)}
    unknown = set(options) - known
    if unknown:
        raise TypeError('Unknown report option: ' + ', '.join(sorted(unknown)))
    for name in ('detail_attempt_ids', 'dataset_case_ids'):
        if options.get(name) is not None:
            options[name] = frozenset(options[name])
    if options.get('english_routes') is not None:
        options['english_routes'] = tuple(options['english_routes'])
    options['diagnostic_view_paths'] = tuple(options.get('diagnostic_view_paths', ()))
    return ReportModel(
        dataset=dataset, records=records, manifests=manifests,
        participants=tuple(Participant(item['id'], item.get('label', item['id'])) for item in participants),
        suites=tuple(Suite(item['id'], item['label'], tuple(item['case_ids'])) for item in suites),
        options=RenderOptions(**options), source_admission=source_admission)


@dataclass(frozen=True)
class MethodScope:
    """The measured methods one render describes: rows, call-time configurations,
    the instance's frozen method registry and labels bound to recorded versions."""

    records: Mapping = field(default_factory=dict)
    execution_configs: Mapping = field(default_factory=dict)
    registry: Mapping = field(default_factory=dict)
    labels: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class RenderContext:
    """Render state derived from one ``ReportModel`` and its measured methods.

    Every renderer helper that depends on it takes it as ``ctx``; the methods
    helpers take the measured-method part as ``scope``.
    """

    labels: Mapping[str, str] = field(default_factory=dict)
    suites: tuple = ()
    banner: str = ''
    english_generated: bool = False
    score_audit: dict | None = None
    coverage_plan: dict | None = None
    scope_presentation: dict | None = None
    descriptive_ranks: bool = False
    result_views: dict | None = None
    methods: MethodScope = field(default_factory=MethodScope)


def model_render_context(model=None, *, methods, descriptive_ranks=False, result_views=None):
    """The render context of ``model``'s participants and suites; shared views may opt into descriptive ranks."""
    return RenderContext(
        labels=model.labels if model else {}, suites=model.suites if model else (),
        descriptive_ranks=descriptive_ranks, result_views=result_views, methods=methods)
