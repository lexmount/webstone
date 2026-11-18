"""Preserve prior calls while adding a method to the same frozen population."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from tests.test_saved_matrix_evidence import build, fixture, write


def reports(tmp_path, *, different_task=False, duplicate=False, undecidable=False, families=False):
    entries = []
    for i in range(2):
        folder = tmp_path / str(i)
        spec, source, record, _ = fixture(folder)
        if families:
            spec['report_families'] = [{'id': 'web', 'label': 'Static websites'}]
        provider = 'method' if duplicate else 'method-' + str(i)
        spec['providers'] = [{'id': provider, 'label': provider}]
        record.update(provider=provider, attempt_id='run-' + str(i))
        if i and undecidable:
            record['metrics']['acceptance_checks'] = {'content': None}
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
        provider_html = '<html lang="en"><head><script defer src="../assets/report.js"></script></head><body><button data-call-log-open="evidence" onclick="openCallLog(this)">Evidence</button><dialog id="evidence" aria-label="Evidence"><button data-call-log-close onclick="closeCallLog(this)">Close</button><pre data-file="answer.txt"></pre></dialog></body></html>'
        (root / 'providers' / (name + '.html')).write_text(provider_html, encoding='utf-8')
        (root/'assets').mkdir()
        (root/'assets/report.js').write_bytes((Path(__file__).resolve().parents[1]/'src/harness/web_fetch/assets/report.js').read_bytes())
        (root/'analysis').mkdir()
        analysis = f'<html><body><main><section id="scope-{name}"><h2>Source</h2><p>{name}</p></section>'
        analysis += f'<select id="compare-a"><option value="{name}">{name}</option></select><div class="method-checks"><input data-compare-method value="{name}"></div>'
        analysis += f'<table id="filtered-table"><thead><tr><th>No.</th><th>Method</th></tr></thead><tbody><tr data-provider="{name}"><td>1</td><td>{name}</td></tr></tbody></table></main></body></html>'
        (root/'analysis/index.html').write_text(analysis, encoding='utf-8')
        (root / 'answer.txt').write_text('unchanged answer: ' + name, encoding='utf-8')
        model = {'providers': [{'id': name}], 'report_families': [{'id': f} for f in order],
                 'rows': [{'provider': name, 'suite': 'suite', 'case_id': 'one', 'url': 'https://example.com/', 'passed': True}]}
        (root / 'matrix.json').write_text(json.dumps(model), encoding='utf-8')
        audit = {'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in root.rglob('*') if p.is_file()}}
        binding = write(root / 'saved-matrix-audit.json', audit)
        return {'root': str(root), 'audit': binding}

    base = snapshot('base', families, [11, 22])
    addition = snapshot('extra', list(reversed(families)), [33, 44])
    overview = write(tmp_path / 'methods.json', [{'id': 'extra', 'label': 'Extra',
        'execution': 'Remote API', 'configuration': '30 seconds',
        'configurations': [{'category': 'All task families', 'parameters': [
            {'label': 'Target URL', 'name': 'url', 'value': '<target-url>'}]}], 'price': 'Unknown',
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
    assert home.select_one('[data-added-method="extra"] .method-parameters code').get_text() == 'url="<target-url>"'
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
    assert (output/'additional/providers/extra.html').read_bytes() == (tmp_path/'extra/providers/extra.html').read_bytes()
    from harness.reporting.packs import read_packed_file
    import zipfile
    with zipfile.ZipFile(tmp_path/'extension.zip') as bundle:
        assert b'data-file="additional/answer.txt"' in read_packed_file(bundle, 'additional/providers/extra.html')
    refreshed = tmp_path / 'refreshed-package'
    extend_published_content(output, content, refreshed)
    refreshed_archive = tmp_path / 'refreshed.zip'
    _write_archive(refreshed, refreshed_archive)
    assert check_extension_archive(refreshed_archive, output, baseline=packed)['valid']
    (output / 'additional/answer.txt').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='bytes changed'):
        check_published_extension(output)






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





def test_extension_updates_only_explicit_population_summary():
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _extension_page
    base = {'providers': [{'id': 'one'}, {'id': 'two'}], 'rows': [
        {'suite': 'suite', 'case_id': str(i), 'url': 'https://example.com/'+str(i)} for i in range(3)]}
    added = {'providers': [{'id': 'three'}], 'rows': base['rows']}
    raw = '<html><body><section class="hero"><p class="lead">2 种方法，3 个逻辑结果。</p></section><p id="evidence">13 ms, $3, 2023-03-03, task-3; 10 种方法</p></body></html>'
    result = BeautifulSoup(_extension_page(raw, '<html><body></body></html>',
        'analysis/index.html', base, added, [], ''), 'html.parser')
    assert result.select_one('.lead').get_text() == '3 种方法，6 个逻辑结果。'
    assert result.select_one('#evidence').get_text() == '13 ms, $3, 2023-03-03, task-3; 10 种方法'




def test_extension_reuses_standard_rankings_and_timing(tmp_path):
    import shutil
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _shared_presentations, _joined_presentation
    _, entries = reports(tmp_path)
    output = tmp_path / 'presentation'
    shutil.copytree(entries[0]['root'], output)
    shutil.copytree(entries[1]['root'], output / 'additional')
    cases, records, providers, _ = _joined_presentation(output)
    assert len(cases) == 1 and len(records) == len(providers) == 2
    rendered = _shared_presentations(output)
    overall = BeautifulSoup(rendered['overall'], 'html.parser')
    assert len(overall.select('#ranking-overall tbody tr')) == 2
    assert all(cell.select_one('strong.comparison-winner.good') for row in overall.select('#ranking-overall tbody tr') for cell in row.find_all('td')[-3:])
    assert [r.find('td').get_text() for r in overall.select('#ranking-overall tbody tr')] == ['1', '1']
    assert overall.select('.comparison-winner')
    timing = BeautifulSoup(rendered['timing'], 'html.parser')
    assert len(timing.select('#overall-matched-latency-table tbody tr')) == 2



    analysis = BeautifulSoup(rendered['analysis'], 'html.parser')
    assert len(analysis.select('#compare-a option')) == 2
    assert len(analysis.select('[data-compare-method]')) == 2
    assert len(analysis.select('#filtered-table tbody tr')) == 2
    assert len(analysis.select('#compare-rows tr')) == 1


def test_extension_refresh_restores_baseline_pages_when_leaving_ranked_mode(tmp_path):
    from harness.web_fetch.report_saved_composition import refresh_published_extension, check_published_extension
    output, spec_path = _real_extension(tmp_path)
    spec = json.loads(spec_path.read_bytes())
    original = (Path(spec['published_base']['root'])/'analysis/index.html').read_bytes()
    spec['presentation'] = 'ranked'
    write(spec_path, spec)
    refresh_published_extension(output, spec_path=spec_path)
    assert (output/'analysis/index.html').read_bytes() != original
    spec['presentation'] = 'audit'
    write(spec_path, spec)
    assert refresh_published_extension(output, spec_path=spec_path)['valid']
    assert (output/'analysis/index.html').read_bytes() == original
    assert json.loads((output/'saved-extension.json').read_bytes())['presentation_pages'] == []
    assert check_published_extension(output)['valid']



def test_frozen_namespaced_extension_keeps_verifying_without_double_prefix(tmp_path):
    from harness.web_fetch.report_saved_composition import _namespace_extension_evidence, check_published_extension
    from harness.web_fetch.report_package import extend_published_content, _write_archive, check_extension_archive
    from harness.reporting.packs import read_packed_file
    import zipfile
    output, baseline, _, _ = _published_extension(tmp_path)
    proof_path = output/'saved-extension.json'
    proof = json.loads(proof_path.read_bytes())
    # Reproduce the older producer's declared presentation projection.
    for page in (output/'additional').rglob('*.html'):
        page.write_text(_namespace_extension_evidence(page.read_text(encoding='utf-8')), encoding='utf-8')
        proof['files'][page.relative_to(output).as_posix()] = hashlib.sha256(page.read_bytes()).hexdigest()
    write(proof_path, proof)
    assert check_published_extension(output)['valid']
    content = tmp_path/'legacy-package'
    extend_published_content(output, baseline, content)
    archive = tmp_path/'legacy.zip'
    _write_archive(content, archive)
    assert check_extension_archive(archive, output, baseline=baseline)['valid']
    with zipfile.ZipFile(archive) as bundle:
        page = read_packed_file(bundle, 'additional/providers/extra.html')
        assert b'data-file="additional/answer.txt"' in page
        assert b'data-file="additional/additional/answer.txt"' not in page



def test_extension_writer_uses_little_endian_on_native_big_endian_host(tmp_path):
    import array
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch
    from harness.web_fetch import report_package
    source, baseline, _, _ = _published_extension(tmp_path)
    native_array = array.array

    class BigEndianArray(native_array):
        def tobytes(self):
            copy = native_array(self.typecode, self)
            if sys.byteorder == 'little':
                copy.byteswap()
            return copy.tobytes()

    output = tmp_path/'big-endian-package'
    # Emulate native writer byte order; the independent wire-format reader
    # remains on this machine's real byte order and reads little-endian bytes.
    with patch.object(array, 'array', BigEndianArray), \
         patch.object(report_package, 'sys', SimpleNamespace(byteorder='big')):
        report_package.extend_published_content(source, baseline, output)
    assert report_package.check_extended_content(source, baseline, output)['valid']


def _real_extension(tmp_path, *, undecidable=False):
    from harness.web_fetch.report_saved_composition import build_published_extension
    _, entries = reports(tmp_path, undecidable=undecidable, families=True)
    # Frozen public reports include a method metadata section alongside the
    # standard renderer. Keep real records, datasets and comparison tables.
    for entry in entries:
        root = Path(entry['root'])
        home = root/'index.html'
        home.write_text(home.read_text(encoding='utf-8').replace('</main>',
            '<section id="method-overview-section"><table id="method-overview"><tbody></tbody></table></section></main>'), encoding='utf-8')
        audit = json.loads((root/'saved-matrix-audit.json').read_bytes())
        audit['files']['index.html'] = hashlib.sha256(home.read_bytes()).hexdigest()
        entry['audit'] = write(root/'saved-matrix-audit.json', audit)
    overview = write(tmp_path/'methods.json', [{'id': 'method-1', 'label': 'Method 1',
        'execution': 'Fixture', 'configuration': 'Default', 'price': 'Unavailable',
        'billing': 'Synthetic test', 'sources': [{'url': 'https://example.com/', 'label': 'Source'}]}])
    path = tmp_path/'extension.json'
    write(path, {'published_base': entries[0], 'additional_report': entries[1],
                 'method_overview': overview, 'comparison_note': 'Different scoring editions.'})
    output = tmp_path/'extension'
    build_published_extension(path, output)
    return output, path


@pytest.mark.parametrize('option', ['--presentation-binding', '--refresh-privacy'])
def test_extension_cli_rejects_ignored_options(tmp_path, monkeypatch, option):
    from harness.web_fetch.report_package import main
    output, baseline, _, _ = _published_extension(tmp_path)
    archive = tmp_path/'requested.zip'
    args = ['package', str(output), str(archive), '--reuse-published', str(baseline), option]
    if option == '--presentation-binding':
        args.append(str(tmp_path/'nonexistent.json'))
    monkeypatch.setattr('sys.argv', args)
    with pytest.raises(SystemExit):
        main()
    assert not archive.exists() and not archive.with_suffix('.content').exists()


@pytest.mark.parametrize('destination', ['source', 'baseline', 'symlink', 'directory'])
def test_extension_cli_rejects_unsafe_outputs_without_changing_inputs(tmp_path, monkeypatch, destination):
    from harness.web_fetch.report_package import main
    from harness.web_fetch.safety import evidence_tree_identity
    output, baseline, _, _ = _published_extension(tmp_path)
    archive = tmp_path/'requested.zip'
    retained = tmp_path/'retained.txt'
    retained.write_bytes(b'retained original')
    if destination in {'source', 'baseline'}:
        archive = (output if destination == 'source' else baseline)/'requested.zip'
    elif destination == 'symlink':
        archive.symlink_to(retained)
    else:
        archive.mkdir()
    before = [evidence_tree_identity(p) for p in (output, baseline)]
    monkeypatch.setattr('sys.argv', ['package', str(output), str(archive), '--reuse-published', str(baseline)])
    with pytest.raises(ValueError):
        main()
    assert [evidence_tree_identity(p) for p in (output, baseline)] == before
    assert retained.read_bytes() == b'retained original'
    assert not archive.with_suffix('.content').exists()


def test_extension_cli_keeps_previous_archive_on_validation_failure(tmp_path, monkeypatch):
    from harness.web_fetch import report_package
    output, baseline, _, _ = _published_extension(tmp_path)
    archive = tmp_path/'requested.zip'
    archive.write_bytes(b'previous release')
    def reject(*args, **kwargs):
        raise ValueError('injected archive rejection')
    monkeypatch.setattr(report_package, 'check_extension_archive', reject)
    monkeypatch.setattr('sys.argv', ['package', str(output), str(archive), '--reuse-published', str(baseline)])
    with pytest.raises(ValueError, match='injected archive rejection'):
        report_package.main()
    assert archive.read_bytes() == b'previous release'
    assert not archive.with_suffix('.content').exists()


def test_ordinary_packager_requires_extension_mode(tmp_path):
    from harness.web_fetch.report_package import build as package
    output, _, _, _ = _published_extension(tmp_path)
    with pytest.raises(ValueError, match='--reuse-published'):
        package(output, tmp_path/'ordinary.zip')


@pytest.mark.parametrize('unranked', ['base', 'extra', 'both'])
def test_extension_audit_keeps_method_links_in_unranked_tables(tmp_path, unranked):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _extended_home
    output, _, _, _ = _published_extension(tmp_path)
    pages = []
    for name in ('base', 'extra'):
        page = BeautifulSoup((tmp_path/name/'index.html').read_text(encoding='utf-8'), 'html.parser')
        if unranked in {name, 'both'}:
            for table in page.select('table[id^="ranking-"]'):
                table['data-row-index'] = 'false'
                table.thead.tr.find('th').decompose()
                for row in table.tbody.find_all('tr'):
                    row.find('td').decompose()
        pages.append(str(page))
    result = BeautifulSoup(_extended_home(*pages,
        json.loads((output/'matrix.json').read_bytes()),
        json.loads((output/'additional/matrix.json').read_bytes()),
        json.loads((output/'extension-methods.json').read_bytes()), 'Different scoring editions.'), 'html.parser')
    rows = result.select('#ranking-overall tbody tr')
    assert [r.a['href'] for r in rows] == ['providers/base.html', 'additional/providers/extra.html']
    assert [c.get_text() for c in rows[1].find_all('td')][4:6] == ['44', '33']


def test_extension_audit_shows_escaped_comparison_note(tmp_path):
    from harness.web_fetch.report_saved_composition import refresh_published_extension
    from bs4 import BeautifulSoup
    output, _, _, _ = _published_extension(tmp_path)
    path = tmp_path/'extension.json'
    spec = json.loads(path.read_bytes())
    spec['comparison_note'] = 'Different editions <not a tag> & retained scores.'
    write(path, spec)
    refresh_published_extension(output, spec_path=path)
    page = BeautifulSoup((output/'index.html').read_text(encoding='utf-8'), 'html.parser')
    assert spec['comparison_note'] in page.get_text()
    assert page.find('not') is None


def test_extension_methodology_retains_each_execution_row_once():
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _extension_page
    def page(provider):
        return '<html><body><main><table><thead><tr><th>序号</th><th>方法</th><th>外层超时</th><th>计划轮次</th></tr></thead><tbody><tr><td>1</td><td><a href="../providers/'+provider+'.html">'+provider+'</a></td><td>90</td><td>3</td></tr></tbody></table></main></body></html>'
    def model(p):
        return {'providers': [{'id': p}], 'rows': []}
    result = BeautifulSoup(_extension_page(page('base'), page('extra'), 'methodology/index.html',
        model('base'), model('extra'), [], ''), 'html.parser')
    rows = result.select('tbody tr')
    assert len(rows) == 2
    assert [c.get_text() for c in rows[1].find_all('td')] == ['2', 'extra', '90', '3']


@pytest.mark.parametrize('failure', ['render', 'link'])
def test_extension_refresh_rejection_preserves_prior_tree(tmp_path, monkeypatch, failure):
    from harness.web_fetch import report_saved_composition as composition
    from harness.web_fetch.safety import evidence_tree_identity
    output, _, _, _ = _published_extension(tmp_path)
    before = evidence_tree_identity(output)
    path = tmp_path/'extension.json'
    spec = json.loads(path.read_bytes())
    if failure == 'render':
        spec['presentation'] = 'ranked'
        monkeypatch.setattr(composition, '_shared_presentations', lambda _: {})
        monkeypatch.setattr(composition, '_unified_home', lambda *args: None)
        def reject(*args):
            raise ValueError('injected render rejection')
        monkeypatch.setattr(composition, '_extension_views', reject)
        spec['comparison_note'] = 'Changed note for new home'
    else:
        methods = json.loads((tmp_path/'methods.json').read_bytes())
        methods[0]['sources'][0]['url'] = 'missing.html'
        spec['method_overview'] = write(tmp_path/'methods.json', methods)
    write(path, spec)
    with pytest.raises(ValueError, match='injected render rejection|broken page link'):
        composition.refresh_published_extension(output, spec_path=path)
    assert evidence_tree_identity(output) == before
    assert composition.check_published_extension(output)['valid']


def test_extension_shared_guide_excludes_undecidable_from_denominator(tmp_path):
    import shutil
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _shared_presentations
    _, entries = reports(tmp_path, undecidable=True)
    output = tmp_path/'presentation'
    shutil.copytree(entries[0]['root'], output)
    shutil.copytree(entries[1]['root'], output/'additional')
    result = _shared_presentations(output)
    guide = BeautifulSoup(result['guide'], 'html.parser').get_text(' ', strip=True)
    assert '1/2' not in guide
    assert ('1 measurable' in guide or '1 条可判定' in guide)
    assert 'unavailable' in guide or '无法判定' in guide


@pytest.mark.parametrize('ranked', [False, True])
def test_extension_real_unmeasured_report_preserves_methods_and_metrics(tmp_path, ranked):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import refresh_published_extension, check_published_extension
    output, spec_path = _real_extension(tmp_path, undecidable=True)
    if ranked:
        spec = json.loads(spec_path.read_bytes())
        spec['presentation'] = 'ranked'
        write(spec_path, spec)
        refresh_published_extension(output, spec_path=spec_path)
    page = BeautifulSoup((output/'index.html').read_text(encoding='utf-8'), 'html.parser')
    rows = page.select('#ranking-overall tbody tr')
    assert {r.a['href'] for r in rows} == {'providers/method-0.html', 'additional/providers/method-1.html'}
    assert '排名' not in page.select_one('#ranking-overall thead').get_text()
    assert check_published_extension(output)['valid']
    for index, header in enumerate(page.select('#ranking-overall thead th')):
        button = header.find('button', class_='sort-button')
        if button:
            assert button['onclick'] == f"sortTable('ranking-overall',{index})"


def test_extension_refresh_restores_prior_tree_on_install_failure(tmp_path, monkeypatch):
    from harness.web_fetch import report_saved_composition as composition
    from harness.web_fetch.safety import evidence_tree_identity
    output, _, _, _ = _published_extension(tmp_path)
    before = evidence_tree_identity(output)
    path = tmp_path/'extension.json'
    spec = json.loads(path.read_bytes())
    spec['comparison_note'] = 'New explanation, same frozen inputs.'
    write(path, spec)
    original = Path.rename
    def fail_install(source, target):
        if source.name == 'report' and target == output:
            raise OSError('injected install failure')
        return original(source, target)
    monkeypatch.setattr(Path, 'rename', fail_install)
    with pytest.raises(OSError, match='injected install failure'):
        composition.refresh_published_extension(output, spec_path=path)
    assert evidence_tree_identity(output) == before
    assert composition.check_published_extension(output)['valid']


def test_extension_public_command_builds_verified_archive(tmp_path, monkeypatch):
    from harness.web_fetch import report_package
    output, baseline, _, _ = _published_extension(tmp_path)
    archive = tmp_path/'requested.zip'
    monkeypatch.setattr('sys.argv', ['package', str(output), str(archive), '--reuse-published', str(baseline)])
    report_package.main()
    assert report_package.check_extension_archive(archive, output, baseline=baseline)['valid']
    assert not archive.with_suffix('.content').exists()


@pytest.mark.parametrize('renamed', [False, True])
@pytest.mark.parametrize('identical', [False, True])
def test_extension_join_preserves_repeated_url_name_cases(tmp_path, renamed, identical):
    import shutil
    from harness.web_fetch.report_saved_composition import _joined_presentation
    roots = []
    for index in range(2):
        folder = tmp_path/str(index)
        spec, source, record, _ = fixture(folder)
        case = json.loads((source/'dataset.json').read_bytes())['cases'][0]
        provider = 'method-'+str(index)
        spec['providers'] = [{'id': provider, 'label': provider}]
        record.update(provider=provider, attempt_id='original-'+str(index))
        second = json.loads(json.dumps(record))
        second.update(case_id='second', attempt_id='second-'+str(index), elapsed_ms=900+index)
        cases = [case, {**case, 'id': 'second', 'category': case['category'] if identical else 'repeat'}]
        if index and renamed:
            cases = [{**c, 'id': 'new-'+c['id']} for c in cases]
            record['case_id'], second['case_id'] = [c['id'] for c in cases]
        # Reverse both the case and result order in the added report. Mapping
        # must follow task identities/contracts, never file order or outcomes.
        if index:
            cases.reverse()
        spec['suites'][0]['dataset'] = write(source/'dataset.json', {'cases': cases})
        spec['sources'][0]['case_ids'] = [c['id'] for c in cases]
        records = [record, second] if not index else [second, record]
        (source/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
        spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
        roots.append(build(folder, spec).parent)
    output = tmp_path/'presentation'
    shutil.copytree(roots[0], output)
    shutil.copytree(roots[1], output/'additional')
    if identical and renamed:
        with pytest.raises(ValueError, match='ambiguous or unmatched task identities'):
            _joined_presentation(output)
        return
    cases, records, _, _ = _joined_presentation(output)
    assert len(cases) == 2 and len(records) == 4
    per_method = {p: {r['case_id']: r['elapsed_ms'] for r in records if r['provider'] == p}
                  for p in ('method-0', 'method-1')}
    assert set(per_method['method-0']) == set(per_method['method-1'])
    assert sorted(per_method['method-1'].values()) == [12, 901]
    if not identical or not renamed:
        assert per_method['method-1']['first--second'] == 901
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _shared_presentations
    analysis = BeautifulSoup(_shared_presentations(output)['analysis'], 'html.parser')
    assert len(analysis.select('#compare-rows tr')) == 2
    assert len(analysis.select('#filtered-table tbody tr')) == 4


@pytest.mark.parametrize('unmeasured_panel', [False, True])
def test_extension_adoption_keeps_added_interface_examples(unmeasured_panel):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import _extension_page
    def page(provider):
        return f'''<html><body><main><section id="method-guide-interfaces"><div class="wrap">
        <h2>Interfaces</h2><details class="interface-panel" id="interface-{provider}">
        <summary>{provider}</summary><dl><dt>Request</dt><dd>Native request</dd></dl>
        <pre data-file="adoption/interfaces/{provider}.json"></pre>
        <a href="interfaces/{provider}.json">Example JSON</a>
        <a href="../attempts/{provider}.html">Saved call</a></details></div></section></main></body></html>'''
    model = {'providers': [{'id': 'base'}], 'rows': []}
    added = {'providers': [{'id': 'extra'}], 'rows': []}
    base = page('base')
    if unmeasured_panel:
        base = base.replace('</div>', '<details class="interface-panel" id="interface-extra"><summary>Not measured</summary></details></div>')
    result = BeautifulSoup(_extension_page(base, page('extra'), 'adoption/index.html',
        model, added, [], ''), 'html.parser')
    panels = result.select('#method-guide-interfaces details.interface-panel')
    assert [p['id'] for p in panels] == ['interface-base', 'interface-extra']
    assert panels[1].pre['data-file'] == 'additional/adoption/interfaces/extra.json'
    assert [a['href'] for a in panels[1].select('a')] == [
        '../additional/adoption/interfaces/extra.json', '../additional/attempts/extra.html']
