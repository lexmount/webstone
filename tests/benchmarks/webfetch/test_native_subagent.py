"""Offline capture and raw-trajectory controls for native fetch experiments."""

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.web_fetch.native_subagent import (
    CAPTURE_SCRIPT,
    assess_protocol,
    audit_single_open,
    delivery_timing,
    dispatch_checks,
    main,
    save_native_response,
    trace_telemetry,
)
from harness.web_fetch.report_usage import token_usage


@pytest.mark.skipif(shutil.which("node") is None, reason="Native capture transfer uses Node")
def test_documented_capture_transfer_preserves_shell_literals_and_split_unicode(tmp_path):
    guide = Path("src/harness/web_fetch/NATIVE_SUBAGENT.md").read_text()
    capture = guide.split("```javascript\n")[2].split("```", 1)[0]
    directory = tmp_path / "case with spaces"
    directory.mkdir()
    body = "x" * 32767 + "😀\n'$() `literal` 中文"
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), cp = require('node:child_process');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const tools = {exec_command: async ({cmd,workdir}) => {
  if(cmd.endsWith(' --capture-script')) return {exit_code:0,output:input.helper};
  if(cmd.startsWith(
    'uv run --frozen --no-cache --no-sync python -m harness.web_fetch.native_subagent '))
    return {exit_code:0,output:'receipt'};
  return {exit_code:0,output:cp.execSync(cmd,{cwd:workdir,encoding:'utf8'})};
}};
const receipts = [];
const context = {tools, notify:value=>receipts.push(value), executionRoot:input.directory, input};
vm.runInNewContext(`
(async()=>{
${input.capture}
  await prepareCapture(input.directory,'fixture_fetch',{url:'https://example.test/'});
  await persistCapture(input.body,input.directory,12.5);
})()`, context).then(()=>process.stdout.write(JSON.stringify(receipts)))
  .catch(e=>{console.error(e);process.exitCode=1;});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(
            {
                "capture": capture,
                "helper": CAPTURE_SCRIPT,
                "directory": str(directory),
                "body": body,
            }
        ),
        text=True,
        check=True,
        capture_output=True,
        timeout=15,
    )
    assert json.loads(completed.stdout) == ["receipt"]
    assert body not in completed.stdout
    save_native_response(directory, fetch_ms=12.5)
    assert (directory / "answer.txt").read_text() == body
    event = json.loads((directory / "events.jsonl").read_text())
    assert event["tool"] == "fixture_fetch"
    assert event["parameters"] == {"url": "https://example.test/"}


def test_capture_keeps_native_text_order_binary_and_raw_envelope(tmp_path, capsys):
    raw = json.dumps(
        {
            "content": [
                {"type": "text", "text": "first\n"},
                {"type": "resource", "resource": {"text": "resource text"}},
                {"type": "image", "data": base64.b64encode(b"\x00\xff").decode()},
                {"type": "text", "text": "last"},
            ]
        },
        ensure_ascii=False,
    ).encode()
    (tmp_path / ".raw-response.partial").write_bytes(raw)
    assert main(["--save-native-response", str(tmp_path), "--fetch-ms", "12.5"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["fetch_ms"] == 12.5
    assert receipt["saved_at"]
    assert (tmp_path / "raw-response.json").read_bytes() == raw
    assert (tmp_path / "answer.txt").read_bytes() == b"first\n\nlast"
    assert (tmp_path / "block-0001.txt").read_text() == "resource text"
    assert (tmp_path / "block-0002.bin").read_bytes() == b"\x00\xff"
    for item in receipt["artifacts"]:
        body = (tmp_path / item["path"]).read_bytes()
        assert (len(body), hashlib.sha256(body).hexdigest()) == (item["bytes"], item["sha256"])
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt
    (tmp_path / ".raw-response.partial").write_bytes(raw)
    with pytest.raises(FileExistsError):
        save_native_response(tmp_path)


@pytest.mark.parametrize(
    "value,answer",
    [
        ("plain", "plain"),
        ({"output": "tool output"}, "tool output"),
        ({"fetch_error": "timeout"}, ""),
    ],
)
def test_capture_preserves_tool_failures_and_supported_envelopes(tmp_path, value, answer):
    (tmp_path / ".raw-response.partial").write_text(json.dumps(value))
    save_native_response(tmp_path)
    assert (tmp_path / "answer.txt").read_text() == answer
    assert json.loads((tmp_path / "raw-response.json").read_text()) == value


def test_capture_rejects_symlink_and_invalid_duration_before_claim(tmp_path):
    target = tmp_path / "target"
    target.write_text('"saved"')
    staged = tmp_path / ".raw-response.partial"
    staged.symlink_to(target)
    with pytest.raises(ValueError, match="regular staged"):
        save_native_response(tmp_path)
    staged.unlink()
    staged.write_text('"saved"')
    with pytest.raises(ValueError, match="duration"):
        save_native_response(tmp_path, fetch_ms=float("nan"))
    assert not (tmp_path / ".native-save-claimed").exists()


def test_dispatch_rejects_history_inheritance_and_preserves_unknown_plaintext():
    expected = dict(
        task_name="case", message="exact prompt", model="fixture-model", reasoning_effort="medium"
    )
    args = {**expected, "fork_turns": "none"}
    assert all(dispatch_checks(args, **expected).values())
    assert dispatch_checks({**args, "fork_turns": "all"}, **expected)["fork_turns_none"] is False
    assert (
        dispatch_checks({**args, "message": "changed"}, **expected)["exact_prompt_matches"] is False
    )
    assert (
        dispatch_checks({**args, "message": "gAAAAAencrypted"}, **expected)["exact_prompt_matches"]
        is None
    )


@pytest.mark.parametrize(
    "call,violations,probes",
    [
        (
            {
                "name": "js",
                "namespace": "mcp__cua_repl",
                "arguments": json.dumps({"code": "await cua.getState();"}),
            },
            0,
            1,
        ),
        (
            {
                "name": "js",
                "namespace": "mcp__cua_repl",
                "arguments": json.dumps(
                    {"code": "await cua.createBrowserTab('iab', 'https://example.test');"}
                ),
            },
            1,
            0,
        ),
        (
            {
                "name": "mcp__cua_repl__js",
                "arguments": json.dumps({"code": "await cua.getState();"}),
            },
            0,
            1,
        ),
        ({"name": "exec", "input": "await tools.mcp__cua_repl__js({code:'click()'});"}, 1, 0),
        (
            {
                "name": "exec",
                "input": "await tools.web__run({open:[{ref_id:'https://example.test'}]});",
            },
            0,
            0,
        ),
        ({"name": "exec", "input": "text(ALL_TOOLS.filter(x=>/browser/.test(x.name)));"}, 0, 0),
        ({"name": "lexmount_fetch", "namespace": "mcp__lexmount", "arguments": "{}"}, 1, 0),
        ({"name": "mcp__lexmount__lexmount_fetch", "arguments": "{}"}, 1, 0),
    ],
)
def test_namespace_aware_protocol_controls(call, violations, probes):
    actual, inventory = assess_protocol([{"payload": call}])
    assert (len(actual), len(inventory)) == (violations, probes)


def completed(item):
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


def test_single_open_does_not_double_count_nested_native_action():
    url = "https://example.test/"
    native = completed(
        {
            "type": "Extension",
            "kind": "web.search",
            "parent_id": "mcp-1",
            "action": {"type": "openPage", "url": url},
        }
    )
    mcp = completed(
        {
            "type": "McpToolCall",
            "id": "mcp-1",
            "server": "codex_apps",
            "tool": "search_service.web_run",
            "arguments": {"open": [{"ref_id": url}], "response_length": "long"},
        }
    )
    assert audit_single_open([native], url)["route"] == "web.open"
    assert audit_single_open([mcp, native], url) == {
        "route": "codex_apps.search_service.web_run",
        "invocation_count": 1,
        "nested_extension_events": 1,
    }
    standalone = completed({**native["payload"]["item"], "parent_id": None})
    for events in ([mcp, mcp], [], [native, native], [mcp, standalone], [mcp, native, standalone]):
        with pytest.raises(ValueError):
            audit_single_open(events, url)
    with pytest.raises(ValueError):
        audit_single_open([mcp, native], "https://other.test/")


def test_terminal_usage_is_last_cumulative_receipt_and_cache_is_not_added_twice():
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "output_tokens": 10,
        "total_tokens": 110,
    }

    def token(value):
        return {
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": value}},
        }

    def event(timestamp, kind, **fields):
        return {
            "timestamp": timestamp,
            "type": "response_item",
            "payload": {"type": kind, **fields},
        }

    events = [
        token({**usage, "total_tokens": 50}),
        event("2026-01-01T00:00:00Z", "function_call", name="exec", call_id="a", arguments="{}"),
        event("2026-01-01T00:00:01Z", "function_call_output", call_id="a"),
        event("2026-01-01T00:00:02Z", "custom_tool_call", name="exec", call_id="b", input="x"),
        token(usage),
    ]
    telemetry = trace_telemetry(events)
    assert telemetry["terminal_usage"] == usage
    assert [call["wall_ms"] for call in telemetry["tool_calls"]] == [1000, None]
    assert telemetry["physical_http_request_count"] is None
    totals = token_usage([{"provider": "native", "provider_metadata": telemetry}])[0]
    assert (
        totals["total_tokens"],
        totals["cached_input_tokens"],
        totals["uncached_input_tokens"],
    ) == (110, 80, 20)
    assert trace_telemetry([])["terminal_usage"] is None


def test_delivery_timing_excludes_parent_cleanup_and_keeps_missing_unavailable():
    assert delivery_timing(
        started_at="2026-01-01T00:00:00Z",
        saved_at="2026-01-01T00:00:02Z",
        ended_at="2026-01-01T00:04:02Z",
    ) == {"delivery_ms": 2000, "terminal_ms": 242000, "post_delivery_ms": 240000}
    assert delivery_timing(started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:04:02Z") == {
        "delivery_ms": None,
        "terminal_ms": 242000,
        "post_delivery_ms": None,
    }
    with pytest.raises(ValueError, match="Inconsistent"):
        delivery_timing(started_at="2026-01-01T00:00:02Z", saved_at="2026-01-01T00:00:01Z")
    with pytest.raises(ValueError, match="timezone"):
        delivery_timing(started_at="2026-01-01T00:00:00")


def test_capture_loader_cli_returns_the_shared_source_without_fetching(capsys):
    assert main(["--capture-script"]) == 0
    assert capsys.readouterr().out == CAPTURE_SCRIPT + "\n"
