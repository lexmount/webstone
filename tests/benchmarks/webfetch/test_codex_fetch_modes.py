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
    session[1]['payload']['output'][0]['text'] = phrase + ' troubleshooting guide (https://example.test/)\nL12: body'
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
            assert provider.offset == 0 and provider.session is None and provider.thread is None
            assert provider.process.returncode < 0
        else:
            assert provider.offset == 0
    finally:
        provider.cancel()
        provider.work.cleanup()


@pytest.mark.parametrize('terminated', [False, True])
def test_invalid_turn_cannot_contaminate_next_url_on_reused_provider(tmp_path, monkeypatch, terminated):
    from harness.web_fetch.providers.codex import CodexProvider
    node=shutil.which('node')
    if not node: pytest.skip('Node.js is required')
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('CODEX_HOME',str(home))
    second_url='https://second.example.test/'
    events,session=trace([second_url])
    payload={'events':events,'session':session,'terminated':terminated}
    binary=tmp_path/'synthetic-codex'
    binary.write_text(f'#!{node}\n'+r"""
const fs=require('node:fs');const home=process.env.CODEX_HOME;
const payload=JSON.parse(fs.readFileSync(__dirname+'/payload.json'));
const count=fs.existsSync(home+'/count')?Number(fs.readFileSync(home+'/count'))+1:1;
fs.writeFileSync(home+'/count',String(count));
fs.appendFileSync(home+'/arguments.jsonl',JSON.stringify(process.argv.slice(2))+'\n');
const id=count===1?'12345678-1234-1234-1234-123456789abc':'12345678-1234-1234-1234-123456789abd';
fs.mkdirSync(home+'/sessions',{recursive:true});
const meta=JSON.stringify({type:'session_meta',payload:{}})+'\n';
fs.writeFileSync(home+'/sessions/rollout-'+id+'.jsonl',meta+(count===1
  ? '{"type":"response_item"' : payload.session.map(JSON.stringify).join('\n')+'\n'));
process.stdout.write(JSON.stringify({type:'thread.started',thread_id:id})+'\n');
if(count===1){if(payload.terminated)setInterval(()=>{},1000);}
else{
 for(const event of payload.events)process.stdout.write(JSON.stringify(event)+'\n');
 process.stdout.write(JSON.stringify({type:'turn.completed',usage:{input_tokens:100,output_tokens:5}})+'\n');
}
""")
    (tmp_path/'payload.json').write_text(json.dumps(payload));binary.chmod(0o700)
    provider=CodexProvider('synthetic',{'mode':'single','executable':str(binary)})
    try:
        first=provider.fetch(SimpleNamespace(url='https://first.example.test/',task_prompt='Read.'),1)
        assert not first.ok and provider.thread is None and provider.session is None
        second=provider.fetch(SimpleNamespace(url=second_url,task_prompt='Read.'),5)
        assert second.ok and second.content=='paragraph 0'
        arguments=[json.loads(s) for s in (home/'arguments.jsonl').read_text().splitlines()]
        assert len(arguments)==2 and all('resume' not in argv for argv in arguments)
        assert second.metadata['usage_delta']=={'input_tokens':100,'output_tokens':5}
        diagnostic=json.loads(base64.b64decode(first.metadata['_diagnostic_log_b64']))
        assert base64.b64decode(diagnostic['raw_session_b64']).endswith(b'{"type":"response_item"')
    finally:
        provider.cancel();provider.work.cleanup()


def test_timeout_kills_pipe_holding_descendant_after_cli_leader_exits(tmp_path):
    import time
    from harness.web_fetch.providers.codex import CodexProvider

    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    binary = tmp_path / 'synthetic-codex'
    binary.write_text(f'#!{node}\n' + r"""
const {spawn} = require('node:child_process');
spawn(process.execPath, ['-e', 'setTimeout(()=>{},6000)'], {stdio:'inherit'});
process.stdout.write('leader exited\n');
setTimeout(()=>process.exit(0),20);
""", encoding='utf-8')
    binary.chmod(0o700)
    provider = CodexProvider('synthetic', {'mode':'single', 'executable':str(binary)})
    try:
        started = time.monotonic()
        result = provider.fetch(SimpleNamespace(url='https://example.test/', task_prompt='Read.'), 2)
        elapsed = time.monotonic() - started
        assert not result.ok and result.metadata['timed_out']
        diagnostic = json.loads(base64.b64decode(result.metadata['_diagnostic_log_b64']))
        assert b'leader exited' in base64.b64decode(diagnostic['raw_stdout_b64'])
        assert elapsed < 4, f'Pipe-holding descendant survived cancellation: {elapsed}'
    finally:
        provider.cancel()
        provider.work.cleanup()


def test_unresolved_detached_child_quarantines_provider_and_retains_diagnostics(tmp_path, monkeypatch):
    import os
    import signal
    import time
    from harness.web_fetch.providers.codex import CodexProvider
    from harness.web_fetch.providers.base import ProcessCleanupError

    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    pid_path = tmp_path / 'child.pid'
    binary = tmp_path / 'synthetic-codex'
    binary.write_text(f'#!{node}\n' + r"""
const fs = require('node:fs');
const {spawn} = require('node:child_process');
const child = spawn(process.execPath, ['-e', 'setTimeout(()=>{},6000)'], {stdio:'inherit', detached:true});
fs.writeFileSync(__dirname+'/child.pid', String(child.pid));
process.stdout.write('retained diagnostic\n');
setTimeout(()=>process.exit(0),20);
""", encoding='utf-8')
    binary.chmod(0o700)
    monkeypatch.setattr(CodexProvider, 'cancellation_cleanup_seconds', property(lambda _: .2))
    provider = CodexProvider('synthetic', {'mode':'single', 'executable':str(binary)})
    try:
        started = time.monotonic()
        with pytest.raises(ProcessCleanupError) as failed:
            provider.fetch(SimpleNamespace(url='https://example.test/', task_prompt='Read.'), 2)
        result = failed.value.result
        assert time.monotonic() - started < 4
        assert not result.ok and result.metadata['pipe_drain_incomplete']
        diagnostic = json.loads(base64.b64decode(result.metadata['_diagnostic_log_b64']))
        assert b'retained diagnostic' in base64.b64decode(diagnostic['raw_stdout_b64'])
        assert provider.thread is None
        assert provider.process.stdout.closed and provider.process.stderr.closed
        original_pid = provider.process.pid
        with pytest.raises(ProcessCleanupError, match='Prior Codex'):
            provider.fetch(SimpleNamespace(url='https://next.example.test/', task_prompt='Read.'), 5)
        assert provider.process.pid == original_pid
    finally:
        provider.cancel()
        if pid_path.exists():
            try:
                os.killpg(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        provider.work.cleanup()


@pytest.mark.parametrize('first_exit_code', [0, 7])
def test_complete_jsonl_without_turn_completion_cannot_succeed_or_resume(tmp_path, monkeypatch, first_exit_code):
    from harness.web_fetch.providers.codex import CodexProvider
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    home = tmp_path/'home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    first_url, second_url = 'https://first.example.test/', 'https://second.example.test/'
    payload = {'first': dict(zip(('events', 'session'), trace([first_url]))),
               'second': dict(zip(('events', 'session'), trace([second_url]))),
               'first_exit_code': first_exit_code}
    (tmp_path/'payload.json').write_text(json.dumps(payload), encoding='utf-8')
    binary = tmp_path/'synthetic-codex'
    binary.write_text(f'#!{node}\n' + r"""
const fs=require('node:fs'),home=process.env.CODEX_HOME;
const payload=JSON.parse(fs.readFileSync(__dirname+'/payload.json'));
const first=!fs.existsSync(home+'/called');fs.writeFileSync(home+'/called','yes');
fs.appendFileSync(home+'/arguments.jsonl',JSON.stringify(process.argv.slice(2))+'\n');
const id=first?'12345678-1234-1234-1234-123456789abc':'12345678-1234-1234-1234-123456789abd';
const data=first?payload.first:payload.second;
fs.mkdirSync(home+'/sessions',{recursive:true});
fs.writeFileSync(home+'/sessions/rollout-'+id+'.jsonl',data.session.map(JSON.stringify).join('\n')+'\n');
process.stdout.write(JSON.stringify({type:'thread.started',thread_id:id})+'\n');
for(const event of data.events)process.stdout.write(JSON.stringify(event)+'\n');
if(!first)process.stdout.write(JSON.stringify({type:'turn.completed',usage:{input_tokens:100,output_tokens:5}})+'\n');
process.exitCode=first?payload.first_exit_code:0;
""", encoding='utf-8')
    binary.chmod(0o700)
    provider = CodexProvider('synthetic', {'mode':'single', 'executable':str(binary)})
    try:
        first = provider.fetch(SimpleNamespace(url=first_url, task_prompt='Read.'), 5)
        assert not first.ok
        assert not first.metadata['turn_completed']
        assert provider.thread is None and provider.session is None and provider.offset == 0
        diagnostic = json.loads(base64.b64decode(first.metadata['_diagnostic_log_b64']))
        assert diagnostic['session'] and diagnostic['stdout']
        second = provider.fetch(SimpleNamespace(url=second_url, task_prompt='Read.'), 5)
        assert second.ok and second.metadata['turn_completed']
        arguments = [json.loads(s) for s in (home/'arguments.jsonl').read_text().splitlines()]
        assert len(arguments) == 2 and all('resume' not in argv for argv in arguments)
        assert second.metadata['usage_delta'] == {'input_tokens':100, 'output_tokens':5}
    finally:
        provider.cancel()
        provider.work.cleanup()
