"""Preserve prior calls while adding a method to the same frozen population."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from tests.test_saved_matrix_evidence import build, fixture, write


def reports(tmp_path, *, different_task=False, duplicate=False):
    entries = []
    for i in range(2):
        folder = tmp_path / str(i)
        spec, source, record, _ = fixture(folder)
        provider = 'method' if duplicate else 'method-' + str(i)
        spec['providers'] = [{'id': provider, 'label': provider}]
        record.update(provider=provider, attempt_id='run-' + str(i))
        raw = (json.dumps(record) + '\n').encode()
        (source / 'results.jsonl').write_bytes(raw)
        spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
        if i and different_task:
            data = json.loads((source / 'dataset.json').read_bytes())
            data['cases'][0]['expected_any'] = ['different']
            spec['suites'][0]['dataset'] = write(source / 'dataset.json', data)
        root = build(folder, spec).parent
        audit = root / 'saved-matrix-audit.json'
        entries.append({'id': 'source-' + str(i), 'root': str(root),
                        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}})
    spec = {'schema_version': 1, 'title': 'Combined', 'saved_reports': entries}
    path = tmp_path / 'combined.json'
    write(path, spec)
    return path, entries


def test_composition_retains_exact_source_records(tmp_path):
    spec, entries = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    assert check_saved_matrix(output, spec_path=spec)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert len(model['providers']) == 2 and len(model['rows']) == 2
    for row, entry in zip(model['rows'], entries, strict=True):
        source = Path(entry['root'])
        original = json.loads((source / 'matrix.json').read_bytes())['rows'][0]
        assert (output / row['record_path']).read_bytes() == (source / original['record_path']).read_bytes()
        assert row['passed'] == original['passed'] and row['elapsed_ms'] == original['elapsed_ms']
    (output / model['rows'][0]['answer_path']).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError):
        check_saved_matrix(output)


def test_composition_cannot_be_packaged_for_release(tmp_path):
    from harness.web_fetch.report_package import build as package
    spec, _ = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    with pytest.raises(ValueError, match='Diagnostic evidence cannot enter certified assembly or reports'):
        package(output, tmp_path / 'combined.zip')


def test_composition_rejects_rebound_row_change(tmp_path):
    spec, _ = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    matrix = json.loads((output / 'matrix.json').read_bytes())
    matrix['rows'][0]['elapsed_ms'] = 0
    binding = write(output / 'matrix.json', matrix)
    audit = json.loads((output / 'saved-matrix-audit.json').read_bytes())
    audit['files']['matrix.json'] = binding['sha256']
    write(output / 'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='immutable source reports'):
        check_saved_matrix(output)


@pytest.mark.parametrize('option', ['different_task', 'duplicate'])
def test_composition_rejects_incompatible_reports(tmp_path, option):
    spec, _ = reports(tmp_path, **{option: True})
    with pytest.raises(ValueError, match='different frozen tasks|duplicate methods'):
        build_saved_matrix(spec, tmp_path / 'combined')


def test_composition_selects_method_without_changing_saved_results(tmp_path):
    spec, _ = reports(tmp_path)
    source = build_saved_matrix(spec, tmp_path / 'combined').parent
    audit = source / 'saved-matrix-audit.json'
    selection = {'schema_version': 1, 'title': 'Selected method', 'saved_reports': [{
        'id': 'prior', 'root': str(source), 'providers': ['method-1'],
        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}}]}
    path = tmp_path / 'selection.json'
    write(path, selection)
    output = build_saved_matrix(path, tmp_path / 'selected').parent
    assert check_saved_matrix(output, spec_path=path)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert [p['id'] for p in model['providers']] == ['method-1']
    assert len(model['rows']) == 1
    row = model['rows'][0]
    original = json.loads((source / 'matrix.json').read_bytes())['rows'][1]
    assert (output / row['record_path']).read_bytes() == (source / original['record_path']).read_bytes()
    assert row['passed'] == original['passed'] and row['elapsed_ms'] == original['elapsed_ms']


@pytest.mark.parametrize('selected', [[], ['unknown'], ['method-0', 'method-0'], 'method-0', [None], None])
def test_composition_rejects_invalid_method_selection(tmp_path, selected):
    path, _ = reports(tmp_path)
    spec = json.loads(path.read_bytes())
    spec['saved_reports'][0]['providers'] = selected
    write(path, spec)
    with pytest.raises(ValueError, match='Invalid saved report provider selection'):
        build_saved_matrix(path, tmp_path / 'combined')


def test_composition_preserves_shared_revision_attachments(tmp_path):
    from tests.test_saved_matrix_revision import revision_fixture
    spec, _, _ = revision_fixture(tmp_path, second_provider=True)
    spec['offline_revision']['review_files'] = {'notes': write(tmp_path / 'notes.json', {'reviewed': True})}
    path = tmp_path / 'revised-spec.json'
    write(path, spec)
    source = build_saved_matrix(path, tmp_path / 'revised').parent
    audit = source / 'saved-matrix-audit.json'
    combined = {'schema_version': 1, 'title': 'Shared attachments', 'saved_reports': [{
        'id': 'source', 'root': str(source),
        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}}]}
    write(tmp_path / 'combined.json', combined)
    output = build_saved_matrix(tmp_path / 'combined.json', tmp_path / 'combined').parent
    assert check_saved_matrix(output)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert len(model['rows']) == 2
    for row in model['rows']:
        receipt = json.loads((output / row['score_revision']['receipt_path']).read_bytes())
        attachment = output / receipt['review_files']['notes']
        assert json.loads(attachment.read_bytes()) == {'reviewed': True}
    attachment.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        check_saved_matrix(output)


def _published_extension(tmp_path, *, external=True, reviewed=False):
    from harness.web_fetch.report_saved_composition import build_published_extension, check_published_extension
    families = ['web', 'document']

    def snapshot(name, order, values):
        root = tmp_path / name
        root.mkdir()
        header = '<thead><tr>' + '<th>value</th>' * 9 + ('<th>审查结果</th>' if reviewed else '') + '</tr></thead>'
        def table(identifier, numbers):
            cells = '<td>1</td><td><a href="providers/' + name + '.html">' + name + '</a></td>'
            cells += ''.join('<td data-sort="' + str(n) + '"><strong class="good">' + str(n) + '</strong></td>' for n in numbers)
            if reviewed and identifier == 'ranking-overall':
                cells += '<td>Reviewed</td>'
            return '<table id="' + identifier + '">' + header + '<tbody><tr>' + cells + '</tr></tbody></table>'
        home = '<html><body><section id="overall-ranking"><h2>Ranking</h2>'
        home += table('ranking-overall', [90, 80, *values, 10, 20, 30]) + '</section>'
        for family in families:
            home += table('ranking-' + family, [1])
        home += '<section id="method-overview-section"><p>Old billing disclaimer</p><div><table id="method-overview"><tbody><tr><td>original configuration</td></tr></tbody></table></div></section></body></html>'
        home = home.replace('</body>', '<img src="logo.png"></body>')
        (root / 'logo.png').write_bytes(b'synthetic public image')
        (root / 'index.html').write_text(home, encoding='utf-8')
        (root / 'providers').mkdir()
        (root / 'providers' / (name + '.html')).write_text('<html><pre data-file="answer.txt"></pre></html>', encoding='utf-8')
        (root / 'answer.txt').write_text('unchanged answer: ' + name, encoding='utf-8')
        model = {'providers': [{'id': name}], 'report_families': [{'id': f} for f in order],
                 'rows': [{'provider': name, 'url': 'https://example.com/', 'passed': True}]}
        (root / 'matrix.json').write_text(json.dumps(model), encoding='utf-8')
        audit = {'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in root.rglob('*') if p.is_file()}}
        binding = write(root / 'saved-matrix-audit.json', audit)
        return {'root': str(root), 'audit': binding}

    base = snapshot('base', families, [11, 22])
    addition = snapshot('extra', list(reversed(families)), [33, 44])
    overview = write(tmp_path / 'methods.json', [{'id': 'extra', 'label': 'Extra',
        'execution': 'Remote API', 'configuration': '30 seconds', 'price': 'Unknown',
        'billing': 'Undisclosed', 'sources': [{'url': 'https://example.com/prices', 'label': 'Official'}]}])
    spec = write(tmp_path / 'extension.json', {'published_base': base, 'additional_report': addition,
        'method_overview': overview, 'comparison_note': 'Different scoring editions; retained scores.'})
    output = tmp_path / 'extended'
    build_published_extension(Path(spec['path']), output)
    assert check_published_extension(output)['providers'] == 2
    assert (output / 'answer.txt').read_bytes() == (Path(base['root']) / 'answer.txt').read_bytes()
    assert (output / 'additional/answer.txt').read_bytes() == (Path(addition['root']) / 'answer.txt').read_bytes()
    from bs4 import BeautifulSoup
    home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    row = home.select('#ranking-overall tbody tr')[1]
    assert [c.get_text() for c in row.find_all('td')][4:6] == ['44', '33']
    assert row.a['href'] == 'additional/providers/extra.html'
    assert not home.select('#ranking-overall .good, #ranking-overall strong')
    assert home.select_one('[data-added-method="extra"]')
    from harness.reporting.packs import write_compact_packs
    from harness.web_fetch.report_package import extend_published_content, _save_wire_manifest, _write_archive, check_extension_archive
    packed = tmp_path / 'baseline-package'
    original_root = Path(base['root'])
    entries = [(p.relative_to(original_root).as_posix(),
                {'size':p.stat().st_size, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                 'mime':'text/html' if p.suffix == '.html' else 'text/plain'}, p.read_bytes())
               for p in original_root.rglob('*') if p.is_file()]
    manifest = write_compact_packs(entries, packed)
    manifest['external_packs'] = {name:{'url':'https://example.com/pinned/' + name,
        'sha256':hashlib.sha256((packed/name).read_bytes()).hexdigest()} for name in manifest['packs']} if external else {}
    _save_wire_manifest(packed, manifest)
    (packed/'index.html').write_text('<html><head></head><body></body></html>', encoding='utf-8')
    (packed/'style.css').write_text('body { color: black; }', encoding='utf-8')
    (packed/'report.js').write_text('/* synthetic report controls */', encoding='utf-8')
    content = tmp_path / 'extension-package'
    extended = extend_published_content(output, packed, content)
    assert extended['external_packs'] == manifest['external_packs']
    assert all((content/name).is_file() is (not external) for name in manifest['packs'])
    archive = tmp_path / 'extension.zip'
    _write_archive(content, archive)
    checked = check_extension_archive(archive, output, baseline=packed)
    assert checked['reused_packs'] == len(manifest['packs'])
    assert checked['appended_files'] > 0
    return output, packed, content, archive


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('reviewed', [False, True])
def test_published_extension_retains_scoring_editions_and_reorders_columns(tmp_path, external, reviewed):
    from harness.web_fetch.report_package import extend_published_content, _write_archive, check_extension_archive
    from harness.web_fetch.report_saved_composition import check_published_extension
    output, packed, content, _ = _published_extension(tmp_path, external=external, reviewed=reviewed)
    assert (output/'additional/providers/extra.html').read_text(encoding='utf-8') == '<html><pre data-file="answer.txt"></pre></html>'
    from harness.reporting.packs import read_packed_file
    import zipfile
    with zipfile.ZipFile(tmp_path/'extension.zip') as bundle:
        assert b'data-file="additional/answer.txt"' in read_packed_file(bundle, 'additional/providers/extra.html')
    refreshed = tmp_path / 'refreshed-package'
    extend_published_content(output, content, refreshed)
    refreshed_archive = tmp_path / 'refreshed.zip'
    _write_archive(refreshed, refreshed_archive)
    assert check_extension_archive(refreshed_archive, output, baseline=packed)['valid']
    # Explicit unified presentation changes views, never saved verdicts or outputs.
    from harness.web_fetch.report_saved_composition import refresh_published_extension
    from bs4 import BeautifulSoup
    ranked = json.loads((tmp_path/'extension.json').read_bytes())
    ranked['presentation'] = 'ranked'
    ranked_spec = tmp_path / 'ranked.json'
    ranked_spec.write_text(json.dumps(ranked), encoding='utf-8')
    refresh_published_extension(output, spec_path=ranked_spec)
    ranked_home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    assert not ranked_home.select('[data-composition-scope]')
    assert 'Old billing disclaimer' not in ranked_home.get_text()
    assert ranked_home.select_one('#ranking-overall th').get_text() == '排名'
    assert ranked_home.select_one('#ranking-overall .metric-best')
    assert all(r.find('td').get_text() == '1' for r in ranked_home.select('#ranking-overall tbody tr'))
    assert (output / 'answer.txt').read_text(encoding='utf-8') == 'unchanged answer: base'
    assert check_published_extension(output)['valid']
    (output / 'additional/answer.txt').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='bytes changed'):
        check_published_extension(output)


def test_extension_rankings_ties_and_latency_use_all_methods():
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _unified_home
    page = BeautifulSoup('''<section id="overall-ranking"><h2>Saved</h2></section>
        <section id="overall-matched-latency"><div class="wrap">old ten-method timing</div></section>''', 'html.parser')
    def model(provider, times, passed):
        return {'providers': [{'id': provider, 'label': provider}], 'report_families': [],
                'rows': [{'provider': provider, 'url': 'https://example.com/' + str(i),
                          'name': str(i), 'passed': i in passed, 'elapsed_ms': ms}
                         for i, ms in enumerate(times)]}
    _unified_home(page, model('deepseek-fetch-api', [10, 500], {0, 1}),
                  model('kimi-fetch', [20, 900], {0}))
    rows = page.select('#overall-matched-latency-table tbody tr')
    assert len(rows) == 2
    assert [r.find_all('td')[2]['data-sort'] for r in rows] == ['10', '20']
    assert '1/2' in page.get_text()
    assert rows[0].select_one('.metric-best')
    assert len(page.select('.model-fetch-badge')) == 2
    assert '模型 Fetch' in page.get_text()


def test_extension_unifies_selectors_and_same_task_outcomes():
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _extension_page
    def page(provider, task):
        return f'''<html><body><main><select id="compare-a"><option value="{provider}">{provider}</option></select>
        <div class="method-checks"><label><input data-compare-method value="{provider}">{provider}</label></div>
        <table><thead><tr><th>序号</th><th>任务</th><th>成功方法</th><th>失败方法</th></tr></thead>
        <tbody id="compare-rows"><tr data-pass="{provider}" data-fail=""><td>1</td><td><a href="../datasets/{task}.html">Task</a></td><td><a href="../providers/{provider}.html">{provider}</a></td><td>—</td></tr></tbody></table>
        <table id="filtered-table"><thead><tr><th>序号</th><th>方法</th></tr></thead><tbody><tr data-provider="{provider}"><td>1</td><td>{provider}</td></tr></tbody></table>
        </main></body></html>'''
    def model(provider, suite):
        return {'providers': [{'id': provider}], 'rows': [{'provider': provider, 'suite': suite,
            'case_id': 'one', 'url': 'https://example.com', 'name': 'Task'}]}
    html = _extension_page(page('base', 'old--one'), page('kimi-fetch', 'new--one'),
        'analysis/index.html', model('base', 'old'), model('kimi-fetch', 'new'), [], '')
    h = BeautifulSoup(html, 'html.parser')
    assert len(h.select('#compare-a option')) == 2
    assert len(h.select('[data-compare-method]')) == 2
    assert len(h.select('#compare-rows tr')) == 1
    assert h.select_one('#compare-rows tr')['data-pass'] == 'base kimi-fetch'
    assert h.select_one('#compare-rows a[href*="kimi-fetch"]')['href'] == '../additional/providers/kimi-fetch.html'
    assert len(h.select('#filtered-table tbody tr')) == 2


@pytest.mark.parametrize('tamper', ['local_pack', 'trailing_bytes', 'unreferenced_block', 'shell', 'style', 'home_asset'])
def test_extension_publication_rejects_tampered_physical_bytes(tmp_path, tamper):
    import gzip
    import struct
    from harness.web_fetch.report_package import _wire_manifest, _save_wire_manifest, _write_archive, check_extension_archive
    output, baseline, content, _ = _published_extension(tmp_path, external=False)
    manifest = _wire_manifest(content)
    old = _wire_manifest(baseline)
    if tamper == 'local_pack':
        (content/old['packs'][0]).write_bytes(b'corrupted retained pack')
    elif tamper in {'trailing_bytes', 'unreferenced_block'}:
        target = content/manifest['packs'][-1]
        target.write_bytes(gzip.compress(gzip.decompress(target.read_bytes())+b'X'))
        if tamper == 'unreferenced_block':
            catalog = content/'catalog.bin.gz'
            raw = gzip.decompress(catalog.read_bytes())
            split = manifest['block_count']*2
            catalog.write_bytes(gzip.compress(raw[:split]+struct.pack('<H', 1)+raw[split:]))
            manifest['block_count'] += 1
            _save_wire_manifest(content, manifest)
    elif tamper == 'shell':
        path = content/'index.html'
        path.write_bytes(path.read_bytes()+b'<script>untrusted()</script>')
    elif tamper == 'style':
        (content/'style.css').write_bytes(b'body { display: none; }')
    else:
        next((content/'home-assets').iterdir()).write_bytes(b'changed image')
    archive = tmp_path/'tampered.zip'
    _write_archive(content, archive)
    with pytest.raises(ValueError, match='Retained local pack differs|undeclared bytes|unreferenced appended blocks|physical home|physical stylesheet'):
        check_extension_archive(archive, output, baseline=baseline)


def test_extension_publication_checks_sensitive_appended_content_at_boundary(tmp_path):
    from unittest.mock import patch
    from harness.web_fetch.report_package import extend_published_content, _write_archive, check_extension_archive
    output, baseline, _, _ = _published_extension(tmp_path)
    path = output/'note.txt'
    path.write_bytes(b'Authorization: Bearer synthetic-private-value')
    proof_path = output/'saved-extension.json'
    proof = json.loads(proof_path.read_bytes())
    proof['files']['note.txt'] = hashlib.sha256(path.read_bytes()).hexdigest()
    proof_path.write_text(json.dumps(proof), encoding='utf-8')
    malicious = tmp_path/'supplied-package'
    # Model a supplied archive that bypassed the trusted builder's privacy check.
    with patch('harness.web_fetch.report_package.publication_file_risk', return_value=None):
        extend_published_content(output, baseline, malicious)
    archive = tmp_path/'supplied.zip'
    _write_archive(malicious, archive)
    with pytest.raises(ValueError, match='Private content in extension'):
        check_extension_archive(archive, output, baseline=baseline)
