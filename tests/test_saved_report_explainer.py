"""Saved reports expose bound findings alongside record-derived method totals."""
import json

from harness.web_fetch.report_explainer import render_explainer
from harness.web_fetch.report_model import RenderContext


def test_saved_findings_keep_content_and_derive_counts(tmp_path):
    cases = {'one': {'id': 'one', 'task_kind': 'body'}}
    records = [{'provider': 'sample-method', 'case_id': 'one', 'ok': True,
                'metrics': {'content_pass': False}}]
    (tmp_path / 'dataset.json').write_text(json.dumps({'saved_matrix_scope': {
        'providers': [{'provider': 'sample-method', 'passed': 99}],
        'limitations': ['Saved body omitted the list. <script>bad</script>']}}), encoding='utf-8')
    from harness.web_fetch.report_model import MethodScope, model_render_context, report_model
    model = report_model({'cases': list(cases.values())}, records,
                         participants=[{'id': 'sample-method', 'label': 'Saved participant'}])
    page = render_explainer(tmp_path, cases, records, authored=False,
                            ctx=model_render_context(model, methods=MethodScope()))
    assert 'Saved participant' in page
    assert '&lt;span' not in page
    from bs4 import BeautifulSoup
    assert [cell.get_text() for cell in BeautifulSoup(page,'html.parser').select('table')[0].select('tbody td')][-3:] == ['1','0','0.0%']
    assert '<td>99</td>' not in page
    assert 'Saved body omitted the list. &lt;script&gt;bad&lt;/script&gt;' in page
    assert '<script>' not in page
    assert '../review/index.html' in page
    from harness.web_fetch.report_i18n import TEXT
    assert 'aria-label="本轮方法对比"' in page
    assert TEXT['本轮方法对比'] == 'Method comparison for this round'


def test_unbound_report_does_not_invent_saved_findings(tmp_path):
    page = render_explainer(tmp_path, {}, [], authored=False, ctx=RenderContext())
    assert '../review/index.html' not in page
    assert '0/0' in page


def test_saved_failures_use_the_same_program_category_as_result_details(tmp_path):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report import failure_category
    case = {'id':'one', 'task_kind':'body', 'url':'https://example.test/'}
    record = {'provider':'api', 'provider_type':'lexmount', 'case_id':'one',
              'attempt_id':'api-one', 'ok':False, 'status_code':502,
              'error_type':'api_error', 'error_message':'proxy CONNECT returned HTTP 502',
              'metrics':{'content_pass':False}}
    (tmp_path/'dataset.json').write_text(json.dumps({'saved_matrix_scope':{
        'presentation':'audit', 'limitations':[]}}), encoding='utf-8')
    page = BeautifulSoup(render_explainer(tmp_path, {'one':case}, [record], authored=False,
                                         ctx=RenderContext()), 'html.parser')
    category = failure_category(record, case, ctx=RenderContext())
    assert category in page.select_one('[data-method-failures]').get_text()
    assert not page.select('.comparison-winner')


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
    soup = BeautifulSoup(render_explainer(tmp_path, cases, records, authored=False, ctx=RenderContext()), 'html.parser')
    chapters = soup.select('[data-method-failures]')
    assert [node['data-method-failures'] for node in chapters] == ['first', 'second']
    for provider, chapter in zip(['first', 'second'], chapters, strict=True):
        cells = [node.get_text() for node in chapter.select('tbody tr td')]
        assert cells[1:4] == ['Missing <body>', '2', '100.0%']
        assert chapter.select_one('a')['href'] == '../attempts/'+provider+'--one.html'
        assert 'The retained response omitted the body.' in chapter.get_text()
    assert records == before


def test_saved_guide_keeps_unmeasured_results_out_of_failures_and_rates(tmp_path):
    from bs4 import BeautifulSoup
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html, untranslated_english_narrative
    cases = {name: {'id': name, 'task_kind': 'body'} for name in ['pass', 'fail', 'unknown']}
    records = [{'provider': 'method', 'case_id': name, 'ok': True,
                'metrics': {'content_pass': name == 'pass'}} for name in cases]
    records[-1]['report_invocation_status'] = 'adapter_rejected'
    records += [{'provider': 'unmeasured', 'case_id': 'unknown', 'ok': True,
                 'metrics': {'acceptance_checks': {'body': None}}}]
    (tmp_path/'dataset.json').write_text(json.dumps({'saved_matrix_scope': {'limitations': []}}), encoding='utf-8')
    soup = BeautifulSoup(render_explainer(tmp_path, cases, records, authored=False, ctx=RenderContext()), 'html.parser')
    chapters = soup.select('[data-method-failures]')
    assert '未通过 1 题' in chapters[0].get_text()
    assert '2 条可判定结果通过 1 条' in soup.get_text()
    assert '2 条无法判定' in soup.get_text()
    assert '1 题无法判定' in chapters[0].get_text()
    assert [cell.get_text() for cell in chapters[0].select('tbody td')][2:4] == ['1', '100.0%']
    assert not chapters[1].select('tbody tr')
    rates = soup.select('[data-metric="saved-guide-pass-rate"]')
    assert rates[0]['data-sort'] == '0.5'
    assert rates[1].get_text() == '—' and not rates[1].has_attr('data-sort')
    english = localize_html(str(soup), PurePosixPath('guide/index.html'))
    assert 'Of 2 measurable results, 1 passed' in english
    assert 'Unavailable results: 1; excluded' in english
    assert untranslated_english_narrative(english) == []
