"""Layered tables separate observed extrema, statistical support and absolute results."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_search.comparison import render_level_summary
from harness.web_search.output_levels import level_benchmarks
from lexbench_websearch.scoring import evaluate


def test_level_latency_uses_all_frozen_rounds_while_quality_stays_on_score_round():
    from copy import deepcopy

    selected = view(n=2)
    selected['timing_rows'] = deepcopy(selected['rows'])
    for row in selected['rows']:
        selected['timing_rows'].append(dict(row, elapsed_ms=900, round=1))
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    values = {row['data-summary-method']: row for row in doc.select('tbody tr[data-summary-method]')}
    assert float(values['a'].select_one('[data-metric$="-mean_ms"]')['data-sort']) == 500
    assert float(values['b'].select_one('[data-metric$="-mean_ms"]')['data-sort']) == 550
    assert len(selected['benchmarks']['factual']['scores']) == 4


def test_collection_context_is_available_without_expanding_comparison_rows():
    selected = view(n=2)
    selected['profile']['method_collections'] = [dict(id='old', methods=['a', 'b'],
        started_at='2026-11-13T10:00:00Z', protocol={'concurrency': 4})]
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    contexts = doc.select('details.measurement-context')
    assert len(contexts) == 2
    assert all(not node.has_attr('open') for node in contexts)
    assert all('2026-11-13' in node.get_text() and 'concurrency 4' in node.get_text() for node in contexts)


def test_context_size_counts_titles_when_the_provider_has_no_primary_excerpt():
    selected = view(n=2)
    for row in selected['rows']:
        row['results'][0].update(title='Only a title', snippet='')
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    values = [float(cell['data-sort']) for cell in doc.select('[data-metric$="-context_characters"]')]
    assert values == [len('Only a title')] * 2


def view(n=40, single=False):
    cases = [
        dict(
            id="q" + str(i),
            query="Who wrote it?",
            family="web",
            reference_answer="needle",
            reference_urls=["https://source.test/" + str(i)],
            evaluation_level="l1",
        )
        for i in range(n)
    ]
    methods = [
        dict(id=mid, label="Method " + mid.upper(), currency="USD", price_per_1000=1)
        for mid in (["a"] if single else ["b", "a"])
    ]
    profile = dict(cases=cases, methods=methods, protocol={"top_k": 5, "relevance_level": "l1"})
    rows = []
    for case in cases:
        for method in methods:
            hit = method["id"] == "a"
            results = [
                dict(
                    url=case["reference_urls"][0] if hit else "https://other.test/",
                    title="needle" if hit else "Unrelated",
                    snippet="Source excerpt",
                )
            ]
            rows.append(
                dict(
                    case_id=case["id"],
                    method=method["id"],
                    results=results,
                    cost=0.001,
                    http_status=200,
                    error="",
                    elapsed_ms=100 if hit else 200,
                    metrics=evaluate(case, results),
                )
            )
    output = dict(profile=profile, rows=rows)
    output["benchmarks"] = level_benchmarks(output)
    return output


def guide_views():
    from copy import deepcopy

    output = {}
    base = view(n=2)
    scene = dict(base['profile']['cases'][0], id='scene')
    del scene['reference_answer']
    base['profile']['cases'].append(scene)
    base['rows'] += [dict(deepcopy(row), case_id='scene') for row in base['rows'][:2]]
    base['relevance_scores'] = [dict(case_id='scene', method=m['id'], ndcg=.5, precision=.2, success=1)
                                for m in base['profile']['methods']]
    for level in ('l1', 'l2', 'l3'):
        selected = deepcopy(base)
        selected['profile']['protocol']['relevance_level'] = level
        for case in selected['profile']['cases']:
            case['evaluation_level'] = level
        if level == 'l2':
            for row in selected['rows']:
                case = next(c for c in selected['profile']['cases'] if c['id'] == row['case_id'])
                row['metrics'] = evaluate(case, row['results'])
        if level == 'l3':
            selected['profile']['methods'] = [m for m in selected['profile']['methods'] if m['id'] == 'a']
            selected['rows'] = [r for r in selected['rows'] if r['method'] == 'a']
            selected['answer_scores'] = [dict(case_id=r['case_id'], method='a', factual_grade='CORRECT', answer_grade=2,
                citation_count=0, supported_citations=0, citations_in_results=0) for r in selected['rows']]
        selected['benchmarks'] = level_benchmarks(selected)
        output[level] = selected
    return output


@pytest.mark.parametrize('zh', [False, True])
def test_output_guide_distinguishes_fields_tasks_and_actual_participants(zh):
    from harness.web_search.comparison import render_level_guide

    views = guide_views()
    doc = BeautifulSoup(render_level_guide(views, zh), 'html.parser')
    assert [r['data-level-guide-row'] for r in doc.select('tbody tr')] == ['l1', 'l2', 'l3']
    assert len(doc.select('thead th')) == 3
    assert [len(doc.select(f'[data-level-participants="{level}"] li')) for level in views] == [2, 2, 1]
    expected = {'l1': ('ndcg', 'source_rr'), 'l2': ('ndcg', 'answer_match'), 'l3': ('answer_grade', 'correct')}
    for level, metrics in expected.items():
        row = doc.select_one(f'[data-level-guide-row="{level}"]')
        assert [n['data-guide-primary'] for n in row.select('[data-guide-primary]')] == list(metrics)
    assert 'Method A' in doc.select_one('[data-shared-level-methods]').get_text()
    assert 'Method B' not in doc.select_one('[data-shared-level-methods]').get_text()
    assert doc.select_one('[data-citation-scope]')
    l2 = doc.select_one('[data-level-guide-row="l2"]')
    assert ('不查标题' if zh else 'excludes titles') in l2.select_one('[data-level-test="factual"]').get_text()
    assert ('标题' if zh else 'Titles') in l2.select_one('[data-level-input]').get_text()


def test_output_guide_uses_the_selected_primary_metric_and_available_levels():
    from harness.web_search.comparison import render_level_guide

    views = guide_views()
    views['l1']['benchmarks']['factual']['primary_metric'] = 'source_hit'
    doc = BeautifulSoup(render_level_guide(views), 'html.parser')
    assert doc.select_one('[data-level-guide-row="l1"] [data-level-test="factual"] [data-guide-primary]')['data-guide-primary'] == 'source_hit'
    del views['l3']
    doc = BeautifulSoup(render_level_guide(views), 'html.parser')
    assert not doc.select('[data-shared-level-methods], [data-citation-scope], [data-level-guide-row="l3"]')


@pytest.mark.parametrize("zh", [False, True])
def test_layer_table_has_correct_sort_order_raw_highlights_and_supported_stars(zh):
    doc = check_presentation(render_level_summary(view(), zh))
    table = doc.select_one("table")
    assert table.select_one("th[data-primary-metric]")["aria-sort"] == "descending"
    assert [r["data-summary-method"] for r in table.select("tbody tr")] == ["a", "b"]
    first = table.select("tbody tr")[0]
    for key in ["source_rr", "source_hit", "answer_match", "mean_ms", "p50_ms", "p95_ms"]:
        assert first.select_one('[data-metric$="-' + key + '"] .comparison-extremum') is not None
    assert first.select_one('[data-metric$="-source_rr"] .comparison-winner')
    assert not first.select_one('[data-metric$="-context_characters"] .comparison-extremum')
    assert not doc.select_one('[data-level-summary-filter]')
    assert doc.select_one('.scroll.inventory[tabindex="0"]')


def test_small_subgroups_keep_observed_highlights_without_stars():
    doc = check_presentation(render_level_summary(view(n=20)))
    assert doc.select(".comparison-extremum")
    assert not doc.select(".comparison-winner")


def test_one_participant_has_absolute_results_without_winner_styling():
    doc = check_presentation(render_level_summary(view(single=True)))
    assert not doc.select(".comparison-winner, .comparison-extremum")
    assert len(doc.select("tbody tr")) == 1


@pytest.mark.parametrize(
    "damage", ["missing-extremum", "wrong-extremum", "missing-star", "unsupported-star"]
)
def test_shared_gate_rejects_wrong_raw_highlights_or_statistical_stars(damage):
    doc = BeautifulSoup(
        render_level_summary(view(n=20 if damage == "unsupported-star" else 40)), "html.parser"
    )
    cells = doc.select('[data-metric$="-source_rr"]')
    if damage == "missing-extremum":
        cells[0].select_one(".comparison-extremum").unwrap()
    elif damage == "wrong-extremum":
        cells[1].append(cells[0].select_one(".comparison-extremum").extract())
    elif damage == "missing-star":
        cells[0].select_one(".comparison-winner").decompose()
    else:
        marker = doc.new_tag("span", attrs={"class": "comparison-winner"})
        marker.string = "★"
        cells[0].append(marker)
    with pytest.raises(ValueError):
        check_presentation(doc)


def test_close_observed_leader_stays_green_without_a_significance_star():
    output = view(n=149)
    cases = {c["id"]: c for c in output["profile"]["cases"]}
    for row in output["rows"]:
        cid = row["case_id"]
        hit = (int(cid[1:]) % 2 == 0) == (row["method"] == "a")
        row["results"][0].update(
            url=cases[cid]["reference_urls"][0] if hit else "https://other.test/",
            title="needle" if hit else "Unrelated",
        )
        row["metrics"] = evaluate(cases[cid], row["results"])
    output["benchmarks"] = level_benchmarks(output)
    doc = check_presentation(render_level_summary(output))
    cells = doc.select('[data-metric$="-source_rr"]')
    assert cells[0].select_one(".comparison-extremum") is not None
    assert not doc.select(".comparison-winner")


def test_factual_chart_counts_joint_outcomes_and_shows_measured_english_sources():
    from harness.web_search.comparison import factual_outcome_chart

    output = view()
    case = output["profile"]["cases"][0]
    case["reference_urls"] = ["https://en.wikipedia.org/wiki/Example"]
    case["url_equivalence"] = "document-url/2"
    by_case = {c["id"]: c for c in output["profile"]["cases"]}
    for row in output["rows"]:
        if row["case_id"] == case["id"] and row["method"] == "a":
            row["results"][0]["url"] = "https://en.wikipedia.com/wiki/Example"
        if row["case_id"] == "q1" and row["method"] == "b":
            row["results"][0]["title"] = "needle"
        if row["case_id"] == "q2" and row["method"] == "a":
            row["results"][0]["title"] = "An unrelated title"
        row["metrics"] = evaluate(by_case[row["case_id"]], row["results"])
    output["benchmarks"] = level_benchmarks(output)
    doc = BeautifulSoup(factual_outcome_chart(output), "html.parser")
    counts = {
        row["data-factual-outcome-method"]: [
            int(n["data-count"]) for n in row.select("[data-count]")
        ]
        for row in doc.select("[data-factual-outcome-method]")
    }
    assert counts == {"a": [39, 1, 0, 0], "b": [0, 0, 1, 39]}
    example = doc.select_one("[data-english-wikipedia-example]")
    assert [a["href"] for a in example.select("a")] == [
        "https://en.wikipedia.com/wiki/Example",
        "https://en.wikipedia.org/wiki/Example",
    ]


def test_unavailable_factual_reference_coverage_has_no_zero_score_chart():
    from harness.web_search.comparison import factual_outcome_chart, source_matching_chart

    output = view()
    output["profile"]["factual_quality_exclusions"] = [c["id"] for c in output["profile"]["cases"]]
    output["benchmarks"] = level_benchmarks(output)
    assert factual_outcome_chart(output) == source_matching_chart(output) == ""
    doc = check_presentation(render_level_summary(output))
    assert not doc.select('[data-metric$="-source_rr"] .comparison-extremum')


def test_legacy_paired_table_cannot_claim_significance_for_two_queries():
    from harness.web_search.report import population_summary
    selected = view(n=2)
    profile, rows = selected['profile'], selected['rows']
    for row in rows:
        row['round'] = 0
    scores = [dict(case_id=r['case_id'], method=r['method'], ndcg=float(r['method']=='a'),
                   precision=float(r['method']=='a'), success=float(r['method']=='a')) for r in rows]
    content = dict(uncertainty='paired-query-bootstrap/1', metric_populations=[dict(
        id='overall', metric_kind='relevance', case_ids=[c['id'] for c in profile['cases']],
        label={'en':'Scenarios','zh':'Scenarios'})])
    doc = BeautifulSoup(population_summary(profile, rows, content, False, relevance_scores=scores), 'html.parser')
    table = doc.select_one('table')
    assert table['data-query-count'] == '2'
    assert table['data-significance'] == 'paired-query-bootstrap/1'
    assert not table.select('.comparison-winner')
    assert table.select('.comparison-extremum')


def test_locale_disclosure_counts_saved_requests_instead_of_one_method_default():
    from harness.web_search.comparison import locale_context

    rows = [dict(method='serper', request=dict(q='Question', num=5, gl='us', hl='en')) for _ in range(3)]
    rows += [dict(method='serper', request=dict(q='问题', num=5))]
    rows += [dict(method='bright', request={'url':'https://www.google.com/search?q=Question&gl=us&hl=en'}),
             dict(method='bright', request={'url':'https://www.google.com/search?q=Question'})]
    assert locale_context(rows, 'serper') == '1 calls: No locale parameters; 3 calls: gl=us, hl=en'
    assert locale_context(rows, 'bright', True) == '1 次调用：未设置地区、语言参数; 1 次调用：gl=us, hl=en'
    assert not locale_context(rows, 'other')


def test_l2_capabilities_show_verified_admission_and_count_unavailable_pages():
    from harness.web_search.comparison import render_level_capabilities
    from harness.web_search.output_levels import assessed_levels
    from tests.web_search.test_level_scores import complete_levels

    profile, rows, admissions, artifacts, evidence = complete_levels()
    for method in profile['methods']:
        method['label'] = method['id']
    views = assessed_levels(profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts)
    proof = next(iter(views['l2']['field_admissions'].values()))
    doc = BeautifulSoup(render_level_capabilities(views), 'html.parser')
    text = doc.select_one('[data-capability-level="l2"]').get_text(' ', strip=True)
    assert f'{proof["matched_fraction"]:.1%}' in text and str(proof['sample_size']) in text
    assert 'Unavailable pages' in text and '80%' in text and '50 accessible pages' in text
    views['l2']['field_admissions'].clear()
    with pytest.raises(ValueError, match='verified field admission'):
        render_level_capabilities(views)


def test_verified_mirrors_keep_publisher_scores_and_capture_failures_separate():
    selected = view(n=2)
    for case in selected['profile']['cases']:
        case['equivalent_reference_urls'] = ['https://copy.test/' + case['id']]
        case['reference_verification_coverage'] = dict(candidate_count=2, verified=1, capture_failed=1)
        case['capture_failed_reference_urls'] = ['https://other.test/']
    cases = {case['id']: case for case in selected['profile']['cases']}
    for row in selected['rows']:
        if row['method'] == 'a':
            row['results'][0]['url'] = cases[row['case_id']]['equivalent_reference_urls'][0]
        row['metrics'] = evaluate(cases[row['case_id']], row['results'])
    selected['benchmarks'] = level_benchmarks(selected)
    scores = selected['benchmarks']['factual']['scores']
    assert all(s['source_hit'] == 1 and s['publisher_hit'] == 0 for s in scores if s['method'] == 'a')
    assert all(s['capture_failed'] == 1 and s['source_hit'] == 0 for s in scores if s['method'] == 'b')
    doc = check_presentation(render_level_summary(selected))
    cells = doc.select('[data-summary-method="b"] [data-metric$="-capture_failed"]')
    assert len(cells) == 1 and cells[0].get_text(strip=True) == '2/2'
    assert not cells[0].select('.win, .best')


def test_request_failures_are_visible_beside_the_affected_method():
    selected = view(n=2)
    selected['rows'][0].update(http_status=503, error='Service unavailable')
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    affected = doc.select_one('[data-summary-method="' + selected['rows'][0]['method'] + '"]')
    assert affected.select_one('[data-delivery-limited]').get_text() == 'Delivery limited: 1/2 failed calls'


@pytest.mark.parametrize('damage', ['missing', 'count', 'audit-link'])
def test_report_gate_rejects_changed_reference_verification_coverage(tmp_path, damage):
    import json
    from harness.record_io import bytes_sha256
    from harness.web_search.example import example
    from harness.web_search.report import build, check

    example(tmp_path, comparison=True)
    source = tmp_path / 'fixture'
    profile = json.loads((source / 'profile.json').read_bytes())
    case = next(case for case in profile['cases'] if 'reference_answer' in case)
    case.update(reference_verification_coverage=dict(candidate_count=2, verified=1, title_mismatch=0,
        evidence_mismatch=0, capture_failed=1), reference_verification_audit_url='https://dataset.test/audit.json')
    (source / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
    receipt = json.loads((source / 'receipt.json').read_bytes())
    receipt['profile_sha256'] = bytes_sha256((source / 'profile.json').read_bytes())
    (source / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    output = tmp_path / 'updated'
    content = tmp_path / 'verification-content.json'
    content.write_text(json.dumps({'presentation': 'comparison-report', 'sections': []}), encoding='utf-8')
    build(output, source=source, content=content, language='zh')
    assert check(output)
    path = output / 'dataset.html'
    doc = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    section = doc.select_one('[data-reference-verification]')
    if damage == 'missing':
        section.decompose()
    elif damage == 'count':
        section.select_one('tbody td').string = '9999'
    else:
        section.select_one('a')['href'] = 'https://wrong.test/audit.json'
    path.write_text(str(doc), encoding='utf-8')
    with pytest.raises(ValueError, match='Dataset definitions'):
        check(output)


def test_mixed_reference_audits_do_not_declare_unavailable_panel_metrics():
    selected = view(n=2)
    selected['profile']['cases'][0].update(reference_verification_coverage=dict(verified=1),
                                          capture_failed_reference_urls=[])
    selected['benchmarks'] = level_benchmarks(selected)
    keys = {metric['key'] for metric in selected['benchmarks']['factual']['metrics']}
    assert not keys.intersection({'publisher_rr', 'publisher_hit', 'capture_failed'})
    assert all('capture_failed' not in score for score in selected['benchmarks']['factual']['scores'])
    assert check_presentation(render_level_summary(selected))
