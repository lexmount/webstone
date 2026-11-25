"""Keep configuration experiments separate from the published leaderboard."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from tests.test_saved_matrix_composition import reports, _published_extension
from tests.test_saved_matrix_evidence import write


def test_configuration_comparison_keeps_leaderboard_population_and_checks_evidence(tmp_path):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_composition import refresh_published_extension
    (tmp_path / 'baseline').mkdir()
    _, packed, _, _ = _published_extension(tmp_path / 'baseline')
    spec_path = tmp_path / 'baseline/extension.json'
    comparison_spec, _ = reports(tmp_path / 'comparison')
    comparison = build_saved_matrix(comparison_spec, tmp_path / 'comparison-report').parent
    # Frozen publications can defer logs at both their root and nested source
    # report roots. These fixtures must retain their own lazy-file namespaces.
    nested = comparison / 'evidence/saved-reports/source-0'
    for owner, name in [(nested, 'method-0'), (comparison, 'method-1')]:
        log = owner / 'lazy-log.txt'
        log.write_text('Complete diagnostic for ' + name, encoding='utf-8')
        page_path = owner / 'providers' / (name + '.html')
        source_page = page_path.read_text(encoding='utf-8')
        page_path.write_text(source_page.replace('</body>', '<pre data-file="lazy-log.txt"></pre></body>'), encoding='utf-8')
        owner_audit = owner / 'saved-matrix-audit.json'
        authority = json.loads(owner_audit.read_bytes())
        authority['files'] = {str(p.relative_to(owner)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in owner.rglob('*') if p.is_file() and p != owner_audit}
        write(owner_audit, authority)
    audit = comparison / 'saved-matrix-audit.json'
    spec = json.loads(spec_path.read_bytes())
    spec['configuration_comparisons'] = [{
        'id': 'proxy', 'root': str(comparison),
        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()},
        'title': {'en': 'Proxy comparison', 'zh': '代理配置对照'},
        'description': {'en': 'Separate saved configurations.', 'zh': '单独展示保存的配置结果。'}}]
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path / 'with-comparison').parent
    assert check_saved_matrix(output, spec_path=spec_path)['providers'] == 2
    page = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    assert len(page.select('#ranking-overall tbody > tr')) == 2
    assert len(page.select('#configuration-proxy-overall tbody > tr')) == 2
    controls = page.select('#configuration-proxy-overall button[onclick]')
    assert controls and all('configuration-proxy-overall' in button['onclick'] for button in controls)
    assert page.select_one('[data-configuration-comparison="proxy"] a')['href'] == 'comparisons/proxy/index.html'
    assert refresh_published_extension(output, spec_path=spec_path)['valid']
    from harness.web_fetch.report_package import extend_published_content, _write_archive, check_extension_archive
    from harness.reporting.packs import read_packed_file
    content = tmp_path / 'packed-comparison'
    manifest = extend_published_content(output, packed, content)
    archive = tmp_path / 'comparison.zip'
    _write_archive(content, archive)
    assert check_extension_archive(archive, output, baseline=packed)['valid']
    import zipfile
    checked = 0
    with zipfile.ZipFile(archive) as bundle:
        for name in manifest['files']:
            if not name.startswith('comparisons/') or not name.endswith('.html'):
                continue
            delivered = BeautifulSoup(read_packed_file(bundle, name), 'html.parser')
            for pre in delivered.select('pre[data-file], pre[data-evidence-file]'):
                key = pre.get('data-file') or pre['data-evidence-file']
                assert key.startswith('comparisons/proxy/') and key in manifest['files']
                if key.endswith('/lazy-log.txt'):
                    prefix = ('comparisons/proxy/evidence/saved-reports/source-0/'
                              if '/source-0/' in name else 'comparisons/proxy/')
                    assert key == prefix + 'lazy-log.txt'
                assert read_packed_file(bundle, key) == (output / key).read_bytes()
                checked += 1
    assert checked >= 2
    frozen_spec = tmp_path / 'frozen-extension.json'
    previous = tmp_path / 'baseline/extended'
    source_proof = previous / 'saved-extension.json'
    # An older renderer highlighted all tied extrema without the later HTML
    # marker. Retain that frozen table while checking every new comparison.
    legacy = '<table id="legacy-ties"><tr>' + ''.join(
        '<td data-metric="legacy" data-metric-direction="max" data-sort="1"><strong class="comparison-winner">1</strong></td>'
        for _ in range(2)) + '</tr></table>'
    home = previous/'index.html'
    home.write_text(home.read_text(encoding='utf-8').replace('</body>', legacy+'</body>'), encoding='utf-8')
    prior = json.loads(source_proof.read_bytes())
    prior['files']['index.html'] = hashlib.sha256(home.read_bytes()).hexdigest()
    write(source_proof, prior)
    write(frozen_spec, {'published_base': {'root': str(previous), 'audit': {
        'path': str(source_proof), 'sha256': hashlib.sha256(source_proof.read_bytes()).hexdigest()}},
        'configuration_comparisons': spec['configuration_comparisons']})
    frozen = build_saved_matrix(frozen_spec, tmp_path / 'frozen-added').parent
    checked_frozen = check_saved_matrix(frozen, spec_path=frozen_spec)
    assert checked_frozen['frozen_base'] and checked_frozen['providers'] == 2
    previous_page = BeautifulSoup((previous/'index.html').read_text(encoding='utf-8'), 'html.parser')
    frozen_page = BeautifulSoup((frozen/'index.html').read_text(encoding='utf-8'), 'html.parser')
    for table in previous_page.select('table[id]'):
        assert str(frozen_page.find('table', id=table['id'])) == str(table)
    old_files = json.loads(source_proof.read_bytes())['files']
    for name in old_files:
        if name != 'index.html':
            assert (frozen / name).read_bytes() == (previous / name).read_bytes()
    assert refresh_published_extension(frozen, spec_path=frozen_spec)['valid']
    from harness.web_fetch.report_package import validate_release
    assert validate_release(frozen) is False
    # A newly embedded comparison is still checked even when its source audit
    # retains the altered bytes exactly.
    bad_page = BeautifulSoup((comparison/'index.html').read_text(encoding='utf-8'), 'html.parser')
    for index, cell in enumerate(bad_page.select('#ranking-overall tbody tr > td:first-child')):
        cell['data-metric'], cell['data-metric-direction'], cell['data-sort'] = 'control', 'max', str(2-index)
        if index:
            marker = bad_page.new_tag('strong', attrs={'class':'comparison-winner'})
            marker.string = 'wrong'
            cell.append(marker)
    (comparison/'index.html').write_text(str(bad_page), encoding='utf-8')
    changed = json.loads(audit.read_bytes())
    changed['files']['index.html'] = hashlib.sha256((comparison/'index.html').read_bytes()).hexdigest()
    new_audit = write(audit, changed)
    bad_spec = json.loads(frozen_spec.read_bytes())
    bad_spec['configuration_comparisons'][0]['audit'] = new_audit
    write(tmp_path/'bad-highlight.json', bad_spec)
    bad_report = build_saved_matrix(tmp_path/'bad-highlight.json', tmp_path/'bad-highlight').parent
    with pytest.raises(ValueError, match='highlight'):
        validate_release(bad_report)
    frozen_content = tmp_path / 'packed-frozen'
    extend_published_content(frozen, packed, frozen_content)
    frozen_archive = tmp_path / 'frozen.zip'
    _write_archive(frozen_content, frozen_archive)
    assert check_extension_archive(frozen_archive, frozen, baseline=packed)['valid']
    # Updating an inventory cannot conceal changes to the original publication.
    original_file = next(name for name in old_files if name.endswith('.html') and name != 'index.html')
    (frozen/original_file).write_text('changed historical evidence', encoding='utf-8')
    frozen_proof = json.loads((frozen/'saved-extension.json').read_bytes())
    frozen_proof['files'][original_file] = hashlib.sha256((frozen/original_file).read_bytes()).hexdigest()
    write(frozen/'saved-extension.json', frozen_proof)
    with pytest.raises(ValueError, match='changed original evidence'):
        check_saved_matrix(frozen)
    # Even a refreshed inventory cannot authorize a wrong displayed value.
    page = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    page.select_one('#configuration-proxy-overall tbody td').string = 'wrong'
    (output / 'index.html').write_text(str(page), encoding='utf-8')
    proof_path = output / 'saved-extension.json'
    proof = json.loads(proof_path.read_bytes())
    proof['files']['index.html'] = hashlib.sha256((output / 'index.html').read_bytes()).hexdigest()
    write(proof_path, proof)
    with pytest.raises(ValueError, match='comparison differs from source metrics'):
        check_saved_matrix(output)



def test_frozen_composition_maps_case_ids_without_changing_source_records(tmp_path):
    spec_path, entries = reports(tmp_path)
    spec = json.loads(spec_path.read_bytes())
    spec.update(frozen_sources=True, retain_source_task_contracts=True)
    for entry in spec['saved_reports']:
        model = json.loads((Path(entry['root']) / 'matrix.json').read_bytes())
        row = model['rows'][0]
        entry['case_id_mapping'] = {row['suite'] + '--' + row['case_id']: 'canonical-task'}
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path / 'mapped').parent
    assert check_saved_matrix(output, spec_path=spec_path)['valid']
    rows = json.loads((output / 'matrix.json').read_bytes())['rows']
    assert {row['case_id'] for row in rows} == {'canonical-task'}
    assert all(json.loads((output / row['record_path']).read_bytes())['case_id'] != 'canonical-task' for row in rows)
    spec['saved_reports'][0]['case_id_mapping']['unknown'] = 'another-task'
    write(spec_path, spec)
    with pytest.raises(ValueError, match='complete frozen population'):
        build_saved_matrix(spec_path, tmp_path / 'invalid-mapping')



def test_frozen_source_composition_accepts_published_audit_and_rejects_changed_bytes(tmp_path):
    spec_path, entries = reports(tmp_path)
    source = Path(entries[0]['root'])
    audit = source/'saved-matrix-audit.json'
    original = json.loads(audit.read_bytes())
    binding = write(audit, {'files': original['files']})
    spec = json.loads(spec_path.read_bytes())
    spec['saved_reports'][0]['audit'] = binding
    spec['frozen_sources'] = True
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path/'combined').parent
    assert check_saved_matrix(output, spec_path=spec_path)['valid']
    (source/'index.html').write_text('changed frozen source', encoding='utf-8')
    with pytest.raises(ValueError, match='Frozen report file changed'):
        build_saved_matrix(spec_path, tmp_path/'changed')



def test_composition_retains_source_review_pages_under_shared_navigation(tmp_path):
    from bs4 import BeautifulSoup
    spec_path, entries = reports(tmp_path)
    spec = json.loads(spec_path.read_bytes())
    spec['retain_source_reviews'] = True
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path/'combined').parent
    assert check_saved_matrix(output, spec_path=spec_path)['valid']
    page = BeautifulSoup((output/'review/index.html').read_text(encoding='utf-8'), 'html.parser')
    sections = page.select('[data-source-review]')
    assert len(sections) == 2
    for index, (section, entry) in enumerate(zip(sections, entries, strict=True)):
        original = BeautifulSoup((Path(entry['root'])/'review/index.html').read_text(encoding='utf-8'), 'html.parser')
        assert f'method-{index}' in section.h2.get_text()
        for paragraph in original.select('main p'):
            assert paragraph.get_text(' ', strip=True) in section.get_text(' ', strip=True)
    (output/'review/index.html').write_text('<html><main>Missing source review scopes</main></html>', encoding='utf-8')
    with pytest.raises(ValueError):
        check_saved_matrix(output)



def test_frozen_revision_stays_source_history_without_inventing_private_attachments(tmp_path):
    spec_path, entries = reports(tmp_path)
    source = Path(entries[0]['root'])
    model = json.loads((source/'matrix.json').read_bytes())
    model['rows'][0]['score_revision'] = {'receipt_path': 'historical-revision.json'}
    matrix = write(source/'matrix.json', model)
    revision = write(source/'historical-revision.json', {'review_files': {'private': 'private-unpublished.json'}})
    audit = json.loads((source/'saved-matrix-audit.json').read_bytes())
    audit['files']['matrix.json'] = matrix['sha256']
    audit['files']['historical-revision.json'] = revision['sha256']
    audit_binding = write(source/'saved-matrix-audit.json', {'files': audit['files']})
    spec = json.loads(spec_path.read_bytes())
    spec['saved_reports'][0]['audit'] = audit_binding
    spec['frozen_sources'] = True
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path/'combined').parent
    assert check_saved_matrix(output, spec_path=spec_path)['valid']
    current = json.loads((output/'matrix.json').read_bytes())
    row = current['rows'][0]
    assert row['source_score_revision']['receipt_path'].endswith('/historical-revision.json')
    assert 'score_revision' not in row and current['scores_changed'] is True
    assert not (output/'private-unpublished.json').exists()



def test_frozen_comparison_keeps_each_saved_task_contract(tmp_path):
    spec_path, entries = reports(tmp_path, different_task=True)
    spec = json.loads(spec_path.read_bytes())
    spec.update(frozen_sources=True, retain_source_task_contracts=True)
    write(spec_path, spec)
    output = build_saved_matrix(spec_path, tmp_path/'combined').parent
    assert check_saved_matrix(output, spec_path=spec_path)['valid']
    records = [json.loads(line) for line in (output/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    assert json.loads((output/records[1]['report_source_case_path']).read_bytes())['expected_any'] == ['different']
    assert json.loads((output/records[0]['report_source_case_path']).read_bytes())['expected_any'] != ['different']
    assert all('report_source_case' not in record for record in records)
    assert all(row['passed'] for row in json.loads((output/'matrix.json').read_bytes())['rows'])


def test_composition_retains_selected_execution_settings_and_review_layout(tmp_path):
    spec, entries = reports(tmp_path)
    expected = [group for entry in entries
                for group in json.loads((Path(entry['root'])/'matrix.json').read_bytes())['execution_settings']]
    assert expected
    config = json.loads(spec.read_bytes())
    config['retain_source_reviews'] = True
    write(spec, config)
    output = build_saved_matrix(spec, tmp_path/'combined').parent
    model = json.loads((output/'matrix.json').read_bytes())
    assert model['execution_settings'] == expected
    from bs4 import BeautifulSoup
    method = BeautifulSoup((output/'methodology/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert method.select_one('#execution-settings')
    assert not BeautifulSoup((output/'index.html').read_text(encoding='utf-8'), 'html.parser').select('.comparison-winner')
    review = BeautifulSoup((output/'review/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert len(review.select('section.section[data-source-review] > .wrap')) == 2
    guide = BeautifulSoup((output/'guide/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert not guide.select('[aria-label] .comparison-winner')

