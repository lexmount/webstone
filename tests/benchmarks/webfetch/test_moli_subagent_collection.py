"""Receipt-only local Moli capture and frozen offline scoring boundaries."""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

BRIDGE = Path(__file__).parents[3] / "src/harness/web_fetch/providers/codex_capture.cjs"


@pytest.mark.parametrize('damage', [None, 'changed_answer', 'missing_terminal', 'second_provider_call'])
def test_completed_moli_collection_scores_saved_bytes_only(tmp_path, monkeypatch, damage):
    from harness.web_fetch.lexmount_collection import score_moli_collection
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256

    # A saved response can pass without running any executable or using credentials.
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Scoring started a process'))
    dataset = tmp_path / 'dataset.json'
    case = dict(id='synthetic', name='Synthetic', url='https://example.test/',
                category='synthetic', expected_any=['saved text'])
    case['content_scopes'] = {'version': 1, **{scope: {
        'requirements': {'task_prompt': 'Return the saved text.',
            'required_patterns': [{'id': 'saved', 'pattern': 'saved text'}]},
        'evidence': {'scope': 'synthetic reference', 'source_sha256': '0'*64},
    } for scope in ('main', 'full')}}
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'cases': [case]}))
    (tmp_path / 'scoring-contract.json').write_text(json.dumps({
        'scoring_source_sha256': SCORING_SOURCE_SHA256}))
    work = tmp_path / 'slot'; work.mkdir()
    raw = b'# saved text\n\n[link](https://example.test/detail)\n'
    (work / 'answer.md').write_bytes(raw)
    (work / 'debug.log').write_bytes(b'')
    (work / 'called').touch()
    (work / 'status.json').write_text(json.dumps({'code': 0, 'bytes': len(raw),
        'elapsed_ms': 100, 'sha256': hashlib.sha256(raw).hexdigest()}))
    terminal = dict(terminal_type='task_complete', usage={'total_tokens': 42},
        session_id='synthetic-session', session_started_at='2026-11-25T00:00:00Z',
        ended_at='2026-11-25T00:00:01Z')
    (work / 'terminal.json').write_text(json.dumps(terminal))
    events = [{'event': 'client_started'},
              {'event': 'receipt_sent', 'timestamp': '2026-11-25T00:00:00.8Z'}]
    if damage == 'changed_answer':
        (work / 'answer.md').write_bytes(b'changed')
    elif damage == 'missing_terminal':
        (work / 'terminal.json').unlink()
    elif damage == 'second_provider_call':
        events.append({'event': 'client_started'})
    (work / 'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
    rows = [dict(scope='formal', case_id='synthetic', agent='synthetic-agent',
                 slot='slot', url=case['url'])]
    contract = {'request': {'argv': ['fetch', '--dump', 'markdown']}, 'binary_sha256': '0'*64}
    if damage:
        expected = {'changed_answer': 'integrity failure',
                    'missing_terminal': 'Missing completed call evidence',
                    'second_provider_call': 'exactly one actual Moli invocation'}[damage]
        with pytest.raises(ValueError, match=expected):
            score_moli_collection(tmp_path, dataset, rows, contract)
        assert not (tmp_path / 'scored').exists()
        return
    summary = score_moli_collection(tmp_path, dataset, rows, contract)
    assert summary['passed'] == 1 and summary['provider_calls_added'] == 0
    result = json.loads((tmp_path / 'scored/results.jsonl').read_text())
    assert (tmp_path / 'scored' / result['artifact_path']).read_bytes() == raw
    assert result['provider_metadata']['subagent_usage']['total_tokens'] == 42
    assert result['metrics']['content_sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('failure', [False, True])
def test_local_moli_capture_preserves_native_output_and_returns_receipt_only(tmp_path, failure):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    binary = tmp_path / 'moli'
    payload = b'# Page\n[Named link](https://example.test/detail)\n' * 10000
    (tmp_path / 'payload').write_bytes(payload)
    binary.write_text(f'#!{node}\n' + """
const fs=require('node:fs');
fs.appendFileSync(__dirname+'/invocations',JSON.stringify(process.argv.slice(2))+'\\n');
process.stdout.write(fs.readFileSync(__dirname+'/payload'));
process.stderr.write('debug process detail');
process.exitCode=process.argv.at(-1).endsWith('/failure')?1:0;
""", encoding='utf-8')
    binary.chmod(0o700)
    url = 'https://example.test/' + ('failure' if failure else 'success')
    answer = tmp_path / 'answer.md'
    (tmp_path / 'contract.json').write_text(json.dumps({
        'mode': 'moli-cli', 'url': url, 'executable': str(binary),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'deadline': int((time.time() + 20) * 1000), 'answer': str(answer),
        'stderr': str(tmp_path / 'debug.log'), 'events': str(tmp_path / 'events.jsonl'),
    }), encoding='utf-8')
    command = [node, str(BRIDGE), str(tmp_path), '--moli-url', url]
    result = subprocess.run(command, capture_output=True, check=True, timeout=10)
    assert result.stderr == b''
    assert json.loads(result.stdout) == {'status': 'failed' if failure else 'saved', 'path': str(answer)}
    assert answer.read_bytes() == payload
    assert (tmp_path/'debug.log').read_text() == 'debug process detail'
    status = json.loads((tmp_path/'status.json').read_text())
    assert status['code'] == int(failure)
    assert status['sha256'] == hashlib.sha256(payload).hexdigest()
    repeated = subprocess.run(command, capture_output=True, check=True, timeout=10)
    assert json.loads(repeated.stdout)['status'] == 'failed'
    assert len((tmp_path/'invocations').read_text().splitlines()) == 1
    assert json.loads((tmp_path/'invocations').read_text()) == [
        'fetch', '--dump', 'markdown', '--log-level', 'debug', url]
