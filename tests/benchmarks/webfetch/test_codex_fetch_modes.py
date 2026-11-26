import base64
import json
import shutil
from types import SimpleNamespace

import pytest

from harness.web_fetch.providers.codex import parse_returns


def trace(targets):
    events, session = [], []
    for i, target in enumerate(targets):
        events.append({"type": "item.completed", "item": {
            "type": "web_search", "results": [{"ref_id": f"view{i}"}]}})
        call = {"type": "custom_tool_call", "name": "exec", "call_id": str(i),
                "input": 'const r = await tools.web__run({open:[{ref_id:"' + target +
                         '"}],response_length:"long"}); text(r)'}
        output = {"type": "custom_tool_call_output", "call_id": str(i),
                  "output": [{"text": f"L{i}: paragraph {i}"}]}
        session.extend({"type": "response_item", "payload": p} for p in (call, output))
    session.append({"type": "response_item", "payload": {"type": "message", "text": "model answer"}})
    return events, session


def test_single_open_only_scores_tool_text():
    result = parse_returns("url", "single", *trace(["url"]))
    assert result.ok and result.content == "paragraph 0"
    assert not parse_returns("url", "single", *trace(["url", "view0"])).content


def test_continuation_accepts_only_this_pages_references():
    result = parse_returns("url", "same-page", *trace(["url", "view0"]))
    assert result.ok and result.content == "paragraph 0\nparagraph 1"
    assert not parse_returns("url", "same-page", *trace(["url", "elsewhere"])).ok


@pytest.mark.parametrize('phrase', ['Internal Error', 'Failed to fetch'])
def test_error_words_in_page_body_are_not_transport_failures(phrase):
    events, session = trace(['url'])
    session[1]['payload']['output'][0]['text'] = 'Page header\nL12: How to fix ' + phrase
    assert parse_returns('url','single',events,session).ok
    session[1]['payload']['output'][0]['text'] = 'How to fix ' + phrase + '\nL12: body'
    assert parse_returns('url','single',events,session).ok
    session[1]['payload']['output'][0]['text'] = phrase + '\nL12: body'
    assert not parse_returns('url','single',events,session).ok


@pytest.mark.parametrize('terminated', [False, True])
def test_native_cli_partial_jsonl_retains_diagnostics_and_offsets(tmp_path, monkeypatch, terminated):
    from harness.web_fetch.providers.codex import CodexProvider
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    home=tmp_path/'codex-home';home.mkdir();monkeypatch.setenv('CODEX_HOME',str(home))
    ident='12345678-1234-1234-1234-123456789abc'
    valid=json.dumps({'type':'session_meta','payload':{}}).encode()+b'\n'
    tail=b'{"type":"response_item"'
    binary=tmp_path/'synthetic-codex'
    binary.write_text(f'#!{node}\n'+f"""
const fs=require('node:fs');
fs.mkdirSync(process.env.CODEX_HOME+'/sessions',{{recursive:true}});
fs.writeFileSync(process.env.CODEX_HOME+'/sessions/rollout-{ident}.jsonl',Buffer.from({list(valid+tail)}));
process.stdout.write(JSON.stringify({{type:'thread.started',thread_id:'{ident}'}})+'\\n');
process.stdout.write('{{"type":"item');
{'setInterval(()=>{},1000);' if terminated else ''}
""")
    binary.chmod(0o700)
    provider=CodexProvider('synthetic',{'mode':'single','executable':str(binary)})
    try:
        result=provider.fetch(SimpleNamespace(url='https://example.test/',task_prompt='Read once.'),1)
        assert not result.ok
        assert result.metadata['timed_out'] == terminated
        diagnostic=json.loads(base64.b64decode(result.metadata['_diagnostic_log_b64']))
        assert base64.b64decode(diagnostic['raw_stdout_b64']).endswith(b'{"type":"item')
        if terminated:
            assert base64.b64decode(diagnostic['raw_session_b64']) == valid+tail
            assert provider.offset == len(valid)
            assert provider.process.returncode < 0
        else:
            assert provider.offset == 0
    finally:
        provider.cancel()
        provider.work.cleanup()
