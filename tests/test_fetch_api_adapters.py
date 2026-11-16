"""Native API projection, category configuration, and remote-only Claude controls."""
import json
from unittest.mock import patch

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.http_client import HttpResponse
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


@pytest.mark.parametrize('track,kind,enabled', [('web', 'body', False), ('image', 'resource', True),
    ('video', 'resource', True), ('video', 'body', True), ('video', 'file', False)])
def test_zai_embedded_media_configuration(track, kind, enabled):
    provider = build_provider('zai', {'type': 'zai_reader', 'api_key': 'test',
        'keep_img_data_url': False, 'embedded_media_keep_img_data_url': True})
    _, args = provider.native_http_arguments(case(track, kind), 30)
    assert args['json_body']['keep_img_data_url'] is enabled


def claude_payload(text='Tool body'):
    return {'content': [{'type': 'server_tool_use', 'name': 'web_fetch', 'input': {'url': case().url}},
        {'type': 'web_fetch_tool_result', 'content': {'type': 'web_fetch_result', 'url': case().url,
         'content': {'type': 'document', 'source': {'type': 'text', 'data': text}}}},
        {'type': 'text', 'text': 'MODEL ANSWER MUST NEVER BE SCORED'}], 'stop_reason': 'end_turn'}


def test_claude_tool_body_only_and_frozen_request():
    provider = build_provider('claude', {**remote_config(), 'uniform_native_parameters': True})
    ledger = NativeRouteLedger([case()], [provider], timeout_seconds=30)
    raw = json.dumps(claude_payload()).encode()
    with patch.object(provider, '_remote_request', return_value=HttpResponse(200, '', {}, raw)):
        result = provider.fetch(case(), 30, native_route_ledger=ledger)
    assert result.ok and result.content == 'Tool body'
    require_native_answer_projection(result.content, result.metadata, raw, request_url=case().url,
                                     title=result.title, final_url=result.final_url)


@pytest.mark.parametrize('change', ['wrong_url', 'model_only', 'base64', 'last_tool_error'])
def test_claude_rejects_model_or_incomplete_tool_output(change):
    payload = claude_payload()
    if change == 'wrong_url':
        payload['content'][0]['input']['url'] = 'https://other.example'
    elif change == 'model_only':
        payload['content'] = [payload['content'][-1]]
    elif change == 'base64':
        payload['content'][1]['content']['content']['source'] = {'type': 'base64', 'data': 'AAAA'}
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
