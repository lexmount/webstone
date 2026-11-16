"""Saved reports expose bound findings alongside record-derived method totals."""
import json

from harness.web_fetch.report_explainer import render_explainer


def test_saved_findings_keep_content_and_derive_counts(tmp_path):
    cases = {'one': {'id': 'one', 'task_kind': 'body'}}
    records = [{'provider': 'sample-method', 'case_id': 'one', 'ok': True,
                'metrics': {'content_pass': False}}]
    (tmp_path / 'dataset.json').write_text(json.dumps({'saved_matrix_scope': {
        'providers': [{'provider': 'sample-method', 'passed': 99}],
        'limitations': ['Saved body omitted the list. <script>bad</script>']}}), encoding='utf-8')
    from harness.web_fetch.report import _REPORT_PROVIDER_LABELS
    token = _REPORT_PROVIDER_LABELS.set({'sample-method': 'Saved participant'})
    try:
        page = render_explainer(tmp_path, cases, records, authored=False)
    finally:
        _REPORT_PROVIDER_LABELS.reset(token)
    assert 'Saved participant' in page
    assert '&lt;span' not in page
    assert '<td>1</td><td>0</td><td>0.0%</td>' in page
    assert '<td>99</td>' not in page
    assert 'Saved body omitted the list. &lt;script&gt;bad&lt;/script&gt;' in page
    assert '<script>' not in page
    assert '../review/index.html' in page
    from harness.web_fetch.report_i18n import TEXT
    assert 'aria-label="本轮方法对比"' in page
    assert TEXT['本轮方法对比'] == 'Method comparison for this round'


def test_unbound_report_does_not_invent_saved_findings(tmp_path):
    page = render_explainer(tmp_path, {}, [], authored=False)
    assert '../review/index.html' not in page
    assert '0/0' in page


def test_saved_failure_chapters_cover_every_method_and_failure_with_exact_evidence(tmp_path):
    from copy import deepcopy
    from bs4 import BeautifulSoup
    cases = {name: {'id': name, 'task_kind': 'body', 'url': 'https://example.com/'+name}
             for name in ['one', 'two', 'three']}
    records = [{'provider': provider, 'case_id': name, 'attempt_id': provider+'--'+name,
        'ok': True, 'metrics': {'content_pass': name == 'three'},
        'diagnosis': None if name == 'three' else {
            'category': 'Missing <body>', 'evidence': 'The retained response omitted the body.'}}
        for provider in ['first', 'second'] for name in cases]
    before = deepcopy(records)
    (tmp_path/'dataset.json').write_text(json.dumps({'saved_matrix_scope': {'limitations': []}}), encoding='utf-8')
    soup = BeautifulSoup(render_explainer(tmp_path, cases, records, authored=False), 'html.parser')
    chapters = soup.select('[data-method-failures]')
    assert [node['data-method-failures'] for node in chapters] == ['first', 'second']
    for provider, chapter in zip(['first', 'second'], chapters):
        cells = [node.get_text() for node in chapter.select('tbody tr td')]
        assert cells[1:4] == ['Missing <body>', '2', '100.0%']
        assert chapter.select_one('a')['href'] == '../attempts/'+provider+'--one.html'
        assert 'The retained response omitted the body.' in chapter.get_text()
    assert records == before
