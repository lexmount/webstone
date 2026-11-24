"""Report views that registered Search benchmarks contribute through ``views()``.

Each function fills one Search hook point (``template.HOOK_POINTS``); shared
report and comparison code renders whatever the registered benchmarks declare.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlsplit

from harness.reporting.presentation import esc
from harness.reporting.shell import cells, numeric_cell, numeric_winners, table
from harness.web_search.messages import report_text, zh_text
from lexbench_websearch import answer_rules
from lexbench_websearch.evidence_quality import factual_sensitivity
from lexbench_websearch.scoring import (
    answer_evidence_key,
    canonical_url,
    evaluate,
    factual_evidence,
)


def factual_panel(profile, selected, zh, *, compact=False):
    cutoff = profile["protocol"]["top_k"]
    excluded = set(profile.get('factual_quality_exclusions', []))
    cases = {c['id']: c for c in profile['cases'] if 'reference_answer' in c and c['id'] not in excluded}
    if not cases:
        return ''
    projected = bool(profile.get("comparison_selection") or profile.get("current_selection"))
    baseline = profile['methods'] if projected else [m for m in profile['methods'] if not any(m.get('output_options', {}).values())]
    answer_key = f'answer_in_preview_{cutoff}' if all(c.get('answer_evidence_scope') == 'preview' for c in cases.values()) else f'answer_in_delivered_text_{cutoff}' if projected else f'answer_in_preview_{cutoff}'
    keys = (f'labeled_source_hit_{cutoff}', f'labeled_source_rr_{cutoff}', answer_key)
    values = []
    by_method = {}
    for method in profile['methods']:
        group = {r['case_id']: r for r in selected if r['method'] == method['id'] and r['case_id'] in cases}
        by_method[method['id']] = group
        if method in baseline:
            values.append((method['label'], len(group), [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[k] for r in group.values()) for k in keys] + [sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][answer_key] for r in group.values())]))
    answer_description = (report_text("Check top-{cutoff} given URLs and answer text in titles and standard snippets, excluding dataset mirrors and evaluation logs.", zh, cutoff=cutoff)
                          if answer_key == f"answer_in_preview_{cutoff}" else report_text("Check top-{cutoff} given URLs and answer text in all delivered fields, excluding dataset mirrors and evaluation logs. Text presence does not verify its context.", zh, cutoff=cutoff))
    if compact:
        headers = [report_text("Method", zh), report_text("Reference Hit@{cutoff}", zh, cutoff=cutoff), report_text("Reference MRR@{cutoff}", zh, cutoff=cutoff), report_text("Answer Match@{cutoff} (text)", zh, cutoff=cutoff)]
        rendered = []
        best = numeric_winners([[v[2][i]/v[1] if v[1] else None for i in (0,1,3)] for v in values], {0: "max", 1: "max", 2: "max"})
        for label, n, sums in values:
            row = '<td>' + esc(label) + '</td>'
            for i, total in enumerate((sums[0], sums[1], sums[3])):
                display = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
                row += numeric_cell(display, total/n if n else None, f'factual-current-{i}', 'max', n > 0 and best.get(i) == total/n)
            rendered.append('<tr>' + row + '</tr>')
        return '<section data-factual><h2>' + report_text("Factual evidence retrieval: {cases} queries", zh, cases=len(cases)) + '</h2><p>' + answer_description + '</p>' + table(headers, rendered) + report_text('<p class="muted">Target MRR is available through the per-query reciprocal-rank details.</p>', zh) + ('<p data-quality-exclusions>' + report_text("Reference review excludes {excluded} defective queries for every provider; quality uses {cases} queries. Original responses, timing and cost retain the full collection.", zh, excluded=len(excluded), cases=len(cases)) + '</p>' if excluded else '') + '</section>'
    rendered = []
    for label, n, sums in values:
        row = ['<td>' + esc(label) + '</td>', f'<td>{n}</td>']
        for i, total in enumerate(sums):
            direction = "neutral" if i == 2 else "max"
            fractions = [[v[2][i] / v[1] if v[1] else None] for v in values]
            value_raw = total / n if n else None
            best = value_raw is not None and numeric_winners(fractions, {0: direction}).get(0) == value_raw
            value = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
            row.append(numeric_cell(value, value_raw, f"factual-{i}", direction, best))
        rendered.append('<tr>' + ''.join(row) + '</tr>')
    body = '<section data-factual><h3>' + (
        report_text("Public factual questions: sources and answer cues", zh) if projected else report_text("Public factual questions: basic-API evidence discovery", zh)
    ) + '</h3><p>' + (
        report_text("A source hit means at least one published supporting URL appears in the top {cutoff}. MRR averages reciprocal first-hit rank: 1 for rank one, 0.5 for rank two and zero for a miss. Answer cues use only shared titles and snippets. Failures, empties and short lists remain in the population.", zh, cutoff=cutoff)
    ) + '</p>' + table(
        [report_text("API", zh, context="base"), report_text("Queries", zh), report_text("Given webpage retrieved", zh), report_text("First-source reciprocal rank", zh), report_text("Answer text: raw", zh), report_text("Answer text: dataset mentions excluded", zh)], rendered)
    if projected:
        body = body.replace(zh_text("Answer cues use only shared titles and snippets."), zh_text("Answer cues use every delivered title, snippet, summary and body in the top {cutoff}.", cutoff=cutoff)).replace('Answer cues use only shared titles and snippets.', f'Answer cues use every delivered title, snippet, summary and body in the top {cutoff}.').replace(zh_text("API", context="base") + '</th>', zh_text("Method") + '</th>').replace('API</th>', 'Method</th>').replace(zh_text("Answer text: raw"), zh_text("Delivered answer cue: raw")).replace('Answer text: raw', 'Delivered answer cue: raw')
    body += '<p>' + report_text("Contamination exclusion is a post-hoc deterministic diagnostic. Results naming SimpleQA in their URL/title, or containing the complete question together with evaluation fields, validation logs or dataset-viewer structure, are blanked without refilling or compacting ranks. Original values remain visible.", zh) + '</p>'
    if excluded:
        body += '<p data-quality-exclusions>' + report_text("Independent reference review excluded {excluded} defective or materially ambiguous questions from all methods’ quality metrics, leaving {cases} common queries. All queries remain in latency and cost; original inputs, responses and diagnostics remain inspectable.", zh, excluded=len(excluded), cases=len(cases)) + '</p>'
    extra = []
    for enabled in ([] if projected else profile['methods']):
        options = enabled.get('output_options', {})
        if not any(options.values()):
            continue
        disabled = next((m for m in baseline if m['adapter'] == enabled['adapter'] and m.get('output_options') == {k: False for k in options}), None)
        if disabled is None:
            continue
        a, b = by_method[disabled['id']], by_method[enabled['id']]
        if set(a) != set(b):
            raise ValueError('Incomplete factual content pair')
        a = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in a.items()}
        b = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in b.items()}
        key = f'answer_in_delivered_text_{cutoff}'
        extra.append(cells([esc(enabled['label']), str(len(a)),
            str(sum(r['metrics'][key] for r in a.values())), str(sum(r['metrics'][key] for r in b.values())),
            str(sum(not a[c]['metrics'][key] and b[c]['metrics'][key] for c in a)),
            str(sum(a[c]['metrics'][key] and not b[c]['metrics'][key] for c in a))]))
    if extra:
        body += '<h3>' + report_text("Option experiment: answer cues in all delivered text", zh) + '</h3><p>' + (
            report_text("This includes returned summaries and bodies after the same explicit-mirror exclusion. Added means absent when disabled and present when enabled; lost is the reverse. Search results can change, so this is the overall option effect rather than a fixed-page body-only gain.", zh)
        ) + '</p>' + table([report_text("Enabled mode", zh), report_text("Pairs", zh), report_text("Disabled hits", zh), report_text("Enabled hits", zh), report_text("Added", zh), report_text("Lost", zh)], extra)
    if any(c.get('answer_match_rule') == answer_rules.POLICY_NAME for c in cases.values()):
        return body + '<p class="muted">' + (
            report_text("Answer checks use the published answer, explicit aliases and allowed numeric ranges, preserving units and percentage modifiers. Matches stay within one returned field; fields are never joined. Unspecified synonyms are not inferred. Negated or unrelated mentions can still match, so this is lexical evidence rather than semantic accuracy. Details expose the reference answer, sources and actual output.", zh)
        ) + '</p></section>'
    return body + '<p class="muted">' + (
        report_text("Answer matching normalizes Unicode, case and punctuation and requires consecutive whole tokens within one field. Negated or unrelated mentions can match; paraphrases can miss. These are source and lexical checks, not semantic accuracy. Every detail exposes the answer, sources and output.", zh)
    ) + '</p></section>'


def source_matching_chart(view, panel, zh=False):
    """Recompute all-source score components; no provider-specific URL exception."""
    t = partial(report_text, zh=zh)
    if not panel['case_ids']:
        return ''
    ids = set(panel['case_ids'])
    cases = {c['id']: c for c in view['profile']['cases'] if c['id'] in ids}
    labels = [t('Original URL match'), t('Equivalent URL form'), t('Verified copy of the same page'), t('Reference page not retrieved')]
    body = '<figure class="source-matching" data-source-matching><figcaption>' + t('Where source matches come from') + '</figcaption><p>' + t('Every bar uses the complete factual quality population. Equivalent URL forms and verified copies count in the main ranking for every method. The remaining segment means none of the first five results matches an annotated page; it does not identify a geographic restriction.') + '</p>'
    body += '<ul class="source-legend">' + ''.join('<li class="source-part-' + str(i) + '">' + label + '</li>' for i,label in enumerate(labels)) + '</ul>'
    for method in view['profile']['methods']:
        counts = [0, 0, 0, 0]
        for row in view['rows']:
            if row['method'] != method['id'] or row['case_id'] not in ids:
                continue
            case = cases[row['case_id']]
            original = {k: v for k,v in case.items() if k not in ('url_equivalence', 'equivalent_reference_urls', 'equivalent_reference_provenance')}
            folded = dict(original, url_equivalence='document-url/2')
            a = factual_evidence(original, row['results'])['labeled_source_hit_5']
            b = factual_evidence(folded, row['results'])['labeled_source_hit_5']
            c = factual_evidence(case, row['results'])['labeled_source_hit_5']
            counts[0 if a else 1 if b else 2 if c else 3] += 1
        body += '<div class="source-chart-row" data-source-method="' + esc(method['id']) + '"><strong>' + esc(method['label']) + '</strong><div class="source-stack">'
        body += ''.join('<span class="source-part-' + str(i) + '" style="width:' + str(count/len(ids)*100) + '%" title="' + esc(labels[i]) + ': ' + str(count) + '">' + (str(count) if count else '') + '</span>' for i,count in enumerate(counts))
        body += '</div><p>' + ' · '.join(label + ' ' + str(count) for label,count in zip(labels,counts,strict=True)) + '</p></div>'
    return body + '</figure>'


def factual_outcome_chart(view, panel, zh=False):
    """Explain source/answer-cue differences using every factual quality query."""
    t = partial(report_text, zh=zh)
    if not panel['case_ids']:
        return ''
    cases = {c['id']: c for c in view['profile']['cases']}
    labels = [t('Source and answer cue'), t('Source only'), t('Answer cue only'), t('Neither')]
    body = '<figure data-factual-outcomes><figcaption>' + t('Finding a source and seeing answer words are different outcomes') + '</figcaption><p>' + t('Answer words may occur on another page even when the annotated source is absent. This chart counts the two checks separately; answer-word matches do not grade answer correctness.') + '</p>'
    body += '<ul class="source-legend">' + ''.join('<li class="source-part-' + str(i) + '">' + label + '</li>' for i,label in enumerate(labels)) + '</ul>'
    for method in view['profile']['methods']:
        mid = method['id']
        scored = [s for s in panel['scores'] if s['method'] == mid]
        counts = [0, 0, 0, 0]
        for score in scored:
            counts[0 if score['source_hit'] and score['answer_match'] else 1 if score['source_hit'] else 2 if score['answer_match'] else 3] += 1
        body += '<div class="source-chart-row" data-factual-outcome-method="' + esc(mid) + '"><strong>' + esc(method['label']) + '</strong><div class="source-stack">'
        body += ''.join('<span class="source-part-' + str(i) + '" data-count="' + str(count) + '" style="width:' + str(count/len(scored)*100) + '%" title="' + esc(labels[i]) + ': ' + str(count) + '">' + (str(count) if count else '') + '</span>' for i,count in enumerate(counts))
        body += '</div><p>' + ' · '.join(label + ' ' + str(count) for label,count in zip(labels,counts,strict=True)) + '</p>'
        for row in sorted((r for r in view['rows'] if r['method'] == mid and r['case_id'] in panel['case_ids']), key=lambda r:r['case_id']):
            case = cases[row['case_id']]
            references = {canonical_url(u, policy='document-url/2'): u for u in case.get('reference_urls', [])
                          if urlsplit(u).hostname == 'en.wikipedia.org'}
            matched = next((item for item in row['results'][:5] if not item.get('invalid_slot') and canonical_url(item['url'], policy='document-url/2') in references), None)
            if matched:
                target = references[canonical_url(matched['url'], policy='document-url/2')]
                body += '<details data-english-wikipedia-example><summary>' + t('Measured example: an English Wikipedia source was returned') + '</summary><p>' + esc(case['query']) + '</p><p>' + t('Returned URL') + ': <a href="' + esc(matched['url']) + '">' + esc(matched['url']) + '</a></p><p>' + t('Reference URL') + ': <a href="' + esc(target) + '">' + esc(target) + '</a></p></details>'
                break
        body += '</div>'
    return body + '</figure>'


@dataclass(frozen=True)
class MetricKind:
    """How one benchmark's metric population is summarised on the comparison home page.

    Authored content selects a kind with ``metric_kind`` (default: the population
    id). Column 0 is the method; quality columns follow from index 1.
    """

    name: str
    headings: Callable  # (zh, cutoff, content) -> quality column headings
    measures: Callable  # (group, cases, quality_ids, cutoff, content) -> raw values, one per heading
    note: Callable  # (zh, cutoff, ids, quality_ids, priced) -> HTML before the table
    ratio_columns: frozenset = frozenset({1})  # shown as count/denominator · percent
    decimal_columns: Callable = lambda content: frozenset()  # shown with three decimals
    primary_column: Callable = lambda content: None
    quality_denominator: bool = False  # ratios use the qualified queries, not the whole population
    validate: Callable = lambda population, profile: None
    discovery: tuple | None = None  # (label, hit(case, row, profile) -> bool) for directed discovery


def _factual_headings(zh, cutoff, content):
    headings = [report_text("Reference Hit@{cutoff}", zh, cutoff=cutoff), report_text("Answer Match@{cutoff} (text)", zh, cutoff=cutoff)]
    if content.get('summary_metrics') == 'retrieval':
        headings.append(report_text("Reference MRR@{cutoff}", zh, cutoff=cutoff))
    return headings


def _factual_measures(group, cases, quality_ids, cutoff, content):
    quality_group = [r for r in group if r['case_id'] in quality_ids]
    measures = [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[f'labeled_source_hit_{cutoff}'] for r in quality_group), sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][answer_evidence_key(cases[r['case_id']], cutoff)] for r in quality_group)]
    if content.get("summary_metrics") == "retrieval":
        measures.append(sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[f'labeled_source_rr_{cutoff}'] for r in quality_group) / len(quality_group) if quality_group else None)
    return measures


def _factual_note(zh, cutoff, ids, quality_ids, priced):
    body = ''
    if quality_ids != ids:
        body += '<p data-quality-exclusions>' + (report_text("Quality uses {qualified} qualified queries; latency retains all {total} collected queries.", zh, qualified=len(quality_ids), total=len(ids)) if priced else report_text("Quality uses {qualified} reviewed-reference queries for every method; {defective} defective references are quarantined. Latency and cost retain all {total} queries.", zh, qualified=len(quality_ids), defective=len(ids) - len(quality_ids), total=len(ids))) + '</p>'
    return body + '<details class="metric-guide"><summary>' + report_text("Metric definitions", zh) + '</summary><p>' + report_text("Hit@{cutoff} checks for a published supporting URL in the first {cutoff} results. MRR@{cutoff} averages reciprocal first-hit rank, with misses scoring zero. Answer Match@{cutoff} checks delivered text for reference-answer phrases after excluding explicit dataset mirrors; it does not measure semantic accuracy.", zh, cutoff=cutoff) + '</p></details>'


def _factual_population(population, profile):
    if set(population["case_ids"]) != {c["id"] for c in profile["cases"] if "reference_answer" in c}:
        raise ValueError("Factual population must include every labeled question")


def _target_measures(group, cases, quality_ids, cutoff, content):
    hit_key = 'anchor_hit_5' if cutoff == 5 else 'anchor_hit_10'
    metrics = [evaluate(cases[r['case_id']], r['results'], cutoff=cutoff) for r in group]
    if any(m.get(hit_key) is None or m.get('reciprocal_rank') is None for m in metrics):
        raise ValueError('Scenario population requires a reference for every query')
    return [sum(m[hit_key] for m in metrics), sum(m['reciprocal_rank'] for m in metrics) / len(group)]


def _site_measures(group, cases, quality_ids, cutoff, content):
    eligible = []
    for row in group:
        value = row['metrics'].get('domain_precision')
        if value is None and row['metrics'].get('result_count') == 0:
            value = 0
        if value is None:
            raise ValueError('Site population has an invalid domain metric')
        eligible.append(value)
    return [sum(value > 0 for value in eligible), sum(eligible) / len(eligible)]


FACTUAL_KIND = MetricKind(
    name='factual', headings=_factual_headings, measures=_factual_measures, note=_factual_note,
    ratio_columns=frozenset({1, 2}), quality_denominator=True, validate=_factual_population,
    decimal_columns=lambda content: frozenset({3}) if content.get('summary_metrics') == 'retrieval' else frozenset(),
    primary_column=lambda content: 3 if content.get('summary_metrics') == 'retrieval' else None)
TARGET_KIND = MetricKind(
    name='scenario', measures=_target_measures,
    headings=lambda zh, cutoff, content: [report_text("Reference target found", zh, context="target page"), report_text("Target MRR", zh)],
    note=lambda zh, cutoff, ids, quality_ids, priced: '<p class="population-note">' + report_text("Target found: a reference page appears in the top {cutoff}. MRR averages reciprocal first-target rank: 1 for first, 0.5 for second, and 0 for a miss. Higher means targets appear earlier.", zh, cutoff=cutoff) + '</p>',
    decimal_columns=lambda content: frozenset({2}),
    discovery=("Find the given webpage", lambda case, row, profile: bool(evaluate(case, row['results'][:profile['protocol']['top_k']])['anchor_hit_10'])))
SITE_KIND = MetricKind(
    name='site', measures=_site_measures,
    headings=lambda zh, cutoff, content: [report_text("Site constraint met", zh), report_text("Requested-domain fraction", zh)],
    note=lambda zh, cutoff, ids, quality_ids, priced: '<p class="population-note">' + report_text("Site satisfied: at least one top-{cutoff} result uses the requested domain. Domain fraction averages the share of top-{cutoff} results from that domain. Neither metric judges page relevance.", zh, cutoff=cutoff) + '</p>',
    discovery=("Find the requested site", lambda case, row, profile: (row['metrics'].get('domain_precision') or 0) > 0))
