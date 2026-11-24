"""Native API projection, category configuration, and remote-only Claude controls."""
import json
import os
import time
import hashlib
import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.http_client import HttpResponse
from harness.web_fetch.native_projection import require_native_answer_projection
from harness.web_fetch.native_routes import NativeRouteLedger


def case(track='web', task_kind='body'):
    return EvalCase('test', 'Test', 'https://example.com', 'static', [], track=track,
                    task_kind=task_kind, source_benchmark={'input_media_kind': 'html_page'})


def remote_config():
    return {'type': 'claude_web_fetch', 'ssh_host': 'user@192.0.2.83',
            'ssh_server_ip': '192.0.2.83', 'remote_worker': '/remote/worker.py',
            'remote_env_file': '/remote/repository/.env', 'remote_uv': '/remote/bin/uv'}


@pytest.mark.parametrize('kind,payload,expected', [
    ('kimi_fetch', {'markdown': 'Native body', 'title': 'Native title', 'url': 'https://example.com'}, 'Native body'),
    ('zai_reader', {'reader_result': {'content': 'Native body', 'title': 'Native title', 'url': 'https://example.com'}}, 'Native body'),
    ('kimi_fetch', {'content': 'Wrong field'}, ''),
    ('zai_reader', {'content': 'Wrong field'}, ''),
])
def test_native_fields_and_no_envelope_scoring(kind, payload, expected):
    provider = build_provider(kind, {'type': kind, 'api_key': 'test'})
    raw = json.dumps(payload).encode()
    with patch('harness.web_fetch.providers.hosted_fetch.request', return_value=HttpResponse(200, '', {}, raw)):
        result = provider.fetch(case(), 30)
    assert result.content == expected
    require_native_answer_projection(result.content, result.metadata, raw, title=result.title,
                                     final_url=result.final_url, request_url=case().url)


@pytest.mark.parametrize('kind', ['kimi_fetch', 'zai_reader', 'claude_web_fetch'])
@pytest.mark.parametrize('raw', [b'error code: 504\n', b'[]', b'null'])
def test_malformed_api_envelope_remains_failure_evidence(kind, raw, tmp_path):
    from dataclasses import asdict
    from harness.web_fetch.artifacts import verify_record_artifacts
    from harness.web_fetch.runner import run_evaluation
    provider = build_provider(kind, {**(remote_config() if kind == 'claude_web_fetch' else {'type':kind,'api_key':'test'}),
                                     'uniform_native_parameters': True})
    dataset = {'tool': 'web_fetch', 'id': 'fixture', 'cases': [asdict(case())]}
    source = tmp_path/'dataset.json'
    source.write_text(json.dumps(dataset), encoding='utf-8')
    transport = (patch.object(provider,'_remote_request',return_value=HttpResponse(504,'',{},raw))
                 if kind == 'claude_web_fetch' else patch('harness.web_fetch.providers.hosted_fetch.request',
                    return_value=HttpResponse(504, '', {}, raw)))
    with transport:
        records, _ = run_evaluation(output_dir=tmp_path/'run', dataset_meta=dataset,
            dataset_path=source, cases=[case()], providers=[provider],
            provider_config_path=tmp_path/'providers.json', runs=1, timeout=30,
            delay=0, reference_policy='frozen_existing')
    record = records[0]
    assert not record['ok']
    assert not record['provider_metadata'].get('native_answer_projection')
    cases = {case().id: asdict(case())}
    verify_record_artifacts(tmp_path/'run', record, native_cases=cases)
    assert (tmp_path/'run'/record['artifact_path']).read_bytes() == b''
    # An undeclared answer must still fail at the public aggregation boundary.
    record['ok'] = True
    with pytest.raises(ValueError, match='no projection declaration'):
        verify_record_artifacts(tmp_path/'run', record, native_cases=cases)


@pytest.mark.parametrize('track,kind,enabled', [('web', 'body', False), ('image', 'resource', True),
    ('video', 'resource', True), ('video', 'body', True), ('video', 'file', False)])
def test_zai_embedded_media_configuration(track, kind, enabled):
    provider = build_provider('zai', {'type': 'zai_reader', 'api_key': 'test',
        'keep_img_data_url': False, 'embedded_media_keep_img_data_url': True})
    _, args = provider.native_http_arguments(case(track, kind), 30)
    assert args['json_body']['keep_img_data_url'] is enabled


def claude_payload(text='Tool body'):
    return {'content': [{'type': 'server_tool_use', 'id':'fetch-001', 'name': 'web_fetch', 'input': {'url': case().url}},
        {'type': 'web_fetch_tool_result', 'tool_use_id':'fetch-001', 'content': {'type': 'web_fetch_result', 'url': case().url,
         'content': {'type': 'document', 'source': {'type': 'text', 'data': text}}}},
        {'type': 'text', 'text': 'MODEL ANSWER MUST NEVER BE SCORED'}], 'stop_reason': 'end_turn'}


def test_claude_tool_body_only_and_frozen_request():
    provider = build_provider('claude', {**remote_config(), 'uniform_native_parameters': True})
    ledger = NativeRouteLedger([case()], [provider], timeout_seconds=30)
    raw = json.dumps(claude_payload()).encode()
    with patch.object(provider, '_remote_request', return_value=HttpResponse(200, '', {}, raw)):
        result = provider.fetch(case(), 30, native_route_ledger=ledger)
    assert result.ok and result.content == 'Tool body'
    assert result.metadata['execution_location'] == 'configured_ssh_worker'
    assert '192.0.2.83' not in json.dumps(result.metadata)
    require_native_answer_projection(result.content, result.metadata, raw, request_url=case().url,
                                     title=result.title, final_url=result.final_url)


@pytest.mark.parametrize('change', ['wrong_url', 'model_only', 'base64', 'last_tool_error',
    'missing_tool_id', 'wrong_tool_id', 'duplicate_tool_id', 'result_before_invocation'])
def test_claude_rejects_model_or_incomplete_tool_output(change):
    payload = claude_payload()
    if change == 'wrong_url':
        payload['content'][0]['input']['url'] = 'https://other.example'
    elif change == 'model_only':
        payload['content'] = [payload['content'][-1]]
    elif change == 'base64':
        payload['content'][1]['content']['content']['source'] = {'type': 'base64', 'data': 'AAAA'}
    elif change == 'missing_tool_id':
        payload['content'][1].pop('tool_use_id')
    elif change == 'wrong_tool_id':
        payload['content'][1]['tool_use_id'] = 'unrelated-call'
    elif change == 'duplicate_tool_id':
        payload['content'].insert(1, dict(payload['content'][0]))
    elif change == 'result_before_invocation':
        payload['content'][:2] = reversed(payload['content'][:2])
    else:
        payload['content'].append({'type': 'web_fetch_tool_result',
                                  'content': {'type': 'web_fetch_tool_result_error', 'error_code': 'timeout'}})
    provider = build_provider('claude', remote_config())
    raw = json.dumps(payload).encode()
    with patch.object(provider, '_remote_request', return_value=HttpResponse(200, '', {}, raw)):
        result = provider.fetch(case(), 30)
    assert not result.ok and result.content == ''
    require_native_answer_projection(result.content, result.metadata, raw, request_url=case().url)


def test_model_text_truncation_does_not_discard_complete_tool_body():
    from harness.web_fetch.native_projection import native_answer_projection, project_native_answer
    from dataclasses import asdict
    payload = claude_payload()
    payload['stop_reason'] = 'max_tokens'
    for task in (case(), asdict(case())):
        declaration = native_answer_projection('claude_web_fetch', task, payload, 200)
        assert project_native_answer(payload, declaration)[0] == 'Tool body'


def test_claude_local_fallback_is_impossible():
    provider = build_provider('claude', {'type': 'claude_web_fetch', 'ssh_host': 'localhost'})
    with patch('subprocess.Popen') as process:
        with pytest.raises(ValueError, match='designated SSH host'):
            provider.fetch(case(), 30)
        process.assert_not_called()


@pytest.mark.parametrize('server,host', [('127.0.0.1', 'user@127.0.0.1'),
    ('192.0.2.83', 'user@192.0.2.84'), ('192.0.2.83', '-oProxyCommand=bad@192.0.2.83')])
def test_claude_rejects_local_mismatched_or_option_like_ssh_targets(server, host):
    provider = build_provider('claude', {**remote_config(), 'ssh_server_ip': server, 'ssh_host': host})
    assert provider.validate()


def test_remote_worker_refuses_execution_without_ssh_before_reading_key(monkeypatch):
    from harness.web_fetch.workers.claude_fetch_worker import main
    monkeypatch.delenv('SSH_CONNECTION', raising=False)
    with patch('urllib.request.urlopen') as network, patch('pathlib.Path.read_text') as read:
        with pytest.raises(RuntimeError, match='designated SSH server'):
            main()
        network.assert_not_called()
        read.assert_not_called()


def test_remote_readiness_uses_the_call_deadline():
    from harness.web_fetch.providers.claude_fetch import _read_remote_ready
    read, write = os.pipe()
    try:
        with os.fdopen(read, 'rb') as stream:
            with pytest.raises(TimeoutError, match='call deadline'):
                _read_remote_ready(stream, time.monotonic() + .02)
    finally:
        os.close(write)


def test_ssh_startup_consumes_budget_without_a_fixed_second_deduction(tmp_path):
    from harness.web_fetch.providers import claude_fetch
    from unittest.mock import Mock
    from harness.call_diagnostics import diagnostic_context
    from harness.web_fetch.diagnostics import CallDiagnostics
    provider = build_provider('claude', remote_config())
    worker = Path(claude_fetch.__file__).parents[1]/'workers/claude_fetch_worker.py'
    ready = {'kind': 'claude_fetch_ready', 'ssh_server': '192.0.2.83',
             'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
             'runtime_sources': claude_fetch.runtime_sources(Path(claude_fetch.__file__).parents[3])}
    read, write = os.pipe()
    os.write(write, (json.dumps(ready)+'\n').encode())
    os.close(write)
    process = Mock(returncode=0)
    process.stdout = os.fdopen(read, 'rb')
    process.communicate.return_value = (json.dumps({'ssh_server': '192.0.2.83', 'status': 200,
        'body_b64': base64.b64encode(json.dumps(claude_payload()).encode()).decode(),
        'remote_api_budget_seconds': 29.9}).encode(), b'')
    try:
        diagnostics = CallDiagnostics(tmp_path/'call.jsonl', 'synthetic-call')
        with patch.object(claude_fetch.subprocess, 'Popen', return_value=process), diagnostic_context(diagnostics):
            result = provider.fetch(case(), 30)
        assert result.ok
        envelope = json.loads(process.communicate.call_args.args[0])
        assert 29.5 < envelope['timeout'] <= 30
        assert process.communicate.call_args.kwargs['timeout'] == envelope['timeout']
        diagnostics.finish(result)
        events = [json.loads(line)['event'] for line in (tmp_path/'call.jsonl').read_text(encoding='utf-8').splitlines()]
        assert events == ['remote_http_request_prepared', 'ssh_worker_ready',
                          'ssh_process_completed', 'remote_http_response_received',
                          'http_dns_resolution', 'collection_completed']
    finally:
        process.stdout.close()


def test_remote_deadline_deducts_actual_post_readiness_elapsed_time(monkeypatch, capsys):
    from harness.web_fetch.workers import claude_fetch_worker as worker
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.1 12345 192.0.2.83 22')
    envelope = {'expected_ssh_server_ip': '192.0.2.83',
        'worker_sha256': hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
        'runtime_sources': worker.runtime_sources(Path(worker.__file__).parents[3]),
        'timeout': 27.0, 'remote_env_file': '/remote/.env', 'body': {}, 'max_bytes': 1000}
    response = HttpResponse(200,'https://api.anthropic.com/v1/messages',{},b'{"content":[]}')
    with patch.object(worker.json, 'load', return_value=envelope), \
         patch.object(worker.time, 'monotonic', side_effect=[10.0, 12.0]), \
         patch.object(worker.signal, 'signal'), patch.object(worker.signal, 'setitimer') as timer, \
         patch('harness.env_file.parse_env_file',return_value={'ANTHROPIC_API_KEY':'unit-test-key'}), \
         patch('harness.http_client.request', return_value=response) as request:
        worker.main()
    assert timer.call_args.args[1] == 25.0
    assert request.call_args.kwargs['allow_redirects'] is False
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_remote_deadline_stops_incomplete_open_stdin_without_api_access():
    import subprocess
    import sys
    from harness.web_fetch.workers import claude_fetch_worker as worker
    env = dict(os.environ, SSH_CONNECTION='192.0.2.1 12345 192.0.2.83 22')
    process = subprocess.Popen([sys.executable, worker.__file__, '0.3'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready['kind'] == 'claude_fetch_ready'
        process.stdin.write(b'{"body":')
        process.stdin.flush()
        # Keep stdin open: the remote timer must interrupt json.load itself.
        assert process.wait(timeout=3) != 0
        assert b'Remote call deadline exceeded' in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in [process.stdin, process.stdout, process.stderr]:
            stream.close()


@pytest.mark.parametrize('phase', ['connect_or_headers', 'body'])
def test_remote_transport_failure_retains_phase_without_credentials_or_retry(monkeypatch, capsys, phase):
    import ssl
    import urllib.error
    from harness.web_fetch.workers import claude_fetch_worker as worker
    from harness.call_diagnostics import emit
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.1 12345 192.0.2.83 22')
    envelope = {'expected_ssh_server_ip': '192.0.2.83', 'timeout': 30,
        'remote_env_file': '/remote/.env', 'body': {}, 'max_bytes': 1000,
        'worker_sha256': hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
        'runtime_sources': worker.runtime_sources(Path(worker.__file__).parents[3])}
    def fail(*args, **kwargs):
        emit('http_request_started', headers={'x-api-key': 'unit-test-key'}, json_body={'private': 'body'})
        emit('http_request_failed', phase=phase, error_type='URLError', message='unit-test-key EOF')
        raise urllib.error.URLError(ssl.SSLEOFError('unit-test-key EOF'))
    with patch.object(worker.json, 'load', return_value=envelope), \
         patch.object(worker.signal, 'signal'), patch.object(worker.signal, 'setitimer'), \
         patch('harness.env_file.parse_env_file', return_value={'ANTHROPIC_API_KEY': 'unit-test-key'}), \
         patch('harness.http_client.request', side_effect=fail) as request:
        worker.main()
    request.assert_called_once()
    output = capsys.readouterr().out
    assert 'unit-test-key' not in output and 'private' not in output
    receipt = json.loads(output.splitlines()[-1])
    assert receipt['kind'] == 'claude_fetch_transport_error'
    assert receipt['transport_error']['phase'] == phase
    assert receipt['transport_error']['reason_type'] == 'SSLEOFError'
    assert 'status' not in receipt and 'body_b64' not in receipt
    assert receipt['ssh_server'] == '192.0.2.83'


@pytest.mark.parametrize('failure', ['invalid_key', 'protocol', 'unexpected'])
def test_remote_request_error_boundary_does_not_expose_credentials(monkeypatch, capsys, failure):
    import http.client
    from harness.web_fetch.workers import claude_fetch_worker as worker
    from harness.call_diagnostics import emit
    monkeypatch.setenv('SSH_CONNECTION', '192.0.2.1 12345 192.0.2.83 22')
    key = 'private-test-key\n' if failure == 'invalid_key' else 'private-test-key'
    envelope = {'expected_ssh_server_ip': '192.0.2.83', 'timeout': 30,
        'remote_env_file': '/remote/.env', 'body': {}, 'max_bytes': 1000,
        'worker_sha256': hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
        'runtime_sources': worker.runtime_sources(Path(worker.__file__).parents[3])}
    def fail(*args, **kwargs):
        if failure == 'protocol':
            emit('http_request_failed', phase='body', error_type='IncompleteRead')
            raise http.client.IncompleteRead(b'partial')
        raise ValueError(repr(key.encode()))
    with patch.object(worker.json, 'load', return_value=envelope), \
         patch.object(worker.signal, 'signal'), patch.object(worker.signal, 'setitimer'), \
         patch('harness.env_file.parse_env_file', return_value={'ANTHROPIC_API_KEY': key}), \
         patch('harness.http_client.request', side_effect=fail) as request:
        if failure == 'protocol':
            worker.main()
        else:
            with pytest.raises(RuntimeError) as error:
                worker.main()
            assert 'private-test-key' not in str(error.value)
            assert error.value.__suppress_context__ or failure == 'invalid_key'
    captured = capsys.readouterr()
    assert 'private-test-key' not in captured.out + captured.err
    assert request.call_count == (0 if failure == 'invalid_key' else 1)
    if failure == 'protocol':
        receipt = json.loads(captured.out.splitlines()[-1])
        assert receipt['transport_error']['phase'] == 'body'
        assert receipt['transport_error']['reason_type'] == 'IncompleteRead'
        assert 'status' not in receipt


def test_verified_remote_transport_error_is_not_a_local_adapter_or_api_error():
    from harness.web_fetch.providers.claude_fetch import RemoteClaudeTransportError
    provider = build_provider('claude', remote_config())
    receipt = {'ssh_server': '192.0.2.83', 'transport_error': {
        'phase': 'connect_or_headers', 'reason_type': 'SSLEOFError', 'message': 'Unexpected EOF'}}
    with patch.object(provider, '_remote_request', side_effect=RemoteClaudeTransportError(receipt)) as call:
        result = provider.fetch(case(), 30)
    call.assert_called_once()
    assert not result.ok and result.error_type == 'network' and result.content == ''
    assert result.metadata['execution_location'] == 'configured_ssh_worker'
    assert '192.0.2.83' not in json.dumps(result.metadata)
    assert result.metadata['remote_transport_error']['reason_type'] == 'SSLEOFError'
    assert result.metadata['client_retries'] == 0
    assert 'api_http_status' not in result.metadata


@pytest.mark.parametrize('bad', [{'content':None}, {'content':[None]},
    {'content':[{'type':'server_tool_use','name':'web_fetch','input':None}]}])
def test_claude_malformed_blocks_are_retained_without_an_exception(bad):
    provider=build_provider('claude',remote_config())
    raw=json.dumps(bad).encode()
    with patch.object(provider,'_remote_request',return_value=HttpResponse(200,'',{},raw)):
        result=provider.fetch(case(),30)
    assert not result.ok and result.error_type=='malformed_response'
    assert result.content==''
    assert base64.b64decode(result.metadata['_raw_response_b64'])==raw
    require_native_answer_projection(result.content,result.metadata,raw,
        title=result.title,final_url=result.final_url,request_url=case().url)


@pytest.mark.parametrize('status',[301,302,303])
@pytest.mark.parametrize('destination',['http://other.example.test/','https://other.example.test/'])
def test_remote_worker_does_not_send_a_key_on_an_api_redirect(monkeypatch,capsys,status,destination):
    import io
    import urllib.request
    from urllib.response import addinfourl
    from harness.web_fetch.workers import claude_fetch_worker as worker
    seen=[]
    def respond(request):
        seen.append((request.full_url,dict(request.headers)))
        response=addinfourl(io.BytesIO(b'redirect'),{'Location':destination},request.full_url,
                           code=status if len(seen)==1 else 200)
        response.msg='Found'
        return response
    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self,request):return respond(request)
    class HTTP(urllib.request.HTTPHandler):
        def http_open(self,request):return respond(request)
    original=urllib.request.build_opener
    monkeypatch.setattr(urllib.request,'build_opener',lambda *handlers:
        original(urllib.request.ProxyHandler({}),*handlers,HTTPS(),HTTP()))
    monkeypatch.setenv('SSH_CONNECTION','192.0.2.1 12345 192.0.2.83 22')
    envelope={'expected_ssh_server_ip':'192.0.2.83','timeout':30,'remote_env_file':'/remote/.env',
        'max_bytes':1000,'body':{},'worker_sha256':hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest(),
        'runtime_sources':worker.runtime_sources(Path(worker.__file__).parents[3])}
    with patch.object(worker.json,'load',return_value=envelope), \
         patch('harness.env_file.parse_env_file',return_value={'ANTHROPIC_API_KEY':'unit-test-key'}), \
         patch.object(worker.signal,'signal'),patch.object(worker.signal,'setitimer'):
        worker.main()
    assert len(seen)==1
    assert seen[0][0]=='https://api.anthropic.com/v1/messages'
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status']==status
