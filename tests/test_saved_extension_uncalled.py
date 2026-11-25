"""Retain declared uncalled positions without fabricating saved attempts."""

import hashlib
import json
from copy import deepcopy

import pytest
from bs4 import BeautifulSoup

from harness.web_fetch.report_saved_composition import (
    build_published_extension, check_published_extension,
)
from tests.test_saved_matrix_evidence import build, fixture, write


@pytest.mark.parametrize('damage', [None, 'drop_called', 'mislabel_uncalled'])
def test_ranked_extension_preserves_uncalled_position(tmp_path, damage):
    entries = []
    for index in range(2):
        folder = tmp_path / str(index)
        spec, source, record, _ = fixture(folder)
        provider = 'method-' + str(index)
        spec['providers'] = [{'id': provider, 'label': provider}]
        spec['report_families'] = [{'id': 'web', 'label': 'Static websites'}]
        record.update(provider=provider, attempt_id='called-' + str(index))
        record.setdefault('provider_metadata', {})['subagent_usage'] = {
            'input_tokens': 230, 'cached_input_tokens': 200,
            'output_tokens': 10, 'total_tokens': 240}
        case = json.loads((source / 'dataset.json').read_bytes())['cases'][0]
        second = {**case, 'id': 'second'}
        spec['suites'][0]['dataset'] = write(source / 'dataset.json', {'cases': [case, second]})
        records = [record]
        spec['sources'][0]['case_ids'] = [case['id']]
        if index:
            extra = deepcopy(record)
            extra.update(case_id='second', attempt_id='second-' + str(index),
                         elapsed_ms=1000, ok=False)
            records.append(extra)
            spec['sources'][0]['case_ids'].append('second')
        else:
            identity = {'suite': 'first', 'provider': provider, 'case_id': 'second'}
            receipt = write(folder / 'not-called.json', {**identity,
                'state': 'pre_call_blocked_no_method_calls',
                'native_route_receipt': {'transport_state': 'not_started'}})
            spec['missing'] = [{**identity, 'status': 'not_called',
                'reason': 'Blocked before transport', 'receipt': receipt}]
        path = source / 'results.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in records))
        spec['sources'][0]['results']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        root = build(folder, spec).parent
        if not index:
            home = root / 'index.html'
            token_table = ('<table id="token-usage"><thead><tr>'
                + '<th>Value</th>' * 6 + '</tr></thead><tbody>'
                '<tr data-provider="method-0"><td>Method 0</td><td>1/2</td>'
                '<td data-sort="240">240</td><td data-sort="30">30</td>'
                '<td data-sort="200">200</td><td data-sort="10">10</td>'
                '</tr></tbody></table>')
            home.write_text(home.read_text().replace('</body>', token_table + '</body>'))
        if not index and damage:
            if damage == 'drop_called':
                (root / 'results.jsonl').write_text('')
            else:
                model = json.loads((root / 'matrix.json').read_bytes())
                next(row for row in model['rows'] if row['status'] == 'not_called')['called'] = True
                write(root / 'matrix.json', model)
        audit = root / 'saved-matrix-audit.json'
        value = json.loads(audit.read_bytes())
        value['files'] = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != audit.name}
        write(audit, value)
        entries.append({'root': str(root), 'audit': {
            'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}})
    overview = write(tmp_path / 'methods.json', [{'id': 'method-1', 'label': 'Method 1',
        'execution': 'Fixture', 'configuration': 'Default', 'price': 'Unavailable',
        'billing': 'Synthetic', 'sources': [{'url': 'https://example.com/', 'label': 'Source'}]}])
    spec = {'published_base': entries[0], 'additional_report': entries[1],
        'method_overview': overview, 'comparison_note': 'Saved results.', 'presentation': 'ranked'}
    path, output = tmp_path / 'extension.json', tmp_path / 'extension'
    write(path, spec)
    if damage:
        with pytest.raises(ValueError, match='result population differs'):
            build_published_extension(path, output)
        return
    build_published_extension(path, output)
    assert check_published_extension(output)['valid']
    rows = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
    assert len(rows) == 1
    model = json.loads((output / 'matrix.json').read_bytes())
    assert sum(row['status'] == 'not_called' for row in model['rows']) == 1
    page = BeautifulSoup((output / 'index.html').read_text(), 'html.parser')
    assert len(page.select('#ranking-overall tbody tr')) == 2
    assert '未完成' in page.select_one('#ranking-overall').get_text()
    method = next(row for row in page.select('#ranking-overall tbody tr')
                  if row.select_one('a')['href'] == 'additional/providers/method-1.html')
    assert float(method.find_all('td')[-4]['data-sort']) == 12
    assert 'second' in page.select_one('#saved-missing').get_text()
    assert len(page.select('#method-overview tbody tr')) == 2
    assert len(page.select('#method-overview thead th')) == 3
    assert all(len(row.find_all('td')) == 3 for row in page.select('#method-overview tbody tr'))
    token_rows = page.select('#token-usage tbody tr')
    assert len(token_rows) == 2
    assert token_rows[0].find_all('td')[2].get_text() == '240'
    assert token_rows[1].find_all('td')[2].get_text() == '480'
    analysis = BeautifulSoup((output / 'analysis/index.html').read_text(), 'html.parser')
    assert len(analysis.select('#filtered-table tbody tr')) == 3
    timings = next(row for row in analysis.select('#all-final-call-latency tbody tr')
                   if row.select_one('a')['href'] == '../additional/providers/method-1.html')
    assert '506 ms' in timings.get_text()
