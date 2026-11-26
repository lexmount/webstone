"""Receipt-only local Moli capture and frozen offline scoring boundaries."""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

BRIDGE = Path(__file__).parents[3] / "src/harness/web_fetch/providers/codex_capture.cjs"


@pytest.mark.parametrize('damage', [None, 'changed_answer', 'missing_terminal', 'second_provider_call', 'wrong_model', 'missing_trace', 'changed_trace', 'forged_terminal'])
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
    terminal = dict(terminal_type='task_complete', usage={'input_tokens':40,'cached_input_tokens':30,'output_tokens':2,'total_tokens':42},
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
    if damage=='wrong_model':
        script='frozen capture script'
        rows[0]['caller_script']=script
        contract.update(receipt_protocol='single_blocking_script_v1',
                        model='gpt-6-luna',reasoning_effort='medium')
        terminal.update(calls=[{'name':'exec','input':script}],
            tool_outputs=[json.dumps({'status':'saved','path':'answer.md'})],
            provider_invocations=1,capture_invocations=1,
            caller_contexts=[{'model':'another-model','effort':'medium'}])
        (work/'terminal.json').write_text(json.dumps(terminal))
    if damage in ('missing_trace', 'changed_trace', 'forged_terminal'):
        from harness.web_fetch.lexmount_collection import _trace_terminal
        script = 'frozen capture script'
        rows[0]['caller_script'] = script
        contract.update(receipt_protocol='single_blocking_script_v1',
                        model='gpt-6-luna', reasoning_effort='medium')
        data = [('session_meta', {'id':'synthetic-session', 'source':{'subagent':{
            'thread_spawn':{'agent_path':'/root/synthetic-agent'}}}}),
            ('event_msg', {'type':'task_started'}),
            ('turn_context', {'model':'gpt-6-luna','effort':'medium'}),
            ('response_item', {'type':'custom_tool_call','name':'exec','input':script}),
            ('response_item', {'type':'custom_tool_call_output','output':json.dumps({
                'status':'saved','path':'answer.md'})}),
            ('event_msg', {'type':'token_count','info':{'total_token_usage':terminal['usage']}}),
            ('event_msg', {'type':'task_complete','last_agent_message':''})]
        trace = [{'type':kind,'payload':payload,'timestamp':
                  '2026-11-25T00:00:01Z' if i==len(data)-1 else '2026-11-25T00:00:00Z'}
                 for i,(kind,payload) in enumerate(data)]
        snapshot = tmp_path/'trace.jsonl'
        snapshot.write_text(''.join(json.dumps(e)+'\n' for e in trace))
        terminal.update(_trace_terminal(trace),provider_invocations=1,capture_invocations=1,
                        session_snapshot='trace.jsonl',
                        session_snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        if damage=='missing_trace': snapshot.unlink()
        elif damage=='changed_trace': snapshot.write_text('{}\n')
        else:
            terminal['usage']={**terminal['usage'],'input_tokens':41,'total_tokens':43}
        (work/'terminal.json').write_text(json.dumps(terminal))
    if damage:
        expected = {'missing_trace':'trace integrity failure',
                    'changed_trace':'trace integrity failure',
                    'forged_terminal':'trace projection mismatch',
                    'changed_answer': 'integrity failure',
                    'missing_terminal': 'Missing completed call evidence',
                    'second_provider_call': 'exactly one actual Moli invocation',
                    'wrong_model': 'Receipt-only caller protocol violation'}[damage]
        with pytest.raises(ValueError, match=expected):
            score_cli_collection(tmp_path, dataset, rows, contract)
        assert not (tmp_path / 'scored').exists()
        return
    rows[0]['inherited_source']={'contract_sha256':'a'*64}
    contract['collection_sha256']='b'*64
    summary = score_cli_collection(tmp_path, dataset, rows, contract)
    assert summary['passed'] == 1 and summary['provider_calls_added'] == 0
    result = json.loads((tmp_path / 'scored/results.jsonl').read_text())
    assert result['provider'] == ('codex-moli-local-default' if product=='moli' else 'codex-lexmount-official-cli')
    assert (tmp_path / 'scored' / result['artifact_path']).read_bytes() == raw
    assert result['provider_metadata']['subagent_usage']['total_tokens'] == 42
    assert result['provider_metadata']['inherited_capture']
    assert result['provider_metadata']['caller_implementation_sha256'] is None
    assert result['provider_metadata']['source_collection_contract_sha256']=='a'*64
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
def test_capture_program_waits_and_emits_only_one_final_receipt(tmp_path, expired, product):
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
    work = tmp_path / 'work'; work.mkdir()
    url = 'https://example.test/'
    deadline = int((time.time() + (0.7 if expired else 10))*1000)
    answer = work/'answer.md'
    (work/'contract.json').write_text(json.dumps(dict(mode=product+'-cli',url=url,
        executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        deadline=deadline,answer=str(answer),answerPath=str(answer),
        stderr=str(work/'debug.log'),events=str(work/'events.jsonl'))))
    started = time.monotonic()
    if product == 'moli':
        command = [node,str(BRIDGE),str(work),'--moli-url',url]
    else:
        env = tmp_path/'synthetic.env'
        env.write_text('LEXMOUNT_API_KEY=synthetic\nLEXMOUNT_PROJECT_ID=synthetic\n')
        command = [node,str(BRIDGE),str(work),str(env),'--invoke-url',url]
    result = subprocess.run(command,capture_output=True,timeout=15,check=True)
    assert result.stderr == b''
    assert json.loads(result.stdout) == {'status':'failed' if expired else 'saved','path':str(answer)}
    events = [json.loads(line) for line in (work/'events.jsonl').read_text().splitlines()]
    assert sum(e['event']=='client_started' for e in events) == 1
    if not expired:
        assert (tmp_path/'invocations').read_text() == 'started\n'
        assert time.monotonic()-started >= 1.2
        assert '[link](https://example.test/)' in answer.read_text()
        assert 'retained body' not in result.stdout.decode()
    else:
        assert json.loads((work/'status.json').read_text())['signal'] == 'SIGKILL'


@pytest.mark.parametrize('damage', [None,'empty_transport_blocks','extra_wait','extra_message_tool','body_returned',
                                    'wrong_url','duplicate_product','final_message',
                                    'envelope_body','missing_usage','partial_usage','inconsistent_usage'])
def test_receipt_protocol_rejects_unnecessary_agent_work(damage):
    from harness.web_fetch.lexmount_collection import receipt_protocol_issues
    script = 'frozen capture script'
    terminal = dict(calls=[{'name':'exec','input':script}],
        tool_outputs=[[{'type':'input_text','text':'Script completed\nWall time 36.6 seconds\nOutput:\n'},
                       {'type':'input_text','text':json.dumps({'status':'saved','path':'answer.md'})}]],
        terminal_type='task_complete',usage={'input_tokens':40,'cached_input_tokens':30,'output_tokens':2,'total_tokens':42},final_text='',
        provider_invocations=1,capture_invocations=1)
    if damage == 'empty_transport_blocks':
        terminal['tool_outputs'][0].insert(1, {'type':'input_text','text':''})
        terminal['tool_outputs'][0].append({'type':'input_text','text':''})
    elif damage in ('extra_wait','extra_message_tool'):
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
        terminal['tool_outputs'][0][0]['body']='page text'
    elif damage=='missing_usage':
        terminal['usage']=None
    elif damage=='partial_usage':
        terminal['usage']={'total_tokens':42}
    elif damage=='inconsistent_usage':
        terminal['usage']['total_tokens']=43
    assert bool(receipt_protocol_issues(terminal,script)) == (damage not in (None,'empty_transport_blocks'))


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
        env = tmp_path/'synthetic.env'
        env.write_text('LEXMOUNT_API_KEY=synthetic\nLEXMOUNT_PROJECT_ID=synthetic\n')
        work = tmp_path/('b'*32); work.mkdir()
        url = 'https://example.test/'
        deadline = int((time.time()+10)*1000)
        (work/'contract.json').write_text(json.dumps(dict(mode='lexmount-api',url=url,
            deadline=deadline,endpoint=f'http://127.0.0.1:{server.server_port}',
            events=str(work/'events.jsonl'),answerPath=str(work/'response.bin'))))
        request = dict(jsonrpc='2.0',id=1,method='tools/call',
            params={'name':'lexmount_fetch','arguments':{'url':url}})
        repeated={**request,'id':2}
        invalid_init={'jsonrpc':'2.0','id':3,'method':'initialize'}
        ping={'jsonrpc':'2.0','id':4,'method':'ping'}
        incoming='\n'.join([json.dumps(request),'{',json.dumps(invalid_init),
                            json.dumps(repeated),json.dumps(ping)])+'\n'
        result = subprocess.run([node,str(BRIDGE),str(work),str(env)],
            input=incoming.encode(),capture_output=True,timeout=15,check=True)
        replies={x['id']:x for x in map(json.loads,result.stdout.splitlines())}
        assert len(replies)==5 and not result.stderr
        assert replies[None]['error']['code']==-32700
        assert replies[3]['error']['code']==-32602
        assert replies[4]['result']=={}
        assert json.loads(replies[2]['result']['content'][0]['text']) == {
            'status':'failed','path':str(work/'response.bin')}
        assert json.loads(replies[1]['result']['content'][0]['text']) == {
            'status':'saved','path':str(work/'response.bin')}
        assert (work/'response.bin').read_bytes() == payload
        assert requests == [{'extract':{'url':url,'options':{'engine_preference':'auto'}}}]
        assert b'complete body' not in result.stdout
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('cleanup_observable', [False, True])
def test_capture_stops_descendants_before_freezing_output(tmp_path, monkeypatch, cleanup_observable):
    import os
    import signal
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    worker=tmp_path/'worker.cjs'
    worker.write_text("setInterval(()=>process.stdout.write('worker output\\n'),20);\n")
    binary=tmp_path/'synthetic-product'
    binary.write_text(f'#!{node}\n'+"""
const fs=require('node:fs');
const child=require('node:child_process').spawn(process.execPath,[__dirname+'/worker.cjs'],
    {stdio:['ignore',1,2]});
fs.writeFileSync(__dirname+'/worker.pid',String(child.pid));
setTimeout(()=>{process.stdout.write('leader done\\n');process.exit(0);},200);
""")
    binary.chmod(0o700)
    answer=tmp_path/'answer.md';url='https://example.test/'
    (tmp_path/'contract.json').write_text(json.dumps(dict(mode='moli-cli',url=url,
        executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        deadline=int((time.time()+10)*1000),answer=str(answer),answerPath=str(answer),
        stderr=str(tmp_path/'debug.log'),events=str(tmp_path/'events.jsonl'))))
    if not cleanup_observable:
        fakebin=tmp_path/'bin';fakebin.mkdir()
        (fakebin/'ps').write_text('#!/bin/sh\nexit 1\n')
        (fakebin/'ps').chmod(0o700)
        monkeypatch.setenv('PATH',str(fakebin)+os.pathsep+os.environ['PATH'])
    try:
        result=subprocess.run([node,str(BRIDGE),str(tmp_path),'--moli-url',url],
            capture_output=True,check=True,timeout=5)
        if not cleanup_observable:
            assert json.loads(result.stdout)['status']=='failed'
            assert not answer.exists() and not (tmp_path/'status.json').exists()
            assert (tmp_path/'answer.md.partial').exists()
            events=[json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
            assert any(e['event']=='group_cleanup_finished' and not e['complete'] for e in events)
            return
        assert json.loads(result.stdout)['status']=='saved'
        status=json.loads((tmp_path/'status.json').read_text())
        assert status['cleanup_complete']
        original=answer.read_bytes()
        assert hashlib.sha256(original).hexdigest()==status['sha256']
        time.sleep(0.1)
        assert answer.read_bytes()==original
        pid=int((tmp_path/'worker.pid').read_text())
        state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
        assert not state or state.startswith('Z')
    finally:
        if (tmp_path/'worker.pid').exists():
            try:os.kill(int((tmp_path/'worker.pid').read_text()),signal.SIGKILL)
            except ProcessLookupError:pass


def test_transport_reports_handler_rejection_without_exiting(tmp_path):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    (tmp_path/'contract.json').write_text(json.dumps({'mode':'lexmount-dispatch'}))
    (tmp_path/'private_artifacts/transport.events.jsonl').mkdir(parents=True)
    requests=[{'jsonrpc':'2.0','id':i,'method':'ping'} for i in (1,2)]
    result=subprocess.run([node,str(BRIDGE),str(tmp_path),str(tmp_path/'synthetic.env')],
        input=('\n'.join(map(json.dumps,requests))+'\n').encode(),capture_output=True,check=True,timeout=5)
    replies=[json.loads(x) for x in result.stdout.splitlines()]
    assert [x['id'] for x in replies]==[1,2]
    assert all(x['error']['code']==-32603 for x in replies)


@pytest.mark.parametrize('extra_call', [False, True])
@pytest.mark.parametrize('inherited', [False, True])
def test_preflight_admission_rechecks_actual_calls(tmp_path, monkeypatch, extra_call, inherited):
    from harness.web_fetch import lexmount_collection as collector
    monkeypatch.setenv('WEBSTONE_DATA_HOME',str(tmp_path))
    experiments=tmp_path/'experiments';experiments.mkdir()
    dataset=experiments/'codex-luna-single-530-20261122/dataset.json'
    dataset.parent.mkdir();dataset.write_text('{}')
    run=experiments/'moli-local-530-synthetic';run.mkdir()
    binary=tmp_path/'synthetic-binary';binary.write_bytes(b'never executed')
    script='frozen capture script'
    contract=dict(executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        bridge_sha256=hashlib.sha256(BRIDGE.read_bytes()).hexdigest(),
        collection_sha256=hashlib.sha256(Path(collector.__file__).read_bytes()).hexdigest(),
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),admitted=False,
        model='gpt-6-luna',reasoning_effort='medium',receipt_protocol='single_blocking_script_v1')
    (run/'contract.json').write_text(json.dumps(contract))
    rows=[]
    for i in range(2):
        slot=str(i);work=run/slot;work.mkdir();url=f'https://example.test/{i}'
        rows.append(dict(scope='preflight',slot=slot,url=url,caller_script=script))
        terminal=dict(response_saved=True,usage={'input_tokens':40,'cached_input_tokens':30,'output_tokens':2,'total_tokens':42},final_text='',
            model='gpt-6-luna',effort='medium',started_at=0,ended_at='1970-01-01T00:00:01Z',
            caller_contexts=[{'model':'gpt-6-luna','effort':'medium'}],
            terminal_type='task_complete',provider_invocations=1,capture_invocations=1,
            calls=[{'name':'exec','input':script}],
            tool_outputs=[json.dumps({'status':'saved','path':'answer.md'})],protocol_issues=[])
        if extra_call and i==0:
            terminal['calls'].append({'name':'wait','input':'{}'})
        (work/'terminal.json').write_text(json.dumps(terminal))
    if inherited:
        work=run/'inherited';work.mkdir()
        (work/'terminal.json').write_text(json.dumps(terminal))
        rows.append(dict(scope='formal',slot=work.name,url=url,caller_script=script,
                         inherited_source={'collection_sha256':'1'*64}))
    (run/'cases.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
    if extra_call:
        with pytest.raises(ValueError,match='receipt-only preflight'):
            collector.api_collection_main(['moli-admit','--run',str(run)])
        assert not json.loads((run/'contract.json').read_text())['admitted']
    else:
        collector.api_collection_main(['moli-admit','--run',str(run)])
        assert json.loads((run/'contract.json').read_text())['admitted']
        plan=json.loads((run/'execution-plan.json').read_text())
        assert plan['planned_calls']==530-int(inherited)
        assert plan['inherited_calls']==int(inherited)


@pytest.mark.parametrize('product', ['moli', 'cli'])
def test_initialization_freezes_scoring_and_single_script_before_calls(tmp_path, monkeypatch, capsys, product):
    from harness.web_fetch import lexmount_collection as collector
    from lexbench_webfetch.scoring import SCORING_SOURCE_FILES, SCORING_SOURCE_SHA256
    monkeypatch.setenv('WEBSTONE_DATA_HOME',str(tmp_path))
    monkeypatch.setattr(collector,'_inputs',lambda home:[('synthetic','https://example.test/')])
    monkeypatch.setattr(collector,'repository_env',lambda:{'LEXMOUNT_API_KEY':'synthetic','LEXMOUNT_PROJECT_ID':'synthetic'})
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Initialization invoked a product'))
    (tmp_path/'experiments').mkdir()
    dataset=tmp_path/'experiments/codex-luna-single-530-20261122/dataset.json'
    dataset.parent.mkdir();dataset.write_text('{}')
    monkeypatch.setattr(collector,'_DATASET_SHA',hashlib.sha256(dataset.read_bytes()).hexdigest())
    name='moli-local-530-synthetic' if product=='moli' else 'lexmount-cli-530-synthetic'
    run=tmp_path/'experiments'/name
    binary=tmp_path/'synthetic-binary';binary.write_bytes(b'never executed')
    collector.api_collection_main([product+'-init','--run',str(run),'--binary',str(binary),
        '--binary-sha256',hashlib.sha256(binary.read_bytes()).hexdigest()])
    output=json.loads(capsys.readouterr().out)
    assert output['caller']['wait_ms']==300000
    assert output['caller']['tool']=='functions.exec'
    assert not output['caller']['intermediate_model_returns']
    frozen=json.loads((run/'scoring-contract.json').read_text())
    assert frozen['scoring_source_sha256']==SCORING_SOURCE_SHA256
    for source in SCORING_SOURCE_FILES:
        assert (run/'private_artifacts/scoring-source'/source.name).read_bytes()==source.read_bytes()
    assert not json.loads((run/'contract.json').read_text())['admitted']
    collector.api_collection_main([product+'-reserve','--run',str(run),'--url','https://outside.test/'])
    assigned=json.loads((run/'cases.jsonl').read_text())
    assert assigned['caller_script']==collector.receipt_caller_script(assigned['command'])
    assert assigned['caller_script'].startswith('// @exec: {"yield_time_ms":300000,')
    assert assigned['caller_script'].count('text(')==1
    assert 'functions.wait' not in assigned['caller_script']
    assert not (run/assigned['slot']/'called').exists()


@pytest.mark.parametrize('damage', [None,'native_call','wrong_model','wrong_effort','earlier_model'])
def test_formal_sync_audits_all_calls_and_caller_contexts(tmp_path, monkeypatch, damage):
    from harness.web_fetch import lexmount_collection as collector
    monkeypatch.setenv('WEBSTONE_DATA_HOME',str(tmp_path))
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    experiments=tmp_path/'experiments';experiments.mkdir()
    dataset=experiments/'codex-luna-single-530-20261122/dataset.json'
    dataset.parent.mkdir();dataset.write_text('{}')
    run=experiments/'moli-local-530-synthetic';run.mkdir()
    binary=tmp_path/'synthetic-binary';binary.write_bytes(b'never executed')
    script='frozen capture script'
    contract=dict(executable=str(binary),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        bridge_sha256=hashlib.sha256(BRIDGE.read_bytes()).hexdigest(),
        collection_sha256=hashlib.sha256(Path(collector.__file__).read_bytes()).hexdigest(),
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),admitted=True,
        model='gpt-6-luna',reasoning_effort='medium',receipt_protocol='single_blocking_script_v1')
    (run/'contract.json').write_text(json.dumps(contract))
    work=run/('a'*32);work.mkdir();(work/'answer.md').write_text('saved')
    (work/'events.jsonl').write_text(''.join(json.dumps({'event':name})+'\n'
        for name in ('tool_called','client_started','receipt_sent')))
    url='https://example.test/'
    row=dict(case_id='synthetic',scope='formal',slot=work.name,url=url,
        agent='moli_synthetic',reserved_at=0,caller_script=script)
    (run/'cases.jsonl').write_text(json.dumps(row)+'\n')
    payloads=[('session_meta',{'id':'synthetic','source':{'subagent':{'thread_spawn':{
        'agent_path':'/root/moli_synthetic'}}}}),
        ('event_msg',{'type':'task_started'})]
    contexts=[{'model':'gpt-6-luna','effort':'medium'}]
    if damage=='wrong_model':contexts[0]['model']='another-model'
    if damage=='wrong_effort':contexts[0]['effort']='low'
    if damage=='earlier_model':contexts.insert(0,{'model':'another-model','effort':'medium'})
    payloads += [('turn_context',c) for c in contexts]
    payloads += [('response_item',{'type':'custom_tool_call','name':'exec','input':script})]
    if damage=='native_call':
        payloads.append(('response_item',{'type':'web_search_call','action':{'type':'search'}}))
    payloads += [('response_item',{'type':'function_call_output','output':json.dumps({
        'status':'saved','path':str(work/'answer.md')})}),
        ('event_msg',{'type':'token_count','info':{'total_token_usage':{'input_tokens':40,'cached_input_tokens':30,'output_tokens':2,'total_tokens':42}}}),
        ('event_msg',{'type':'task_complete','last_agent_message':''})]
    session=tmp_path/'.codex/sessions/2026/11/26/synthetic.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(''.join(json.dumps({'type':kind,'payload':p,
        'timestamp':'2026-11-26T00:00:00Z'})+'\n' for kind,p in payloads))
    original_session=session.read_bytes()
    session.write_bytes(original_session+b'{"type":')
    collector.api_collection_main(['moli-sync','--run',str(run)])
    assert not (work/'terminal.json').exists()
    session.write_bytes(original_session)
    collector.api_collection_main(['moli-sync','--run',str(run)])
    terminal=json.loads((work/'terminal.json').read_text())
    assert bool(terminal['protocol_issues']) == bool(damage)
    snapshot=run/terminal['session_snapshot']
    assert snapshot.read_bytes()==session.read_bytes()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==terminal['session_snapshot_sha256']
    if damage:
        with pytest.raises(ValueError,match='Caller protocol failed'):
            collector.api_collection_main(['moli-reserve','--run',str(run)])
        assert len((run/'cases.jsonl').read_text().splitlines())==1
    else:
        session.unlink()
        (work/'terminal.json').unlink()
        collector.api_collection_main(['moli-sync','--run',str(run)])
        restored=json.loads((work/'terminal.json').read_text())
        assert restored['usage']==terminal['usage'] and not restored['protocol_issues']


@pytest.mark.parametrize('suffix', ['{"type":', '{"type":"event_msg"}\n{"type":'])
def test_live_session_sync_defers_partial_record_without_accepting_corruption(suffix):
    from harness.web_fetch.lexmount_collection import _session_events
    complete = json.dumps({'type':'event_msg','payload':{'type':'task_complete'}})+'\n'
    assert _session_events(complete+suffix, live=True) is None
    with pytest.raises(ValueError):
        _session_events(complete+suffix, live=False)
    with pytest.raises(ValueError):
        _session_events(complete+'{broken}\n', live=True)
    assert _session_events(b'{"text":"\xe4\xb8',live=True) is None
    with pytest.raises(UnicodeDecodeError):
        _session_events(b'{"text":"\xff"}\n',live=True)
    event={'type':'response_item','payload':{'text':'first\u2028second\u2029third'}}
    assert _session_events(json.dumps(event,ensure_ascii=False)+'\n',live=True)==[event]


def test_visible_child_commentary_is_not_a_clean_receipt():
    from harness.web_fetch.lexmount_collection import _trace_terminal, receipt_protocol_issues
    events=[{'type':'session_meta','timestamp':'2026-11-26T00:00:00Z','payload':{'id':'synthetic'}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'commentary',
            'content':[{'type':'output_text','text':'Checking whether fetch finished.'}]}},
        {'type':'event_msg','timestamp':'2026-11-26T00:00:01Z',
         'payload':{'type':'task_complete','last_agent_message':''}}]
    projected=_trace_terminal(events)
    assert projected['assistant_messages']
    assert 'unexpected_final_message' in receipt_protocol_issues(projected,'script')


@pytest.mark.parametrize('phrase', ['Internal Error', 'Failed to fetch'])
@pytest.mark.parametrize('failed', [False, True])
def test_legacy_capture_does_not_treat_error_words_in_page_titles_as_failure(tmp_path, failed, phrase):
    node=shutil.which('node')
    if not node: pytest.skip('Node.js is required')
    (tmp_path/'contract.json').write_text(json.dumps({'mode':'same-page',
        'deadline':int((time.time()+10)*1000)}))
    title=phrase if failed else phrase+' troubleshooting guide (https://example.test/)'
    body=title+'\nSource: open({}); Total lines: 1\nL0: useful content'
    request={'jsonrpc':'2.0','id':1,'method':'tools/call',
        'params':{'name':'save','arguments':{'response':json.dumps(body)}}}
    completed=subprocess.run([node,str(BRIDGE),str(tmp_path)],input=json.dumps(request)+'\n',
        capture_output=True,text=True,check=True,timeout=5)
    reply=json.loads(completed.stdout)['result']
    assert json.loads(reply['content'][0]['text'])['error'] is failed
