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
@pytest.mark.parametrize('product', ['moli', 'lexmount'])
def test_completed_moli_collection_scores_saved_bytes_only(tmp_path, monkeypatch, damage, product):
    from harness.web_fetch.lexmount_collection import score_cli_collection
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
    contract = {'mode':product+'-cli-receipt',
        'request': {'argv': ['fetch', '--dump', 'markdown']}, 'binary_sha256': '0'*64}
    if damage:
        expected = {'changed_answer': 'integrity failure',
                    'missing_terminal': 'Missing completed call evidence',
                    'second_provider_call': 'exactly one actual Moli invocation'}[damage]
        with pytest.raises(ValueError, match=expected):
            score_cli_collection(tmp_path, dataset, rows, contract)
        assert not (tmp_path / 'scored').exists()
        return
    summary = score_cli_collection(tmp_path, dataset, rows, contract)
    assert summary['passed'] == 1 and summary['provider_calls_added'] == 0
    result = json.loads((tmp_path / 'scored/results.jsonl').read_text())
    assert result['provider'] == ('codex-moli-local-default' if product=='moli' else 'codex-lexmount-official-cli')
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


@pytest.mark.parametrize('expired', [False, True])
@pytest.mark.parametrize('product', ['moli', 'lexmount'])
def test_blocking_mcp_capture_waits_and_emits_only_one_final_receipt(tmp_path, expired, product):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    binary = tmp_path / 'synthetic-product'
    binary.write_text(f'#!{node}\n' + """
const fs=require('node:fs');
fs.appendFileSync(__dirname+'/invocations','started\\n');
setTimeout(()=>{process.stdout.write('# retained body\\n[link](https://example.test/)');},1200);
""")
    binary.chmod(0o700)
    run = tmp_path / 'run'; run.mkdir(); (run/'dispatch').mkdir()
    (run/'contract.json').write_text(json.dumps({'mode':product+'-cli-receipt'}))
    slot = 'a'*32; work = run/slot; work.mkdir()
    url = 'https://example.test/'
    deadline = int((time.time() + (0.7 if expired else 10))*1000)
    answer = work/'answer.md'
    (work/'contract.json').write_text(json.dumps(dict(mode=product+'-cli',url=url,
        executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        deadline=deadline,answer=str(answer),answerPath=str(answer),
        stderr=str(work/'debug.log'),events=str(work/'events.jsonl'))))
    key = hashlib.sha256(url.encode()).hexdigest()
    (run/'dispatch'/f'{key}.json').write_text(json.dumps(dict(url=url,slot=slot,deadline=deadline)))
    request = {'jsonrpc':'2.0','id':2,'method':'tools/call',
               'params':{'name':'fetch','arguments':{'url':url}}}
    startup = [{'jsonrpc':'2.0','id':0,'method':'initialize',
                'params':{'protocolVersion':'2025-06-18'}},
               {'jsonrpc':'2.0','id':1,'method':'tools/list'}]
    started = time.monotonic()
    command = [node,str(BRIDGE),str(run),'--receipt-tool']
    if product == 'lexmount':
        env = tmp_path/'synthetic.env'
        env.write_text('LEXMOUNT_API_KEY=synthetic\nLEXMOUNT_PROJECT_ID=synthetic\n')
        command.append(str(env))
    result = subprocess.run(command,
        input=('\n'.join(json.dumps(x) for x in [*startup,request])+'\n').encode(),
        capture_output=True,timeout=15,check=True)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 3 and result.stderr == b''
    assert responses[0]['result']['serverInfo']['name'] == 'capture'
    assert [x['name'] for x in responses[1]['result']['tools']] == ['fetch']
    receipt = json.loads(responses[2]['result']['content'][0]['text'])
    assert receipt == {'status':'failed' if expired else 'saved','path':str(answer)}
    events = [json.loads(line) for line in (work/'events.jsonl').read_text().splitlines()]
    assert sum(e['event']=='client_started' for e in events) == 1
    if not expired:
        assert (tmp_path/'invocations').read_text() == 'started\n'
        assert time.monotonic()-started >= 1.2
        assert '[link](https://example.test/)' in answer.read_text()
        assert 'retained body' not in result.stdout.decode()
    else:
        assert json.loads((work/'status.json').read_text())['signal'] == 'SIGKILL'


@pytest.mark.parametrize('damage', [None,'extra_wait','extra_message_tool','body_returned',
                                    'wrong_url','duplicate_product','final_message',
                                    'envelope_body','missing_usage'])
def test_receipt_protocol_rejects_unnecessary_agent_work(damage):
    from harness.web_fetch.lexmount_collection import receipt_protocol_issues
    tool = 'mcp__webstone_capture__fetch'
    url = 'https://example.test/'
    terminal = dict(calls=[{'name':tool,'input':json.dumps({'url':url})}],
        tool_outputs=[json.dumps({'content':[{'type':'text','text':json.dumps(
            {'status':'saved','path':'answer.md'})}]})],
        terminal_type='task_complete',usage={'total_tokens':42},final_text='',
        provider_invocations=1,capture_invocations=1)
    if damage in ('extra_wait','extra_message_tool'):
        terminal['calls'].append({'name':'wait' if damage=='extra_wait' else 'send_message','input':'{}'})
    elif damage=='body_returned':
        terminal['tool_outputs']=[json.dumps({'status':'saved','path':'answer.md','body':'page text'})]
    elif damage=='wrong_url':
        terminal['calls'][0]['input']=json.dumps({'url':'https://wrong.test/'})
    elif damage=='duplicate_product':
        terminal['provider_invocations']=2
    elif damage=='final_message':
        terminal['final_text']='done'
    elif damage=='envelope_body':
        envelope=json.loads(terminal['tool_outputs'][0]);envelope['body']='page text'
        terminal['tool_outputs']=[json.dumps(envelope)]
    elif damage=='missing_usage':
        terminal['usage']=None
    assert bool(receipt_protocol_issues(terminal,url,tool)) == bool(damage)


def test_blocking_api_capture_preserves_complete_response(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    payload = b'{"body":"complete body","links":["https://example.test/detail"]}'
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        (tmp_path/'contract.json').write_text(json.dumps({'mode':'lexmount-api-receipt'}))
        env = tmp_path/'synthetic.env'
        env.write_text('LEXMOUNT_API_KEY=synthetic\nLEXMOUNT_PROJECT_ID=synthetic\n')
        work = tmp_path/('b'*32); work.mkdir(); (tmp_path/'dispatch').mkdir()
        url = 'https://example.test/'
        deadline = int((time.time()+10)*1000)
        (work/'contract.json').write_text(json.dumps(dict(mode='lexmount-api',url=url,
            deadline=deadline,endpoint=f'http://127.0.0.1:{server.server_port}',
            events=str(work/'events.jsonl'),answerPath=str(work/'response.bin'))))
        key = hashlib.sha256(url.encode()).hexdigest()
        (tmp_path/'dispatch'/f'{key}.json').write_text(json.dumps(dict(
            url=url,slot=work.name,deadline=deadline)))
        request = dict(jsonrpc='2.0',id=1,method='tools/call',
            params={'name':'fetch','arguments':{'url':url}})
        result = subprocess.run([node,str(BRIDGE),str(tmp_path),'--receipt-tool',str(env)],
            input=json.dumps(request).encode()+b'\n',capture_output=True,timeout=15,check=True)
        reply = json.loads(result.stdout)
        assert json.loads(reply['result']['content'][0]['text']) == {
            'status':'saved','path':str(work/'response.bin')}
        assert (work/'response.bin').read_bytes() == payload
        assert requests == [{'extract':{'url':url,'options':{'engine_preference':'auto'}}}]
        assert b'complete body' not in result.stdout
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('extra_call', [False, True])
def test_preflight_admission_rechecks_actual_calls(tmp_path, monkeypatch, extra_call):
    from harness.web_fetch import lexmount_collection as collector
    monkeypatch.setenv('WEBSTONE_DATA_HOME',str(tmp_path))
    experiments=tmp_path/'experiments';experiments.mkdir()
    dataset=experiments/'codex-luna-single-530-20261122/dataset.json'
    dataset.parent.mkdir();dataset.write_text('{}')
    run=experiments/'moli-local-530-synthetic';run.mkdir()
    binary=tmp_path/'synthetic-binary';binary.write_bytes(b'never executed')
    tool='mcp__webstone_capture__fetch'
    contract=dict(executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        bridge_sha256=hashlib.sha256(BRIDGE.read_bytes()).hexdigest(),
        collection_sha256=hashlib.sha256(Path(collector.__file__).read_bytes()).hexdigest(),
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),admitted=False,
        model='gpt-6-luna',reasoning_effort='medium',tool_name=tool,receipt_protocol='single_blocking_tool_v1')
    (run/'contract.json').write_text(json.dumps(contract))
    rows=[]
    for i in range(2):
        slot=str(i);work=run/slot;work.mkdir();url=f'https://example.test/{i}'
        rows.append(dict(scope='preflight',slot=slot,url=url))
        terminal=dict(response_saved=True,usage={'total_tokens':42},final_text='',
            model='gpt-6-luna',effort='medium',started_at=0,ended_at='1970-01-01T00:00:01Z',
            terminal_type='task_complete',provider_invocations=1,capture_invocations=1,
            calls=[{'name':tool,'input':json.dumps({'url':url})}],
            tool_outputs=[json.dumps({'status':'saved','path':'answer.md'})],protocol_issues=[])
        if extra_call and i==0:
            terminal['calls'].append({'name':'wait','input':'{}'})
        (work/'terminal.json').write_text(json.dumps(terminal))
    (run/'cases.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
    if extra_call:
        with pytest.raises(ValueError,match='receipt-only preflight'):
            collector.api_collection_main(['moli-admit','--run',str(run)])
        assert not json.loads((run/'contract.json').read_text())['admitted']
    else:
        collector.api_collection_main(['moli-admit','--run',str(run)])
        assert json.loads((run/'contract.json').read_text())['admitted']


@pytest.mark.parametrize('product', ['moli', 'cli'])
def test_initialization_freezes_scoring_and_blocking_tool_before_calls(tmp_path, monkeypatch, capsys, product):
    from harness.web_fetch import lexmount_collection as collector
    from lexbench_webfetch.scoring import SCORING_SOURCE_FILES, SCORING_SOURCE_SHA256
    monkeypatch.setenv('WEBSTONE_DATA_HOME',str(tmp_path))
    monkeypatch.setattr(collector,'_inputs',lambda home:[('synthetic','https://example.test/')])
    monkeypatch.setattr(collector,'repository_env',lambda:{'LEXMOUNT_API_KEY':'synthetic','LEXMOUNT_PROJECT_ID':'synthetic'})
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Initialization invoked a product'))
    (tmp_path/'experiments').mkdir()
    name='moli-local-530-synthetic' if product=='moli' else 'lexmount-cli-530-synthetic'
    run=tmp_path/'experiments'/name
    binary=tmp_path/'synthetic-binary';binary.write_bytes(b'never executed')
    collector.api_collection_main([product+'-init','--run',str(run),'--binary',str(binary),
        '--binary-sha256',hashlib.sha256(binary.read_bytes()).hexdigest()])
    output=json.loads(capsys.readouterr().out)
    assert output['mcp_server']['tool_timeout_sec']==300
    assert output['mcp_server']['enabled_tools']==['fetch']
    assert output['mcp_server']['required']
    frozen=json.loads((run/'scoring-contract.json').read_text())
    assert frozen['scoring_source_sha256']==SCORING_SOURCE_SHA256
    for source in SCORING_SOURCE_FILES:
        assert (run/'private_artifacts/scoring-source'/source.name).read_bytes()==source.read_bytes()
    assert not json.loads((run/'contract.json').read_text())['admitted']
