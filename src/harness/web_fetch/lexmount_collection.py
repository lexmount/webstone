"""Prepare the Lexmount saving tool for parent-dispatched, fresh Codex subagents."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

from harness.benchmark_execution import assess_execution
from harness.record_io import file_sha256, load_jsonl, read_json
from harness.web_fetch.config import ROOT, load_dataset, repository_env
from harness.web_fetch.providers.codex import (
    _BRIDGE,
    LEXMOUNT_CLI_PROMPT,
    capture_event,
)
from lexbench_webfetch.scoring import load_list_reference

_REVISION = "e4e036818c48a813f9a40f6e8f350c55e8132c59"
_DEFINITION_SHA = "6ae76832f1594e410ab36371c7841a5bd6b4fa65dab39bd6496a8f07c5b22b85"
_DATASET_SHA = "9026e89de17d87e3cb2f374618c5df79195cf55e555dab29910a920a6d1d2de9"
_CLI_SHA = "11981bab56ff55141aa868e4cd059ea09bc80a6db5707b647a32c21cfe5182ca"
_ENDPOINT = "https://api.lexmount.com"
_METHOD = "codex-lexmount-official-cli"
_RUN_BINDING = ROOT / "runs/.lexmount-active-run.json"


def receipt_protocol_issues(terminal, url, tool_name):
    """Validate the complete caller trace, rather than a filtered tool count."""
    issues = []
    calls = terminal.get('calls', [])
    if len(calls) != 1 or calls[0].get('name') != tool_name:
        issues.append('expected_one_direct_fetch_tool_call')
    else:
        try:
            arguments = json.loads(calls[0]['input'])
        except (ValueError, TypeError, KeyError):
            arguments = None
        if arguments != {'url': url}:
            issues.append('unexpected_fetch_arguments')
    outputs = terminal.get('tool_outputs', [])
    value = outputs[0] if len(outputs) == 1 else None
    try:
        for _ in range(5):
            if isinstance(value, str):
                value = json.loads(value)
            elif isinstance(value, list) and len(value) == 1:
                block = value[0]
                value = (block.get('text') if isinstance(block, dict)
                         and set(block) == {'type', 'text'}
                         and block['type'] in ('text', 'input_text') else None)
            elif isinstance(value, dict) and 'content' in value:
                value = value['content'] if set(value) <= {'content', 'isError'} else None
            else:
                break
    except (ValueError, TypeError, AttributeError):
        value = None
    if not (isinstance(value, dict) and set(value) == {'status', 'path'}
            and value['status'] in ('saved', 'failed') and isinstance(value['path'], str) and value['path']):
        issues.append('expected_only_final_status_and_path')
    if terminal.get('final_text'):
        issues.append('unexpected_final_message')
    if terminal.get('terminal_type') != 'task_complete' or not terminal.get('usage'):
        issues.append('missing_terminal_usage')
    if terminal.get('provider_invocations') != 1 or terminal.get('capture_invocations') != 1:
        issues.append('expected_one_capture_and_provider_invocation')
    return issues


def subagent_tool_main(argv):
    """Prepare the MCP transport; actual agents are dispatched by the parent chat."""
    parser = argparse.ArgumentParser(description=subagent_tool_main.__doc__)
    parser.add_argument(
        "operation", choices=("setup-tool", "bind-tool", "admit-tool", "reserve-tool"),
    )
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--webfetch-cli", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--case-id")
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args(argv)
    data_home = Path(os.environ.get("WEBSTONE_DATA_HOME", Path.home() / ".webstone")).resolve()
    root = args.run.resolve()
    if root.parent != data_home / "experiments" or not root.name.startswith("lexmount-api-530-"):
        parser.error("Use a new Lexmount experiment under the configured data home")
    os.umask(0o077)
    if args.operation == "setup-tool":
        if args.webfetch_cli is None:
            parser.error("--webfetch-cli is required")
        executable = args.webfetch_cli.resolve()
        if file_sha256(executable) != _CLI_SHA:
            raise ValueError("The official CLI binary changed")
        _inputs(data_home)
        credentials = repository_env()
        if not all(credentials.get(k) for k in ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")):
            raise ValueError("Missing repository credentials")
        root.mkdir(exist_ok=False)
        (root / "dispatch").mkdir()
        binary = root / "private_artifacts/bin/webfetch-cli"
        binary.parent.mkdir(parents=True)
        shutil.copy2(executable, binary)
        executable = binary
        _write_json(root / "contract.json", {
            "mode": "lexmount-dispatch", "executable": str(executable), "endpoint": _ENDPOINT,
            "cli_sha256": _CLI_SHA, "bridge_sha256": file_sha256(_BRIDGE),
            "dataset_sha256": _DATASET_SHA, "dataset_revision": _REVISION,
            "content_scope": "full", "score_scope": "full", "retries": 0,
            "formal_collection_admitted": False,
            "prompt": LEXMOUNT_CLI_PROMPT,
            "prompt_sha256": hashlib.sha256(LEXMOUNT_CLI_PROMPT.encode()).hexdigest(),
        })
        print(json.dumps({"run": str(root), "command": shutil.which("node"),
                          "args": [str(_BRIDGE), "--bound-dispatch", str(ROOT / ".env")]}))
        return
    contract = read_json(root / "contract.json")
    if (contract["mode"] != "lexmount-dispatch"
            or contract["bridge_sha256"] != file_sha256(_BRIDGE)
            or contract["cli_sha256"] != file_sha256(Path(contract["executable"]))):
        raise ValueError("The frozen tool identity changed")
    if args.operation == "admit-tool":
        if args.preflight is None or contract["formal_collection_admitted"]:
            parser.error("A new run and --preflight receipt are required")
        receipt = read_json(args.preflight)
        if receipt["bridge_sha256"] != file_sha256(_BRIDGE):
            raise ValueError("The preflight bridge identity changed")
        normal = [r for r in receipt["results"] if not r["artificial_wait_control"]]
        if not receipt["active_agent_interruption_verified"] or len(normal) != 2 or not all(
            r["source"] == "subagent" and r["tool_calls"] == r["cli_invocations"] == 1
            and r["terminal_usage"] is not None for r in normal
        ):
            raise ValueError("Fresh-subagent preflight did not pass")
        _write_json(root / "preflight.json", receipt)
        plan = {
            "execution_policy": {"purpose": "quality", "max_in_flight": 2,
                                 "resources": {"lexmount_account": {"capacity": 2}}},
            "planned_calls": 530, "setup_seconds": 0, "wall_time_budget_seconds": 14400,
            "preflight": {k: receipt[k] for k in
                          ("completed_calls", "wall_seconds", "max_in_flight")},
        }
        plan["preflight"].update(evidence_path="preflight.json",
                                 evidence_sha256=file_sha256(root / "preflight.json"))
        assessment = assess_execution(plan, evidence_root=root)
        _write_json(root / "execution-plan.json", plan)
        contract.update(formal_collection_admitted=True, execution_assessment=assessment,
                        plan_sha256=file_sha256(ROOT / "runs/lexmount-api-530-plan.zh.md"),
                        collection_sha256=file_sha256(Path(__file__)),
                        prompt=receipt["prompt_template"],
                        prompt_sha256=hashlib.sha256(receipt["prompt_template"].encode()).hexdigest(),
                        isolation=receipt["isolation"])
        _write_json(root / "contract.json", contract)
        print(json.dumps(assessment))
        return
    if args.operation == "bind-tool":
        if _RUN_BINDING.exists():
            previous = Path(read_json(_RUN_BINDING)["run"])
            if previous != root and any(
                read_json(record)["deadline"] > time.time() * 1000
                for record in (previous / "dispatch").glob("*.json")
            ):
                raise ValueError("The previously bound run still has unexpired reservations")
        _write_json(_RUN_BINDING, {"run": str(root)})
        print(json.dumps({"run": str(root), "binding": str(_RUN_BINDING)}))
        return
    if not args.url or not args.case_id:
        parser.error("--url and --case-id are required")
    if read_json(_RUN_BINDING)["run"] != str(root):
        raise ValueError("The reservation directory must match the project tool binding")
    selected = dict(_inputs(data_home))
    if contract["formal_collection_admitted"]:
        if selected.get(args.case_id) != args.url:
            raise ValueError("The formal case ID and URL do not match the frozen dataset")
    elif args.url in selected.values():
        raise ValueError("Formal dataset URLs cannot be used for preflight")
    with (root / "run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = load_jsonl(root / "cases.jsonl") if (root / "cases.jsonl").exists() else []
        if any(row["case_id"] == args.case_id for row in rows):
            raise ValueError("This preflight case is already reserved; retries are disabled")
        if contract["formal_collection_admitted"]:
            pending = [r for r in rows if not (root / "private_artifacts/live-logs" /
                       r["slot"] / "terminal.json").exists()]
            if len(pending) >= 2:
                raise ValueError("The two-worker admission limit is full")
        key = hashlib.sha256(args.url.encode()).hexdigest()
        reservation = root / "dispatch" / (key + ".json")
        if reservation.exists():
            old = read_json(reservation)
            if not contract["formal_collection_admitted"] or not (
                root / "private_artifacts/live-logs" / old["slot"] / "terminal.json"
            ).exists():
                raise ValueError("This URL is already reserved; retries are disabled")
        slot = uuid4().hex
        (root / slot).mkdir()
        private = root / "private_artifacts" / slot
        (private / "work").mkdir(parents=True)
        (private / "cli.stderr.log").touch()
        events = root / "private_artifacts/live-logs" / slot / "events.jsonl"
        events.parent.mkdir(parents=True)
        capture_event(events, "reserved")
        row = {"case_id": args.case_id, "method": _METHOD, "slot": slot,
               "answer_path": f"{slot}/answer.md",
               "scope": "formal" if contract["formal_collection_admitted"] else "preflight"}
        with (root / "cases.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _write_json(reservation, {"url": args.url, "slot": slot,
                                  "deadline": int((time.time() + 300) * 1000)})
        print(json.dumps(row))


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _inputs(data_home):
    edition = read_json(ROOT / "src/harness/benchmarks/data-lock.json")["datasets"]["webfetch"]
    definition = edition["datasets"]["content-scopes-530"]
    tasks = data_home / "datasets/webfetch" / _REVISION / "definitions" / definition["path"]
    dataset = data_home / "experiments/codex-luna-single-530-20261122/dataset.json"
    if (edition["revision"] != _REVISION or definition["sha256"] != _DEFINITION_SHA
            or file_sha256(tasks) != _DEFINITION_SHA or file_sha256(dataset) != _DATASET_SHA):
        raise ValueError("The frozen content-scopes-530 input changed")
    _, cases = load_dataset(dataset, content_scope="full", scoring_scope="full")
    selected = [(case.id, case.url) for case in cases]
    pinned = [(row["id"], row["url"]) for row in read_json(tasks)["cases"]]
    if len(selected) != 530 or selected != pinned:
        raise ValueError("The full input does not match the 530 pinned task IDs and URLs")
    for case in cases:
        load_list_reference(case, dataset.parent)
    return selected


def api_collection_main(argv):
    """Collect receipt-only provider responses through fresh parent-spawned subagents."""
    parser = argparse.ArgumentParser(description=api_collection_main.__doc__)
    parser.add_argument('operation', choices=tuple(
        f'{provider}-{operation}' for provider in ('api', 'moli', 'cli')
        for operation in ('init', 'reserve', 'sync', 'admit', 'score')))
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--url')
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--binary-sha256')
    parser.add_argument('--wall-time-budget-seconds', type=int, default=14400)
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--tool-name', default='mcp__webstone_capture__fetch')
    args = parser.parse_args(argv)
    local = args.operation.startswith('moli-')
    official = args.operation.startswith('cli-')
    markdown = local or official
    operation = 'api-' + args.operation.split('-', 1)[1]
    root = args.run.resolve()
    home = Path(os.environ.get('WEBSTONE_DATA_HOME', Path.home() / '.webstone')).resolve()
    prefix = 'moli-local-530-' if local else 'lexmount-cli-530-' if official else 'lexmount-api-530-'
    if root.parent != home / 'experiments' or not root.name.startswith(prefix):
        parser.error('Use a fresh experiment under the data home')
    os.umask(0o077)
    dataset = home / 'experiments/codex-luna-single-530-20261122/dataset.json'
    if operation == 'api-init':
        if not re.fullmatch(r'mcp__\w+__fetch', args.tool_name):
            raise ValueError('Bind the connected MCP fetch tool name')
        selected = _inputs(home)
        if not local and not all(repository_env().get(k) for k in ('LEXMOUNT_API_KEY', 'LEXMOUNT_PROJECT_ID')):
            raise ValueError('Missing repository credentials')
        if markdown and (args.binary is None or not args.binary_sha256 or
                      file_sha256(args.binary) != args.binary_sha256):
            raise ValueError('Bind the exact executable and SHA-256')
        root.mkdir(exist_ok=False)
        contract = dict(mode='lexmount-api-receipt', endpoint=_ENDPOINT,
                        request={'extract': {'url': '{url}', 'options': {'engine_preference': 'auto'}}},
                        dataset=str(dataset), dataset_sha256=_DATASET_SHA, cases=len(selected),
                        bridge=str(_BRIDGE), bridge_sha256=file_sha256(_BRIDGE),
                        collection_sha256=file_sha256(Path(__file__)),
                        retries=0, timeout_seconds=300, max_in_flight=2,
                        model='gpt-6-luna', reasoning_effort='medium', fork_turns='none',
                        content_scope='full', score_scope='full', admitted=False,
                        transport='one blocking MCP call; no terminal or model polling fallback',
                        receipt_protocol='single_blocking_tool_v1', tool_name=args.tool_name,
                        output='unaltered complete HTTP response bytes; receipt only to subagent',
                        scoring='after all 530 subagents terminate; shared native API projection and score_attempt',
                        plan_changes=['direct API with explicit auto replaces default CLI Markdown',
                                      'native API fields replace CLI Markdown as scoring input',
                                      'capture bridge waits and saves bytes before its only tool response'])
        if markdown:
            contract.update(mode='moli-cli-receipt' if local else 'lexmount-cli-receipt', executable=str(args.binary.resolve()),
                            binary_sha256=args.binary_sha256,
                            request={'argv': ['fetch', '--dump', 'markdown', '--log-level', 'debug', '{url}'] if local
                                     else ['extract', '--url', '{url}']},
                            endpoint=None if local else _ENDPOINT,
                            output='complete native Markdown stdout; receipt only to subagent',
                            scoring='after all 530 subagents terminate; shared score_attempt and task predicate',
                            plan_changes=['local Moli defaults; explicit Markdown and debug only'] if local else
                                         ['official CLI defaults; blocking receipt-only transport'])
        from harness.web_fetch.native_projection import native_projection_policy
        if not markdown:
            contract['native_projection_policy'] = native_projection_policy('lexmount')
        _write_json(root / 'contract.json', contract)
        from lexbench_webfetch.scoring import SCORING_SOURCE_FILES, SCORING_SOURCE_SHA256
        snapshots = root / 'private_artifacts/scoring-source'
        snapshots.mkdir(parents=True)
        for source in SCORING_SOURCE_FILES:
            shutil.copyfile(source, snapshots / source.name)
        _write_json(root / 'scoring-contract.json', dict(
            scoring_source_sha256=SCORING_SOURCE_SHA256,
            source_files={source.name: file_sha256(source) for source in SCORING_SOURCE_FILES},
            phase='frozen_before_collection'))
        (root / 'dispatch').mkdir()
        print(json.dumps({'run': str(root), 'cases': len(selected), 'admitted': False,
            'mcp_server': {'name': 'webstone_capture', 'required': True, 'tool_timeout_sec': 300,
                'enabled_tools': ['fetch'], 'command': shutil.which('node'), 'args': [str(_BRIDGE), str(root),
                '--receipt-tool', *([] if local else [str(ROOT / '.env')])]}}))
        return
    contract = read_json(root / 'contract.json')
    if markdown and file_sha256(Path(contract['executable'])) != contract['binary_sha256']:
        raise ValueError('Frozen executable changed')
    if (file_sha256(_BRIDGE) != contract['bridge_sha256'] or
            (operation != 'api-score' and file_sha256(Path(__file__)) != contract['collection_sha256']) or
            file_sha256(dataset) != contract['dataset_sha256']):
        raise ValueError('Frozen collection inputs changed')
    manifest = root / 'cases.jsonl'
    rows = load_jsonl(manifest) if manifest.exists() else []
    if operation == 'api-reserve':
        if contract.get('receipt_protocol') and any(
                read_json(root/r['slot']/'terminal.json').get('protocol_issues', ['missing_protocol_audit'])
                for r in rows if (root/r['slot']/'terminal.json').exists()):
            raise ValueError('Caller protocol failed; stop admission and preserve completed calls')
        if sum(not (root / r['slot'] / 'terminal.json').exists() for r in rows) >= 2:
            raise ValueError('Two-worker admission is full')
        selected = _inputs(home)
        if args.url:
            if contract['admitted'] or args.url in dict(selected).values():
                raise ValueError('Preflight must use an outside-cohort URL before admission')
            case_id, url, scope = 'preflight-' + uuid4().hex, args.url, 'preflight'
        else:
            if not contract['admitted']:
                raise ValueError('Formal collection is not admitted')
            done = {r['case_id'] for r in rows if r['scope'] == 'formal'}
            available = [(k, v) for k, v in selected if k not in done]
            if not available:
                print(json.dumps({'remaining': 0})); return
            case_id, url = available[0]; scope = 'formal'
        slot = uuid4().hex
        work = root / slot
        work.mkdir()
        agent = ('moli_' if local else 'lx_cli_' if official else 'lx_api_') + slot
        prompt = ('Call ' + contract['tool_name'] + ' exactly once with {"url":' + json.dumps(url) + '}. '
                  'The tool waits for completion and saves the full response itself. '
                  'After its saved/failed receipt, end with no message. Use no other tools.')
        deadline = int((time.time()+300)*1000)
        _write_json(work / 'contract.json', dict(mode='moli-cli' if local else 'lexmount-cli' if official else 'lexmount-api', url=url,
                    endpoint=contract['endpoint'], deadline=deadline,
                    answerPath=str(work/('answer.md' if markdown else 'response.bin')),
                    events=str(work/'events.jsonl'),
                    **(dict(executable=contract['executable'], binary_sha256=contract['binary_sha256'],
                            answer=str(work/'answer.md'),
                            stderr=str(work/'debug.log')) if markdown else {})))
        _write_json(root / 'dispatch' / (hashlib.sha256(url.encode()).hexdigest()+'.json'),
                    dict(url=url, slot=slot, deadline=deadline))
        row = dict(case_id=case_id, url=url, scope=scope, slot=slot, agent=agent,
                   prompt=prompt, reserved_at=time.time(),
                   answer_path=slot+('/answer.md' if markdown else '/response.bin'))
        with manifest.open('a') as stream:
            stream.write(json.dumps(row)+'\n'); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({'agent': agent, 'prompt': prompt, 'scope': scope, 'case_id': case_id}))
        return
    if operation == 'api-sync':
        by_agent = {r['agent']: r for r in rows if not (root/r['slot']/'terminal.json').exists()}
        if by_agent:
            for session in (Path.home()/'.codex/sessions').glob('*/*/*/*.jsonl'):
                with session.open() as stream:
                    first = stream.readline()
                    if not any(name in first for name in by_agent):
                        continue
                    events = [json.loads(first), *(json.loads(line) for line in stream)]
                meta = events[0].get('payload', {})
                spawn = meta.get('source', {}).get('subagent', {}).get('thread_spawn', {})
                row = by_agent.get(spawn.get('agent_path', '').removeprefix('/root/'))
                if row is None: continue
                payloads = [e.get('payload', {}) for e in events if e['type']=='event_msg']
                terminal = next((p for p in reversed(payloads) if p.get('type') in ('task_complete','turn_aborted')), None)
                if terminal is None: continue
                usage = next((p.get('info', {}).get('total_token_usage') for p in reversed(payloads)
                              if p.get('type')=='token_count' and p.get('info')), None)
                contexts = [e['payload'] for e in events if e['type']=='turn_context']
                calls = [e['payload'] for e in events if e['type']=='response_item' and
                         e['payload'].get('type') in ('function_call','custom_tool_call')]
                final = next(e for e in reversed(events) if e.get('payload') is terminal)
                if __import__('datetime').datetime.fromisoformat(final['timestamp'].replace('Z', '+00:00')).timestamp() < row['reserved_at']:
                    continue  # A resumed agent may still contain its earlier rejected turn.
                started_event = next((e for e in events if e.get('payload', {}).get('type') == 'task_started'), events[0])
                work = root/row['slot']
                result = dict(agent=row['agent'], session_id=meta['id'], session_path=str(session),
                              terminal_type=terminal['type'], ended_at=final['timestamp'],
                              started_at=row['reserved_at'], usage=usage,
                              session_started_at=started_event['timestamp'], reserved_at=row['reserved_at'],
                              model=contexts[-1].get('model') if contexts else None,
                              effort=contexts[-1].get('effort') if contexts else None,
                              final_text=terminal.get('last_agent_message', ''),
                              calls=[{'name':p.get('name'),'input':p.get('input',p.get('arguments'))} for p in calls],
                              tool_outputs=[e['payload']['output'] for e in events
                                  if e['type']=='response_item' and e['payload'].get('type')
                                  in ('function_call_output','custom_tool_call_output')],
                              response_saved=(work/('answer.md' if markdown else 'response.bin')).is_file(),
                              http_status=read_json(work/'status.json') if (work/'status.json').exists() else None)
                capture = load_jsonl(work/'events.jsonl') if (work/'events.jsonl').exists() else []
                result.update(provider_invocations=sum(e['event'] in ('client_started','api_started') for e in capture),
                              capture_invocations=sum(e['event']=='tool_called' for e in capture))
                if contract.get('receipt_protocol'):
                    result['protocol_issues'] = receipt_protocol_issues(result, row['url'], contract['tool_name'])
                _write_json(work/'terminal.json', result)
        formal = [r for r in rows if r['scope']=='formal']
        preflight = [r for r in rows if r['scope']=='preflight']
        result = dict(reserved=len(formal), terminal=sum((root/r['slot']/'terminal.json').exists() for r in formal),
                      preflight_reserved=len(preflight),
                      preflight_terminal=sum((root/r['slot']/'terminal.json').exists() for r in preflight),
                      active=[r['agent'] for r in rows if not (root/r['slot']/'terminal.json').exists()])
        _write_json(root/'summary.json',result); print(json.dumps(result)); return
    if operation == 'api-admit':
        selected = [r for r in rows if r['scope']=='preflight']
        preflight = [read_json(root/r['slot']/'terminal.json') for r in selected]
        protocol_failed = any(receipt_protocol_issues(terminal, row['url'], contract['tool_name'])
                              for row, terminal in zip(selected, preflight, strict=True))
        if contract['admitted'] or len(preflight)!=2 or any(
            not r['response_saved'] or r['usage'] is None or r['final_text'] or
            r.get('protocol_issues', ['missing_protocol_audit']) or
            r['model'] != contract['model'] or r['effort'] != contract['reasoning_effort']
            for r in preflight) or protocol_failed:
            raise ValueError('Two complete receipt-only preflight subagents are required')
        elapsed = max(__import__('datetime').datetime.fromisoformat(r['ended_at'].replace('Z','+00:00')).timestamp()
                      for r in preflight)-min(r['started_at'] for r in preflight)
        receipt=dict(completed_calls=2, wall_seconds=elapsed, max_in_flight=2, results=preflight)
        _write_json(root/'preflight.json',receipt)
        plan=dict(execution_policy={'purpose':'quality','max_in_flight':2,
                  'resources':{('local_moli_host' if local else 'lexmount_account'):{'capacity':2}}},planned_calls=530,setup_seconds=0,
                  wall_time_budget_seconds=args.wall_time_budget_seconds,preflight=dict(completed_calls=2,wall_seconds=elapsed,
                  max_in_flight=2,evidence_path='preflight.json',evidence_sha256=file_sha256(root/'preflight.json')))
        assessment=assess_execution(plan,evidence_root=root)
        _write_json(root/'execution-plan.json',plan)
        contract.update(admitted=True,execution_assessment=assessment)
        _write_json(root/'contract.json',contract);print(json.dumps(assessment));return
    if operation == 'api-score':
        if markdown:
            print(json.dumps(score_cli_collection(root, dataset, rows, contract, jobs=args.jobs)))
            return
        raise ValueError('Scoring is disabled until the full frozen cohort has terminated')


def _score_moli_record(arguments):
    from lexbench_webfetch.scoring import score_attempt
    record, case, root = arguments
    raw = (root / record['artifact_path']).read_bytes()
    record['metrics'] = score_attempt(case, raw, ok=record['ok'],
        status_code=record['status_code'], final_url=record['final_url'],
        metadata=record['provider_metadata'], list_reference=load_list_reference(case, root),
        content_scope='full', scoring_scope='full')
    return record


def score_cli_collection(root, dataset, rows, contract, *, jobs=1):
    """Export and score a completed receipt-only cohort without invoking Moli."""
    from concurrent.futures import ProcessPoolExecutor
    from datetime import datetime
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
    from harness.web_fetch.artifacts import materialize_task_references
    from harness.web_fetch.native_projection import (
        main_document_url_from_fetch_log, native_fetch_diagnostics,
    )
    from harness.web_fetch.providers.moli import _markdown_http_error_status, _moli_native_error_type
    from harness.web_fetch.reference_inputs import input_tree

    if type(jobs) is not int or not 1 <= jobs <= 8:
        raise ValueError('Offline scoring jobs must be between 1 and 8')
    frozen = read_json(root / 'scoring-contract.json')
    if frozen['scoring_source_sha256'] != SCORING_SOURCE_SHA256:
        raise ValueError('The pre-collection frozen scorer changed')
    formal = [row for row in rows if row['scope'] == 'formal']
    _, cases = load_dataset(dataset, content_scope='full', scoring_scope='full')
    if len(formal) != len(cases) or len({row['agent'] for row in formal}) != len(cases):
        raise ValueError('The complete unique subagent cohort is required before scoring')
    by_case = {row['case_id']: row for row in formal}
    if set(by_case) != {case.id for case in cases}:
        raise ValueError('The selected cohort differs from the frozen dataset')
    verified = []
    for case in cases:
        row = by_case[case.id]
        work = root / row['slot']
        if row['url'] != case.url or any(not (work / name).is_file() for name in
                ('called', 'status.json', 'answer.md', 'debug.log', 'events.jsonl', 'terminal.json')):
            raise ValueError('Missing completed call evidence: ' + case.id)
        for path in work.iterdir():
            if path.is_symlink():
                raise ValueError('Symlinked collection evidence')
        status, terminal = read_json(work / 'status.json'), read_json(work / 'terminal.json')
        if ((work / 'answer.md').stat().st_size != status['bytes']
                or file_sha256(work / 'answer.md') != status['sha256']):
            raise ValueError('Saved response integrity failure: ' + case.id)
        events = load_jsonl(work / 'events.jsonl')
        if sum(event['event'] == 'client_started' for event in events) != 1:
            raise ValueError('Require exactly one actual Moli invocation: ' + case.id)
        if terminal['terminal_type'] != 'task_complete' or not terminal.get('usage'):
            raise ValueError('Missing final subagent telemetry: ' + case.id)
        if contract.get('receipt_protocol') and receipt_protocol_issues(terminal, row['url'], contract['tool_name']):
            raise ValueError('Receipt-only caller protocol violation: ' + case.id)
        verified.append((case, row, work, status, terminal, events))
    output = root / 'scored'
    output.mkdir(exist_ok=False)
    shutil.copy2(dataset, output / 'dataset.json')
    materialize_task_references(output, {name: input_tree(dataset, name)
        for name in ('sources', 'list-references')}, [case.id for case in cases])
    local = contract.get('mode', 'moli-cli-receipt') == 'moli-cli-receipt'
    provider = 'codex-moli-local-default' if local else 'codex-lexmount-official-cli'
    prepared = []
    def timestamp(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    for case, row, work, status, terminal, events in verified:
        artifact = Path('artifacts') / provider / case.id / 'answer.md'
        debug = Path('private_artifacts') / provider / case.id / 'debug.log'
        for source, target in ((work / 'answer.md', artifact), (work / 'debug.log', debug)):
            (output / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output / target)
        diagnostic = (output / debug).read_bytes()
        native_status, timed_out = native_fetch_diagnostics(diagnostic, case.url)
        raw = (output / artifact).read_bytes()
        parsed_status = native_status or _markdown_http_error_status(raw.decode('utf-8', 'replace'))
        ok = status['code'] == 0 and bool(raw.strip()) and not (parsed_status and parsed_status >= 400)
        start = terminal['session_started_at']
        elapsed = (timestamp(terminal['ended_at']) - timestamp(start)) * 1000
        receipts = [e for e in events if e['event'] == 'receipt_sent']
        metadata = dict(answer_format='markdown', subagent_id=terminal['session_id'],
            subagent_usage=terminal['usage'], full_subagent_lifecycle_ms=elapsed,
            cli_invocations=1, retries_used=0,
            native_readiness_timed_out=timed_out,
            native_route_receipt={'transport_state': 'started', 'interface': 'local-moli-cli' if local else 'official-cli'},
            native_parameters=contract['request'], binary_sha256=contract['binary_sha256'],
            diagnostic_log_path=debug.as_posix(), diagnostic_log_sha256=file_sha256(output / debug),
            diagnostic_log_bytes=(output / debug).stat().st_size)
        metadata['moli_cli_ms' if local else 'lexmount_cli_ms'] = status['elapsed_ms']
        if receipts:
            metadata['receipt_to_exit_ms'] = (timestamp(terminal['ended_at']) -
                timestamp(receipts[-1]['timestamp'])) * 1000
        if case.task_kind == 'file':
            metadata['file_evaluation'] = {'files': [], 'pass': False,
                'scope': 'returned_bytes_signature_length_sha256'}
        record = dict(attempt_id=row['slot'], provider=provider, provider_type='codex_native_subagent',
            case_id=case.id, case_name=case.name, category=case.category, url=case.url, run=1,
            started_at=start, completed_at=terminal['ended_at'], elapsed_ms=elapsed, ok=ok,
            status_code=parsed_status, final_url=main_document_url_from_fetch_log(diagnostic, case.url),
            title=None, error_type=None if ok else _moli_native_error_type(diagnostic) if local else 'cli_error',
            error_message=None if ok else 'See the retained same-call CLI diagnostic log.',
            artifact_path=artifact.as_posix(), artifact_sha256={artifact.as_posix(): status['sha256']},
            provider_metadata=metadata, scoring={'mode': 'offline_initial_score',
                'source_sha256': SCORING_SOURCE_SHA256, 'original_output_sha256': status['sha256'],
                'no_provider_calls': True})
        prepared.append((record, vars(case), output))
    executor = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else None
    try:
        scored = list(executor.map(_score_moli_record, prepared) if executor
                      else map(_score_moli_record, prepared))
    finally:
        if executor:
            executor.shutdown()
    with (output / 'results.jsonl').open('x') as stream:
        for record in scored:
            stream.write(json.dumps(record) + '\n')
    result = dict(cases=len(scored), provider=provider,
        passed=sum(task_passed(record['ok'], record['metrics'], case.task_kind)
                   for record, case in zip(scored, cases, strict=True)),
        scoring_source_sha256=SCORING_SOURCE_SHA256, provider_calls_added=0,
        dataset_sha256=file_sha256(output / 'dataset.json'),
        results_sha256=file_sha256(output / 'results.jsonl'))
    _write_json(output / 'score-summary.json', result)
    return result


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1].startswith(('api-', 'moli-', 'cli-')):
        api_collection_main(sys.argv[1:])
    else:
        subagent_tool_main(sys.argv[1:])
