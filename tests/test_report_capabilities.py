import json
import tempfile
import unittest
from pathlib import Path
from harness.web_fetch.report import table, cell, link
from harness.web_fetch.report_methods import PROFILES
from harness.web_fetch.report_capabilities import CAPABILITIES, SOURCES, render_capabilities
from harness.web_fetch.report_model import RenderContext


class CapabilityTests(unittest.TestCase):
    def test_native_cohort_discloses_native_document_output_without_external_processing(self):
        from harness.web_fetch.report_capabilities import task_capabilities
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            (out/'dataset.json').write_text(json.dumps({'cases':[
                {'id':'body','url':'https://example.com/','track':'web','task_kind':'body'},
                {'id':'pdf','url':'https://example.com/a.pdf','track':'document','task_kind':'body'}]}), encoding='utf-8')
            names=['moli','lightpanda','obscura','chromium','kitesurf']
            original_sources = dict(SOURCES)
            claims=task_capabilities(out,names,[{'provider_metadata':{'output_contract':'readable_document_text'}}], ctx=RenderContext())
            self.assertEqual(SOURCES, original_sources)
            self.assertEqual(claims['moli']['document']['status'],'有限支持')
            self.assertIn('直接 PDF/DOCX 未提供可读正文',claims['moli']['document']['detail'])
            for name in ('lightpanda','obscura','kitesurf'):
                self.assertEqual(claims[name]['document']['status'],'原生输出')
            self.assertEqual(claims['chromium']['document']['status'],'共同转换')
            for name in ('moli','lightpanda','obscura','kitesurf'):
                self.assertIn('网页内嵌媒体与正文统一走原生 Markdown',claims[name]['notes'])
            self.assertIn('固定标准包',claims['chromium']['notes'])
            for name in names:
                self.assertIn('不使用外部',claims[name]['document']['detail'])
                self.assertNotIn('没有 PDF / Office 解析器',claims[name]['document']['detail'])
            self.assertIn('官方 JS 实现',claims['obscura']['scope'])
            self.assertIn('原生 Markdown',claims['moli']['scope'])
            self.assertIn('browser=kitesurf',claims['kitesurf']['scope'])
            self.assertEqual(CAPABILITIES['lightpanda']['document']['status'],'未声明')
            self.assertIsNone(task_capabilities(out,['firecrawl'],[{'provider_metadata':{'output_contract':'markdown'}}], ctx=RenderContext()))

    def test_all_evaluated_interfaces_have_scoped_evidence(self):
        self.assertTrue(set(PROFILES) <= set(CAPABILITIES))
        for name, row in CAPABILITIES.items():
            self.assertTrue(row['scope'], name)
            self.assertTrue(row['tested'], name)
            self.assertTrue(row['sources'], name)
            for source in row['sources']:
                self.assertTrue(SOURCES[source][1].startswith('https://'))
            for field in ['web', 'document', 'image', 'av', 'file', 'js']:
                self.assertIn(row[field]['status'], ['支持', '需配置', '仅链接', '有限支持', '无此能力', '未声明'])
                self.assertTrue(row[field]['detail'])

    def test_render_has_every_evidence_target_and_download(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'adoption').mkdir()
            result = render_capabilities(out, sorted(PROFILES), table=table, cell=cell, link=link, ctx=RenderContext())
            for name in PROFILES:
                self.assertIn('href="#capability-'+name+'"', result)
                self.assertIn('id="capability-'+name+'"', result)
            self.assertIn('内容与资源', result)
            self.assertIn('运行与接入', result)
            self.assertNotIn('sortTable(', result)
            self.assertIn('不是实测成绩', result)
            exported = json.loads((out/'adoption/capabilities.json').read_text(encoding='utf-8'))
            self.assertEqual(set(exported['methods']), set(PROFILES))

    def test_unknown_provider_is_disclosed_instead_of_silently_omitting(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'adoption').mkdir()
            result = render_capabilities(out, ['new-provider'], table=table, cell=cell, link=link, ctx=RenderContext())
            self.assertIn('尚无已核对的官方能力资料：new-provider', result)


def test_source_version_mismatch_is_visible_and_exported(tmp_path):
    from harness.web_fetch.report_i18n import localize_html
    (tmp_path/'adoption').mkdir()
    record = {'provider': 'moli-webfetch', 'provider_metadata': {'reported_version': 'moli 9.9.9'}}
    html = render_capabilities(tmp_path, ['moli-webfetch'], table=table, cell=cell, link=link,
                               records=[record], ctx=RenderContext())
    exported = json.loads((tmp_path/'adoption/capabilities.json').read_text(encoding='utf-8'))
    checks = exported['source_version_checks']['moli-webfetch']
    assert checks
    assert all(row['status'] == 'mismatch' for row in checks)
    assert all(row['checked_version'] == '1.1.4' for row in checks)
    assert '核对版本 1.1.4；实测版本 9.9.9' in html
    from pathlib import PurePosixPath
    english = localize_html(html, PurePosixPath('adoption/index.html'))
    assert 'checked version 1.1.4; measured version 9.9.9' in english
    assert '/blob/v1.1.4/' in english  # Preserve the actual source binding.


def test_source_version_controls_preserve_unknown_and_mixed_versions():
    from harness.web_fetch.report_capabilities import source_version_checks
    def statuses(versions):
        records = [{'provider': 'moli-webfetch', 'provider_metadata': {
            'reported_version': version}} for version in versions]
        return {row['status'] for row in source_version_checks(['moli-webfetch'], records, ctx=RenderContext())['moli-webfetch']}
    assert statuses(['MOLI 1.1.4']) == {'matched'}
    assert statuses(['moli 1.1.4', 'moli 9.9.9']) == {'mismatch'}
    assert statuses(['moli 1.1.4-build.1']) == {'mismatch'}
    assert statuses(['moli 1.1.4', None]) == {'unverified'}
    assert statuses([]) == {'unverified'}


def test_native_capabilities_retain_version_bound_sources(tmp_path):
    (tmp_path/'adoption').mkdir()
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [
        {'id': 'web', 'track': 'web', 'url': 'https://example.test/'}]}), encoding='utf-8')
    records = [{'provider': 'moli', 'case_id': 'web', 'provider_metadata': {
        'reported_version': 'moli 9.9.9', 'output_contract': 'markdown'}}]
    html = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link,
                               records=records, ctx=RenderContext())
    assert '核对版本 1.1.5；实测版本 9.9.9' in html
    evidence = json.loads((tmp_path/'adoption/capabilities.json').read_text(encoding='utf-8'))
    assert 'headless-moli-1.1.5' in evidence['methods']['moli']['sources']
    assert 'task-native-moli' in evidence['methods']['moli']['sources']


def test_capability_fields_render_english_without_catalog_reverse_lookup(tmp_path, monkeypatch):
    from pathlib import PurePosixPath
    from harness.web_fetch.methods import report_method_catalog
    from harness.web_fetch.report_i18n import localize_html
    (tmp_path/'adoption').mkdir()
    page = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link, ctx=RenderContext())
    english = localize_html(page, PurePosixPath('adoption/index.html'))
    from html import unescape
    english = unescape(english)
    registered = report_method_catalog('en')['methods']['moli']['capabilities']
    assert registered['notes'] in english
    assert registered['web']['detail'] in english
    assert registered['tested'] in english
    assert 'Official capability documentation is not a measured result.' in english
    assert '官方能力资料不是实测成绩' not in english


def test_source_notice_distinguishes_missing_version_from_mismatch():
    from harness.web_fetch.report_capabilities import render_source_version_notice, source_version_checks
    def notice(metadata):
        checks = source_version_checks(['moli-webfetch'], [{'provider': 'moli-webfetch', 'provider_metadata': metadata}], ctx=RenderContext())
        return render_source_version_notice(checks['moli-webfetch'])
    missing = notice({})
    assert '资料适用版本未确认' in missing
    assert 'Source applicability is unverified' in missing
    mismatch = notice({'version': '9.9.9'})
    assert '资料版本与实测版本不一致' in mismatch
    assert 'Source and measured versions differ' in mismatch


def test_configured_capability_legend_is_fully_localized(tmp_path):
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html, untranslated_english_narrative
    (tmp_path/'adoption').mkdir()
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [
        {'id': 'web', 'track': 'web', 'url': 'https://example.test/'}]}), encoding='utf-8')
    records = [{'provider': 'moli', 'case_id': 'web', 'provider_metadata': {
        'reported_version': 'moli 9.9.9', 'output_contract': 'markdown'}}]
    page = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link, records=records, ctx=RenderContext())
    english = localize_html(page, PurePosixPath('adoption/index.html'))
    assert 'configured interfaces and processing boundaries' in english
    assert not untranslated_english_narrative(english)
