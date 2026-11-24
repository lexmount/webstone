"""Web Search benchmark adapters: populations, metric sets, answer graders and reference datasets.

Comparison, report, output-level, answer and revision code read these
declarations instead of naming a dataset. A further benchmark is added by
registering one more ``SearchBenchmark``; its ``member`` predicate must not
overlap another benchmark's.
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import ModuleType

from harness.adapters import Registry
from lexbench_websearch import simpleqa
from lexbench_websearch.answers import (
    SCENARIO_PROMPT,
    SIMPLEQA_UPSTREAM,
    answer_messages,
    factual_grade,
    factual_messages,
    official_template,
    scenario_grade,
)
from lexbench_websearch.scoring import evaluate, factual_evidence


@dataclass(frozen=True)
class AnswerGrading:
    """How the L3 judge grades one benchmark's generated answers.

    ``source`` names the pinned grader source a run must supply, when the
    benchmark's prompt is taken from upstream code.
    """

    prompt: Callable  # (grader_source) -> prompt text bound in judge evidence
    messages: Callable  # (case, packet, grader_source) -> judge messages
    parse: Callable  # (packet, response_text) -> grade
    result_field: str
    value: Callable  # (grade) -> value stored in ``result_field``
    json_response: bool
    format_correction: str
    source: dict | None = None  # {"upstream", "filename", "label"}


@dataclass(frozen=True)
class ReferenceDataset:
    """A published dataset whose reference extensions a report revision may apply."""

    track: str
    path: str
    repository: str
    rules: ModuleType  # mirror qualification, repairs and reference evidence checks


@dataclass(frozen=True)
class SearchBenchmark:
    """One Web Search task population; conforms to ``harness.adapters.BenchmarkAdapter``."""

    name: str  # population id; persisted in judge evidence and report data
    label: str  # English catalog key; translated by the report catalog
    order: int
    member: Callable[[dict], bool]
    metric_sets: Callable  # (level, quality_cases) -> [(key, label)]
    level_values: Callable  # (level, row, case, answer, relevance, quality_cases) -> dict
    answers: AnswerGrading
    text: dict = field(default_factory=dict)  # English catalog keys for the comparison guide
    references: ReferenceDataset | None = None

    def cases(self, profile):
        return [case for case in profile['cases'] if self.member(case)]

    def inputs(self, cases):
        """Search calls query the live web; there is no served input environment."""
        return nullcontext(None)

    def score(self, case, scored_input):
        return evaluate(case, scored_input)

    def populations(self):
        return [dict(id=self.name, label=self.label,
                     metrics={level: self.metric_sets(level, []) for level in ('l1', 'l2', 'l3')})]


BENCHMARKS = Registry("web search benchmark")
# Introduces the registered benchmarks together in the output-level guide.
GUIDE_OVERVIEW = ('Scenario queries measure search usefulness; SimpleQA checks factual sources and answers. '
                  'Each output type has its own primary metric.')


def ordered():
    """Registered benchmarks in their declared presentation order."""
    return sorted((item for _, item in BENCHMARKS.items()), key=lambda item: item.order)


def catalog_keys():
    """English catalog keys these declarations pass to the report's dynamic lookups."""
    keys = {GUIDE_OVERVIEW}
    for item in ordered():
        keys.add(item.label)
        for value in item.text.values():
            if isinstance(value, str):
                keys.add(value)
            elif isinstance(value, dict):
                for entry in value.values():
                    keys.update((entry,) if isinstance(entry, str) else entry)
    return keys


def benchmark_of(case):
    matches = [item for item in ordered() if item.member(case)]
    if len(matches) != 1:
        raise ValueError('Each search task belongs to exactly one benchmark: ' + str(case.get('id')))
    return matches[0]


def grader_source():
    """The single pinned grader source that L3 answer grading requires."""
    sources = [item.answers.source for item in ordered() if item.answers.source]
    if len(sources) != 1:
        raise ValueError('L3 answer grading requires exactly one declared grader source')
    return sources[0]


def reference_dataset(track=None):
    """The dataset declaring reference revisions, selected by track when one is given."""
    found = [item.references for item in ordered() if item.references
             and (track is None or item.references.track == track)]
    if len(found) != 1:
        raise ValueError('Reference extensions require a registered reference dataset')
    return found[0]


def _scenario_metrics(level, cases):
    if level == 'l3':
        return [('answer_grade', 'Answer usefulness / 3')]
    return [('ndcg', 'nDCG@5'), ('precision', 'Precision@5'), ('success', 'Success@5')]


def _scenario_values(level, row, case, answer, relevance, cases):
    if level == 'l3':
        return dict(answer_grade=answer()['answer_grade'])
    scores = relevance()
    return {key: scores[key] for key in ('ndcg', 'precision', 'success')}


def _audit_complete(cases):
    return bool(cases) and all('reference_verification_coverage' in case for case in cases)


def _factual_metrics(level, cases):
    if level == 'l3':
        return [('correct', 'SimpleQA accuracy')]
    if level != 'l1':
        return [('answer_match', 'Extra-text Answer Match@5')]
    metrics = [('source_rr', 'MRR@5'), ('source_hit', 'Source Hit@5'), ('answer_match', 'Answer Match@5')]
    if _audit_complete(cases):
        metrics += [('publisher_rr', 'Publisher-only MRR@5'), ('publisher_hit', 'Publisher-only Hit@5')]
    return metrics


def _factual_values(level, row, case, answer, relevance, cases):
    if level == 'l3':
        grade = answer()['factual_grade']
        return dict(correct=int(grade == 'CORRECT'), incorrect=int(grade == 'INCORRECT'),
                    not_attempted=int(grade == 'NOT_ATTEMPTED'))
    metrics = row['metrics']
    if level != 'l1':
        return dict(answer_match=metrics['answer_in_extra_text_5'])
    values = dict(source_rr=metrics['labeled_source_rr_5'], source_hit=metrics['labeled_source_hit_5'],
                  answer_match=metrics['answer_in_preview_5'])
    if _audit_complete(cases):
        publisher = factual_evidence(dict(case, equivalent_reference_urls=[]), row['results'], cutoff=5)
        values.update(publisher_rr=publisher['labeled_source_rr_5'], publisher_hit=publisher['labeled_source_hit_5'])
    return values


BENCHMARKS.register('scenario', SearchBenchmark(
    name='scenario', label='Scenario relevance', order=0,
    member=lambda case: 'reference_answer' not in case,
    metric_sets=_scenario_metrics, level_values=_scenario_values,
    answers=AnswerGrading(
        prompt=lambda source: SCENARIO_PROMPT,
        messages=lambda case, packet, source: answer_messages(packet),
        parse=scenario_grade, result_field='answer_grade', value=lambda grade: grade['grade'], json_response=True,
        format_correction=("\nOutput-format correction only; keep the rubric unchanged. Return the required "
                           "scenario JSON with only an integer grade and a short reason; do not copy evidence.")),
    text=dict(
        column='Scenario benchmark · {count} queries',
        caption='Practical searches for official sites, resources and information.',
        guide=dict(l1=('Are the first five results useful, and are the best ones ranked first?',),
                   l2=('Do the titles and additional excerpts satisfy the search intent, in the returned order?',),
                   l3=('Does the generated answer directly satisfy the request?',)),
        model_graded=frozenset({'l1', 'l2', 'l3'}),
        purpose=dict(l1='Search tasks test the usefulness and ranking of the first five results.',
                     l2='Search tasks test the usefulness and ranking of the first five results.',
                     l3='Search tasks test whether the generated answer satisfies the request.'),
        finding=dict(l1=('Scenario queries test whether the delivered text satisfies the query; '
                         'nDCG@5 rewards relevant results appearing earlier.'),
                     l2=('Scenario queries test whether the delivered text satisfies the query; '
                         'nDCG@5 rewards relevant results appearing earlier.'),
                     l3='Scenario queries test how useful the generated answer is, from 0 to 3.')),
))

BENCHMARKS.register('factual', SearchBenchmark(
    name='factual', label='SimpleQA Verified', order=1,
    member=lambda case: 'reference_answer' in case,
    metric_sets=_factual_metrics, level_values=_factual_values,
    answers=AnswerGrading(
        prompt=official_template,
        messages=lambda case, packet, source: factual_messages(case, packet['answer'], source),
        parse=lambda packet, text: factual_grade(text), result_field='factual_grade', value=lambda grade: grade,
        json_response=False,
        format_correction="\nOutput-format correction only; keep the original rubric unchanged. Return exactly A, B or C.",
        source=dict(upstream=SIMPLEQA_UPSTREAM, filename='simpleqa_eval.py', label='official SimpleQA grader')),
    text=dict(
        column='SimpleQA Verified · {count} valid queries',
        caption='Factual questions with reference answers and source pages.',
        guide=dict(
            l1=('How highly is an annotated reference page ranked in the first five results?',
                'Answer-text match is a secondary check on titles and primary previews; it is not answer correctness.'),
            l2=('Does any of the first five additional excerpts contain the reference answer text?',
                'This text-match check excludes titles and primary previews; it does not judge a generated answer.'),
            l3=('Is the generated answer correct against the reference answer?',
                'The official SimpleQA rubric classifies answers as correct, incorrect or not attempted.')),
        model_graded=frozenset({'l3'}),
        purpose=dict(
            l1='Factual questions test whether the first five results find a verified reference page and rank it early.',
            l2='Factual questions test answer-text coverage in additional excerpts, excluding titles and primary previews.',
            l3='Factual questions test generated-answer correctness with the official SimpleQA rubric.'),
        finding=dict(
            l1=('Factual queries test retrieval of the annotated supporting pages. MRR@5 rewards finding that source '
                'earlier; alternative pages are not automatically equivalent sources.'),
            l2=('Factual queries test whether the extra text contains the reference answer. Source ranking is not '
                'measured at this level.'),
            l3=('Factual answers are graded correct, incorrect or not attempted with the official SimpleQA template. '
                'The primary metric is the fraction graded correct.'))),
    references=ReferenceDataset(track='simpleqa-verified', path='datasets/factual-v1.json',
                                repository='Lexmount/LexBench-SimpleQA-Verified', rules=simpleqa),
))
