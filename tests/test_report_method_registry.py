"""New methods and executable versions must not require renderer changes."""
from harness.distribution import source_path

import copy
import json

import pytest

from harness.web_fetch.methods import (
    browser_method_label, is_browser_comparison, method_version,
    report_method_catalog, with_measured_method_labels,
)
from harness.web_fetch.methods import method_access
from harness.web_fetch.report_interfaces import method_spec, render_interfaces
from harness.web_fetch.report_model import MethodScope, RenderContext


def test_claude_setup_and_example_keep_credentials_on_the_remote_host():
    method = report_method_catalog('en')['methods']['claude-web-fetch']
    assert method['access']['env_vars'] == ['ANTHROPIC_API_KEY']
    assert 'only' in method['access']['configuration']
    assert 'SSH host' in method['access']['configuration']
    interface = method_spec('claude-web-fetch', {'provider_type':'claude_web_fetch'}, language='en', ctx=RenderContext())
    assert interface['entry'].startswith('SSH')
    assert interface['request']['model']
    assert interface['request']['tool_choice'] == {'type':'tool','name':'web_fetch'}
    assert interface['request']['tools'][0]['allowed_callers'] == ['direct']
    assert 'no local API fallback' in interface['notes']
    assert 'Model input limits' in interface['notes']
    for name in ('kimi-fetch','zai-reader'):
        assert method_spec(name, None, language='en', ctx=RenderContext())['request'] == {'url':'https://example.com/'}


@pytest.fixture
def custom_catalog(monkeypatch):
    from harness.web_fetch.methods import _raw_report_method_catalog, _catalog_value
    catalog = copy.deepcopy(_raw_report_method_catalog())
    catalog['methods']['acme-fetch'] = {
        'label': {'zh': 'Acme Fetch', 'en': 'Acme Fetch'},
        'kind': 'browser', 'label_template': '{label} {version}',
        'source_url': 'https://example.com/acme', 'adapter_types': ['acme'],
        'profile': {'kind': 'Local CLI', 'description': 'A registered synthetic browser.'},
        'access': {'account': 'No account', 'configuration': 'Install the bound binary.',
                   'env_vars': ['ACME_BINARY'], 'links': [['Source', 'https://example.com/acme']]},
        'interface': {'entry': 'acme fetch URL', 'auth': 'Local binary',
                      'params': [['url', 'string', 'Target URL']],
                      'fields': [['text', 'string', 'Returned text']],
                      'request': {'url': 'https://example.com/'},
                      'source': 'https://example.com/acme', 'notes': 'Synthetic registration.'},
    }
    monkeypatch.setattr('harness.web_fetch.methods.report_method_catalog', lambda language="zh": _catalog_value(catalog, language=language))
    monkeypatch.setattr('harness.web_fetch.report_interfaces.report_method_catalog', lambda language="zh": _catalog_value(catalog, language=language))
    return catalog


def test_new_method_uses_one_registration_in_all_method_views(tmp_path, custom_catalog):
    from harness.web_fetch.report_methods import render_methods
    from harness.reporting.presentation import esc
    from harness.web_fetch.report import hero, section, table, cell, link
    (tmp_path / 'answer.txt').write_text('Synthetic answer', encoding='utf-8')
    (tmp_path / 'dataset.json').write_text('{}', encoding='utf-8')
    records = [{'provider': 'acme-fetch', 'provider_type': 'acme', 'case_id': 'new-task',
                'artifact_path': 'answer.txt', 'attempt_id': 'acme-attempt',
                'provider_metadata': {'reported_version': '7.8.9'}}]

    @with_measured_method_labels
    def render(out, dataset, rows, *, scope):
        assert browser_method_label('acme-fetch', scope=scope) == 'Acme Fetch 7.8.9'
        assert is_browser_comparison(rows, scope=scope)
        assert method_access('acme-fetch', rows, scope=scope)['env_vars'] == ['ACME_BINARY']
        return render_methods(out, rows, hero=hero, section=section, table=table,
                              cell=cell, link=link, esc=esc, ctx=RenderContext(methods=scope))
    body = render(tmp_path, {}, records)
    assert 'A registered synthetic browser.' in body
    assert 'acme fetch URL' in body
    assert 'Acme Fetch 7.8.9' in body
    assert 'ACME_BINARY' in body
    saved = json.loads((tmp_path / 'adoption/interfaces/acme-fetch.json').read_text(encoding='utf-8'))
    assert saved['interface']['entry'] == 'acme fetch URL'


def test_unknown_method_retains_output_and_discloses_missing_registration(tmp_path):
    (tmp_path / 'adoption').mkdir()
    (tmp_path / 'answer.txt').write_text('Retained unknown output', encoding='utf-8')
    row = {'provider': 'unknown-vendor', 'provider_type': 'unknown', 'case_id': 'unknown-task',
           'artifact_path': 'answer.txt', 'attempt_id': 'unknown-attempt'}
    body = render_interfaces(tmp_path, [row], ctx=RenderContext())
    assert 'Not registered' in body
    assert 'Retained unknown output' in body
    saved = json.loads((tmp_path / 'adoption/interfaces/unknown-vendor.json').read_text(encoding='utf-8'))
    assert saved['interface']['entry'] == '未登记'
    assert method_spec('unknown-vendor', row, language='en', ctx=RenderContext())['entry'] == 'Not registered'
    assert method_spec('unknown-vendor', row, ctx=RenderContext())['request'] == {}


@pytest.mark.parametrize('name,engine,version,expected', [
    ('new-moli', 'moli', 'moli 9.9.9', 'Moli 9.9.9'),
    ('new-browser', 'acme-fetch', '7.8.9', 'Acme Fetch 7.8.9'),
])
def test_recorded_versions_win_over_provenance_and_never_leak_between_reports(
        tmp_path, custom_catalog, name, engine, version, expected):
    row = {'provider': name, 'provider_metadata': {
        'engine': engine, 'binary_sha256': 'a' * 64, 'reported_version': version}}
    provenance = {'a' * 64: {'version': 'older'}}
    (tmp_path / 'implementation-provenance.json').write_text(json.dumps({
        'implementations': [{'binary_sha256': 'a' * 64, 'version': 'older'}]}), encoding='utf-8')
    assert method_version(row, provenance, scope=MethodScope()) == version

    @with_measured_method_labels
    def render(out, dataset, rows, *, scope):
        return browser_method_label(name, scope=scope)
    before = browser_method_label(name, scope=MethodScope())
    assert render(tmp_path, {}, [row]) == expected
    assert browser_method_label(name, scope=MethodScope()) == before
    row['provider_metadata'].pop('reported_version')
    assert render(tmp_path, {}, [row]).endswith(' older')


def test_native_compatibility_version_is_not_published_for_a_renamed_engine():
    record = {'provider': 'new-native-run', 'provider_metadata': {
        'engine': 'moli', 'browser_version': '150.0.0.0'}}
    assert method_version(record, scope=MethodScope()) is None


def test_diagnostic_method_view_uses_executable_version_before_cdp_version(tmp_path):
    from harness.web_fetch.report_methods import render_methods
    from harness.reporting.presentation import esc
    from harness.web_fetch.report import hero, section, table, cell, link
    row = {'provider': 'new-moli', 'provider_type': 'headless_markdown',
           'provider_metadata': {'engine': 'moli', 'reported_version': 'moli 9.9.9',
                                 'browser_version': '150.0.0.0'}}
    body = render_methods(tmp_path, [row], hero=hero, section=section, table=table,
                          cell=cell, link=link, esc=esc, authored=False, ctx=RenderContext())
    assert 'moli 9.9.9' in body
    assert '150.0.0.0' not in body


def test_method_catalog_rejects_unsupported_language():
    with pytest.raises(ValueError, match='Unsupported method catalog language'):
        report_method_catalog('fr')


@pytest.mark.parametrize('version,release', [
    ('MOLI 9.9.9', True), ('9.9.9', True), ('moli 9.9.9-build.17', False),
])
def test_release_links_and_labels_share_normalization(version, release):
    from harness.web_fetch.methods import report_method, versioned_method_label
    row = {'provider': 'moli', 'provider_metadata': {'reported_version': version}}
    access = method_access('moli', [row], scope=MethodScope())
    label = versioned_method_label(report_method('moli', scope=MethodScope()), 'Moli', version)
    assert label.startswith('Moli 9.9.9')
    assert ('/releases/tag/v9.9.9' in access['links'][0][1]) is release
    assert method_version(row, scope=MethodScope()) == version  # Raw evidence is never rewritten.


def test_ambiguous_transport_requires_recorded_engine(custom_catalog):
    from harness.web_fetch.methods import report_method
    custom_catalog['methods']['other-acme'] = {
        'label': {'zh': 'Other', 'en': 'Other'}, 'adapter_types': ['acme']}
    row = {'provider_type': 'acme'}
    with pytest.raises(ValueError, match='Ambiguous report method'):
        report_method('unregistered-run', row, scope=MethodScope())
    row['provider_metadata'] = {'engine': 'other-acme'}
    assert report_method('unregistered-run', row, scope=MethodScope())['label']['en'] == 'Other'


@pytest.mark.parametrize('label,channel,expected_candidate', [
    ('Acme candidate build', None, False),
    ('Acme preview', 'candidate', True),
    ('Acme candidate build', 'stable', False),
])
def test_candidate_status_is_explicit_not_guessed_from_label(
        tmp_path, custom_catalog, monkeypatch, label, channel, expected_candidate):
    monkeypatch.setattr('harness.web_fetch.methods.browser_method_registry', lambda *, scope: {
        'acme-run': {'engine': 'acme-fetch', 'label': label, 'release_channel': channel}})
    rows = [{'provider': 'acme-run', 'provider_metadata': {
        'reported_version': version}} for version in ['9.9.9-build.a', '9.9.9-build.b']]

    @with_measured_method_labels
    def render(out, dataset, records, *, scope):
        return browser_method_label('acme-run', scope=scope)
    assert ('候选版' in render(tmp_path, {}, rows)) is expected_candidate


def test_json_registered_route_and_interface_variant_need_no_engine_branch(custom_catalog):
    from harness.web_fetch.report_interfaces import _observed_route
    method = custom_catalog['methods']['acme-fetch']
    method['observed_routes'] = [{
        'metadata_equals': {'extractor': 'acme-native'},
        'text': 'Acme native Markdown{format}',
        'fragments': {'format': [{'metadata_equals': {'native_metadata.format': 'acme-json'},
                                  'text': ' via JSON'}]}}]
    method['interface_variants'] = [{
        'metadata_equals': {'native_metadata.format': 'acme-json'},
        'command': 'acme fetch --json URL', 'notes': 'Native JSON output.'}]
    metadata = {'engine': 'acme-fetch', 'extractor': 'acme-native',
                'native_metadata': {'format': 'acme-json', 'api_token': 'secret-canary'}}
    assert _observed_route('acme-fetch', metadata, ctx=RenderContext()) == 'Acme native Markdown via JSON'
    spec = method_spec('acme-run', {'provider_metadata': metadata}, ctx=RenderContext())
    assert spec['request']['command'] == 'acme fetch --json URL'
    assert 'secret-canary' not in json.dumps(spec)
    metadata['extractor'] = 'unknown'
    assert 'Acme native Markdown' not in _observed_route('acme-fetch', metadata, ctx=RenderContext())
    metadata['native_metadata']['format'] = 'unsupported'
    assert 'command' not in method_spec('acme-run', {'provider_metadata': metadata}, ctx=RenderContext())['request']


def test_new_engine_matrix_uses_registered_plan_and_recorded_wait(custom_catalog):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    method = custom_catalog['methods']['acme-fetch']
    method['routes'] = {'capabilities': 'Native text', 'markdown': 'Acme text'}
    method['task_interfaces'] = [{'case_equals': {}, 'description': '{markdown}', 'wait': 'ready'}]
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    planned = browser_task_interface_matrix(cases, ['acme-fetch'], ctx=RenderContext())
    assert planned['rows'][0]['cells']['acme-fetch'] == [
        {'count': 1, 'mode': 'markdown', 'description': 'Acme text', 'wait': 'ready'}]
    records = [{'provider': 'renamed-run', 'case_id': 'body',
                'provider_metadata': {'engine': 'acme-fetch', 'wait_until': 'observed-ready'}}]
    measured = browser_task_interface_matrix(cases, ['renamed-run'], records, ctx=RenderContext())
    part = measured['rows'][0]['cells']['renamed-run'][0]
    assert part['wait'] == '输出完成条件 observed-ready'
    assert 'Acme text' not in part['description']  # No observed extractor was recorded.
    records[0]['provider_metadata'].pop('wait_until')
    assert '未提供' in browser_task_interface_matrix(
        cases, ['renamed-run'], records, ctx=RenderContext())['rows'][0]['cells']['renamed-run'][0]['wait']


def test_registered_task_modes_match_public_execution_selection():
    from types import SimpleNamespace
    from harness.web_fetch.providers.headless import output_mode_for_case
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {f'{track}-{kind}': {'id': f'{track}-{kind}', 'track': track,
        'task_kind': kind, 'url': 'https://example.test/document.pdf'}
        for track in ['web', 'dynamic', 'document', 'image', 'video', 'challenge', 'risk_control']
        for kind in ['body', 'resource', 'file']}
    from harness.web_fetch.methods import report_method_catalog
    providers = [name for name, method in report_method_catalog()['methods'].items()
                 if method.get('kind') == 'browser']
    # Single-case matrices keep the assertion independent of presentation grouping.
    for case_id, case in cases.items():
        matrix = browser_task_interface_matrix({case_id: case}, providers, ctx=RenderContext())
        for provider in providers:
            assert matrix['rows'][0]['cells'][provider][0]['mode'] == output_mode_for_case(
                provider, 'headless_markdown', SimpleNamespace(**case))


def test_interface_converter_version_comes_from_call_not_catalog():
    row = {'provider': 'chromium', 'provider_metadata': {
        'engine': 'chromium', 'extractor': 'node-html-markdown', 'extractor_version': '9.9.9'}}
    spec = method_spec('chromium', row, ctx=RenderContext())
    assert spec['request']['converter'] == 'node-html-markdown 9.9.9'
    assert next(param[2] for param in spec['params'] if param[0] == 'converter') == 'node-html-markdown 9.9.9'
    row['provider_metadata'].pop('extractor_version')
    assert method_spec('chromium', row, ctx=RenderContext())['request']['converter'] == '<recorded_converter>'


def test_mixed_build_labels_have_explicit_english_and_scoped_lifetime(tmp_path):
    rows = [{'provider': 'moli-candidate', 'provider_metadata': {'reported_version': version}}
            for version in ['moli 9.9.8', 'moli 9.9.9', 'moli 9.9.9']]
    @with_measured_method_labels
    def render(out, dataset, records, *, scope):
        return browser_method_label('moli-candidate', scope=scope), browser_method_label('moli-candidate', language='en', scope=scope)
    zh, en = render(tmp_path, {}, rows)
    assert zh == 'Moli 多构建结果（1 例 9.9.8；2 例 9.9.9）'
    assert en == 'Moli mixed builds (1 case: 9.9.8; 2 cases: 9.9.9)'
    assert browser_method_label('moli-candidate', language='en', scope=MethodScope()) == 'Moli candidate build'
    for row, version in zip(rows, ['9.9.9-a', '9.9.9-b', '9.9.9-b'], strict=True):
        row['provider_metadata']['reported_version'] = version
    assert render(tmp_path, {}, rows) == ('Moli 9.9.9（候选版）', 'Moli 9.9.9 (candidate build)')


# positive_number itself is covered by test_positive_number_accepts_only_finite_positive_numbers.
@pytest.mark.parametrize('value,expected', [(2500, '2,500'), (True, None)])
def test_registered_runtime_parameter_uses_only_valid_numeric_evidence(custom_catalog, value, expected):
    method = custom_catalog['methods']['acme-fetch']
    method['interface']['params'] = [['timeout', 'number', 'Unknown']]
    method['runtime_parameters'] = [{'parameter': 'timeout', 'rules': [{
        'metadata_equals': {}, 'description': {'text': '{budget}{value}',
            'fields': {'budget': 'report_outer_budget'},
            'fragments': {'value': [{'text': '; inner {ms:,} ms',
                'fields': {'ms': 'inner_ms'}, 'positive_numeric_fields': ['ms']}]}},
        'note': 'Read saved parameters.'}]}]
    row = {'provider': 'acme-run', 'provider_metadata': {'engine': 'acme-fetch', 'inner_ms': value}}
    result = method_spec('acme-run', row, timeout_seconds=30, language='en', ctx=RenderContext())
    description = result['params'][0][2]
    assert '30-second outer deadline' in description
    if expected:
        assert '; inner '+expected+' ms' in description
    else:
        assert 'inner' not in description


def test_observed_route_matrix_localizes_without_reverse_lookup(monkeypatch):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    records = [{'provider': 'moli', 'case_id': 'body', 'provider_metadata': {
        'extractor': 'moli_native_markdown', 'dump': 'markdown',
        'wait_until': 'domstable', 'navigation_wait_until': 'load'}}]
    import re
    for compact in (False, True):
        matrix = browser_task_interface_matrix(cases, ['moli'], records, language='en', compact=compact, ctx=RenderContext())
        assert not re.search(r'[一-鿿]', json.dumps(matrix, ensure_ascii=False))
        assert 'Markdown' in matrix['rows'][0]['cells']['moli'][0]['description']
    detailed = browser_task_interface_matrix(cases, ['moli'], records, language='en', ctx=RenderContext())
    assert detailed['rows'][0]['cells']['moli'][0]['wait'] == 'Navigation wait load; output completion domstable'
    missing = browser_task_interface_matrix(cases, ['unknown'], records=[], language='en', ctx=RenderContext())
    assert missing['rows'][0]['cells']['unknown'][0]['description'] == 'Not registered'


def test_english_filters_and_hosted_labels_use_registered_language(tmp_path):
    from pathlib import PurePosixPath
    from bs4 import BeautifulSoup
    from harness.web_fetch.report import filter_controls, render_execution_contract
    from harness.web_fetch.report_i18n import localize_html
    records = [{'provider': 'moli-candidate', 'provider_metadata': {'reported_version': version}}
               for version in ['9.9.8', '9.9.9']]
    records.append({'provider': 'kitesurf', 'provider_metadata': {'engine': 'kitesurf'}})

    @with_measured_method_labels
    def render(out, dataset, rows, *, scope):
        names = ['moli-candidate', 'kitesurf']
        controls = filter_controls([('provider', '方法', [
            (name, browser_method_label(name, scope=scope)) for name in names])], ctx=RenderContext(methods=scope))
        body = controls + render_execution_contract({}, rows, [], ctx=RenderContext(methods=scope))
        return localize_html(body, PurePosixPath('analysis/index.html'))

    document = BeautifulSoup(render(tmp_path, {}, records), 'html.parser')
    options = document.select('select[data-filter="provider"] option')
    assert [option['value'] for option in options] == ['', 'moli-candidate', 'kitesurf']
    assert options[1].get_text() == 'Moli mixed builds (1 case: 9.9.8; 1 case: 9.9.9)'
    assert 'hosted backend version not disclosed' in options[2].get_text()
    assert not any(option.find() for option in options)
    import re
    assert not re.search(r'[\u4e00-\u9fff]', document.get_text())


def test_output_contract_labels_use_selected_method():
    for language in ('zh', 'en'):
        for contract in ('native_markdown_resource_urls', 'native_html_resource_urls', 'native_json_resource_urls'):
            label = report_method_catalog(language)['output_contract_labels'][contract].format(method='Acme Fetch')
            assert 'Acme Fetch' in label
            assert 'Moli' not in label


def test_positive_number_accepts_only_finite_positive_numbers():
    from harness.numbers import positive_number
    for value in (True, False, None, '30', 0, -1, float('inf'), float('-inf'), float('nan')):
        assert not positive_number(value), value
    for value in (0.001, 1, 30.5):
        assert positive_number(value), value


def test_catalog_templates_merge_overrides_without_mutating_defaults():
    from harness.web_fetch.methods import _resolve_catalog_templates
    source = {'templates': {'base': {'interface': {'notes': 'shared', 'params': ['base']}}},
              'methods': {'one': {'extends': 'base', 'interface': {'params': ['one']}},
                          'two': {'extends': 'base'}}}
    result = _resolve_catalog_templates(source)
    assert result['methods']['one']['interface'] == {'notes': 'shared', 'params': ['one']}
    result['methods']['one']['interface']['notes'] = 'changed'
    assert result['methods']['two']['interface']['notes'] == 'shared'
    assert source['templates']['base']['interface']['params'] == ['base']


@pytest.mark.parametrize('templates,parent', [({}, 'missing'), ({'loop': {'extends': 'loop'}}, 'loop')])
def test_catalog_templates_reject_missing_or_cyclic_parent(templates, parent):
    from harness.web_fetch.methods import _resolve_catalog_templates
    with pytest.raises(ValueError, match='Unknown|Cyclic'):
        _resolve_catalog_templates({'templates': templates, 'methods': {'test': {'extends': parent}}})


def test_matrix_uses_recorded_provider_type_in_execution_selector(custom_catalog):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    records = [{'provider': 'acme-fetch', 'provider_type': 'headless_html', 'case_id': 'body',
                'provider_metadata': {'engine': 'acme-fetch'}}]
    part = browser_task_interface_matrix(cases, ['acme-fetch'], records, ctx=RenderContext())['rows'][0]['cells']['acme-fetch'][0]
    assert part['mode'] == 'html'


def test_catalog_descriptions_do_not_claim_measured_run_settings():
    import re
    from fnmatch import fnmatchcase
    from pathlib import Path
    data = json.loads((source_path('harness/web_fetch/data/report-methods.json', root=Path(__file__).resolve().parents[1])).read_text(encoding='utf-8'))
    def violations(value, path=()):
        if isinstance(value, dict):
            return [item for key, child in value.items()
                    for item in violations(child, (*path, str(key)))]
        if isinstance(value, list):
            return [item for index, child in enumerate(value)
                    for item in violations(child, (*path, str(index)))]
        bound = any(fnmatchcase('.'.join(path), rule) for rule in (
            'adapters.*.report.*', 'methods.*.runtime_parameters.*.rules.*', 'messages.*'))
        return [path] if isinstance(value, str) and re.search('本轮|this run', value, re.I) and not bound else []
    assert not violations(data)
    assert not violations({'messages': {'run_label': {'zh': '本轮输出', 'en': 'This run’s output'}}})
    for text in ('本轮使用默认参数', 'THIS RUN uses a timeout'):
        assert violations({'methods': {'new': {'profile': {'description': text}}}})
        assert not violations({'adapters': {'new': {'report': {'description': text}}}})


@pytest.mark.parametrize("version", ["2.3.4", None])
def test_explicit_unknown_engine_does_not_inherit_colliding_product(tmp_path, version):
    from harness.web_fetch.methods import report_method, native_browser_engine
    row = {"provider": "moli", "provider_type": "headless_markdown",
           "provider_metadata": {"engine": "new-engine", "reported_version": version}}
    assert report_method("moli", row, scope=MethodScope()) == {}
    assert native_browser_engine("moli", row, scope=MethodScope()) == "new-engine"
    assert method_access("moli", [row], scope=MethodScope())["env_vars"] == []
    assert method_spec("moli", row, language="en", ctx=RenderContext())["entry"] == "Not registered"

    @with_measured_method_labels
    def render(out, dataset, records, *, scope):
        return browser_method_label("moli", language="en", scope=scope)

    assert render(tmp_path, {}, [row]) == "new-engine" + (" 2.3.4" if version else "")


@pytest.mark.parametrize("provider, version, expected", [
    ("obscura", "obscura 0.2.2", "Obscura 0.2.2"),
    ("lightpanda", "Lightpanda 0.4.1+build.7", "Lightpanda 0.4.1+build.7"),
    ("obscura", "0.2.2 (obscura-compatible)", "Obscura 0.2.2 (obscura-compatible)"),
    ("obscura", "ObscuraTools 2.0", "Obscura ObscuraTools 2.0"),
])
def test_reported_product_name_is_not_repeated_or_removed_from_build_suffix(tmp_path, provider, version, expected):
    @with_measured_method_labels
    def render(out, dataset, records, *, scope):
        return tuple(browser_method_label(provider, language=language, scope=scope) for language in ('zh', 'en'))
    records = [{'provider': provider, 'provider_metadata': {'engine': provider, 'reported_version': version}}]
    assert render(tmp_path, {}, records) == (expected, expected)


def test_method_message_languages_preserve_parameter_contracts():
    from string import Formatter
    from harness.web_fetch.methods import _raw_report_method_catalog
    for key, entries in _raw_report_method_catalog()['messages'].items():
        assert set(entries) == {'zh', 'en'}, key
        fields = [{name for _, name, _, _ in Formatter().parse(entries[language]) if name is not None}
                  for language in ('zh', 'en')]
        assert fields[0] == fields[1], key


def test_multiple_configurations_of_the_same_engine_have_distinct_names(tmp_path, custom_catalog):
    rows = [{'provider': name, 'provider_metadata': {'engine': engine, 'reported_version': version}}
            for name, engine, version in [('moli-auto', 'moli', 'moli 9.9.9'),
                                          ('moli-wait', 'moli', 'moli 9.9.9'),
                                          ('other', 'acme-fetch', '7.8.9')]]
    @with_measured_method_labels
    def render(out, dataset, records, *, scope):
        return {row['provider']: browser_method_label(row['provider'], scope=scope) for row in records}
    assert render(tmp_path, {}, rows) == {
        'moli-auto': 'Moli 9.9.9 (moli-auto)',
        'moli-wait': 'Moli 9.9.9 (moli-wait)',
        'other': 'Acme Fetch 7.8.9',
    }
