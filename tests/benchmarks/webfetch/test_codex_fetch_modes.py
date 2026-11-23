import base64
import json
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from harness.web_fetch.call_boundary import fetch_with_deadline
from harness.web_fetch.methods import method_page_model
from harness.web_fetch.providers import codex
from harness.web_fetch.report_interfaces import method_spec
from lexbench_webfetch.models import EvalCase

URL = "https://example.com/"
THREAD = str(UUID(int=1))


def trace(url=URL, *continuations):
    events, session = [], []
    for i, target in enumerate((url, *continuations)):
        events.append(
            {
                "type": "item.completed",
                "item": {"type": "web_search", "results": [{"ref_id": f"view{i}"}]},
            }
        )
        position = f",lineno:{i}" if i else ""
        call = {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": str(i),
            "input": "const r = await tools.web__run({open:[{ref_id:"
            + json.dumps(target)
            + position
            + '}],response_length:"long"}); text(r)',
        }
        output = {
            "type": "custom_tool_call_output",
            "call_id": str(i),
            "output": [{"text": f"L{i}: paragraph {i}"}],
        }
        session.extend({"type": "response_item", "payload": p} for p in (call, output))
    session.append(
        {"type": "response_item", "payload": {"type": "message", "text": "model answer"}}
    )
    return events, session


@pytest.mark.parametrize(
    "url", [URL, "https://example.com/,ref_id:foo", "https://example.com/,lineno:%22"]
)
def test_single_open_only_scores_tool_text(url):
    result = codex.parse_returns(url, "single", *trace(url))
    assert result.ok and result.content == "paragraph 0"
    assert not codex.parse_returns(url, "single", *trace(url, "view0")).content


def test_page_text_is_distinct_from_a_tool_error():
    events, session = trace()
    block = session[1]["payload"]["output"][0]
    block["text"] = "L0: Failed to fetch is an HTTP client error.\nL1: Internal Error"
    assert codex.parse_returns(URL, "single", events, session).ok
    block["text"] += "\nInternal Error ()"
    assert codex.parse_returns(URL, "single", events, session).error_type == "codex_tool_error"


def test_continuation_accepts_only_this_pages_references():
    result = codex.parse_returns(URL, "same-page", *trace(URL, "view0"))
    assert result.ok and result.content == "paragraph 0\nparagraph 1"
    for invalid_target in (URL, "elsewhere"):
        assert not codex.parse_returns(URL, "same-page", *trace(URL, invalid_target)).ok
    events, session = trace(URL, "view0")
    session[2]["payload"]["input"] = session[2]["payload"]["input"].replace(",lineno:1", "")
    assert not codex.parse_returns(URL, "same-page", events, session).ok


def jsonl(records):
    return b"".join(json.dumps(record).encode() + b"\n" for record in records)


def make_provider():
    return codex.CodexProvider("codex-single", {"mode": "single", "expected_version": "1.2.3"})


def install_process(monkeypatch, provider, turns):
    cmds = []

    def launch(cmd, **kwargs):
        cmds.append(cmd)
        process = SimpleNamespace(returncode=None, args=cmd)
        stdout, session, cancelled = next(turns)
        if session:
            path = provider.home / "sessions" / f"rollout-{THREAD}.jsonl"
            path.parent.mkdir(exist_ok=True)
            with path.open("ab") as stream:
                stream.write(session)

        def communicate(timeout):
            if cancelled and process.returncode is None:
                raise subprocess.TimeoutExpired(cmd, timeout)
            process.returncode = -9 if cancelled else 0
            return stdout, b"fixture stderr\xff"

        process.communicate = communicate
        return process

    def terminate(process):
        if process:
            process.returncode = -9

    monkeypatch.setattr(codex.subprocess, "Popen", launch)
    monkeypatch.setattr(codex, "terminate_owned_process_tree", terminate)
    return cmds


def diagnostic(result):
    return json.loads(base64.b64decode(result.metadata["_diagnostic_log_b64"]))


@pytest.mark.parametrize("started", [False, True])
def test_timeout_preserves_partial_native_records(monkeypatch, started):
    provider = make_provider()
    thread = jsonl([{"type": "thread.started", "thread_id": THREAD}]) if started else b""
    stdout = thread + b'{"type":"item.comple'
    session = b'{"type":"response_it\xf0' if started else b""
    install_process(monkeypatch, provider, iter([(stdout, session, True)]))
    case = EvalCase("one", "One", URL, "static", [])
    result, _ = fetch_with_deadline(provider, case, 1)
    assert result.error_type == "timeout" and not result.content
    evidence = diagnostic(result)
    assert evidence["stdout_jsonl"].encode("utf-8", errors="surrogateescape") == stdout
    assert evidence["session_jsonl"].encode("utf-8", errors="surrogateescape") == session
    assert evidence["stderr"].encode("utf-8", errors="surrogateescape") == b"fixture stderr\xff"


def test_resume_scores_only_new_tool_records_and_keeps_usage_attribution(monkeypatch):
    provider = make_provider()
    events, session = trace()
    thread = [{"type": "thread.started", "thread_id": THREAD}]
    native = jsonl(session)

    def completed(total):
        return jsonl(
            [*thread, *events, {"type": "turn.completed", "usage": {"input_tokens": total}}]
        )

    commands = install_process(
        monkeypatch,
        provider,
        iter(
            [
                (completed(100), native, False),
                (jsonl(thread), b'{"type":"partial', True),
                (completed(300), native, False),
                (completed(400), native, False),
            ]
        ),
    )
    case = EvalCase("one", "One", URL, "static", [])
    first, timed_out, resumed, following = [
        fetch_with_deadline(provider, case, 1)[0] for _ in range(4)
    ]
    assert first.ok and first.metadata["usage_delta"] == {"input_tokens": 100}
    assert not timed_out.ok
    assert resumed.ok and resumed.content == "paragraph 0"
    assert resumed.metadata["usage_delta"] is None
    assert following.ok and following.metadata["usage_delta"] == {"input_tokens": 100}
    assert commands[0][1:3] == ["exec", "--json"]
    assert all(command[1:4] == ["exec", "resume", THREAD] for command in commands[1:])


def test_preflight_binds_binary_and_repository_credentials(monkeypatch):
    provider = make_provider()
    monkeypatch.setattr(codex.shutil, "which", lambda _: "/path/to/codex")
    monkeypatch.setattr(codex.subprocess, "check_output", lambda *a, **k: b"codex-cli 0.0.0\n")
    assert provider.validate() == ["Codex CLI 1.2.3"]
    monkeypatch.setattr(codex.subprocess, "check_output", lambda *a, **k: b"codex-cli 1.2.3\n")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-key")
    monkeypatch.setattr(codex, "repository_env", lambda: {})
    assert provider.validate() == ["CODEX_API_KEY in the repository .env"]
    assert "CODEX_API_KEY" not in provider.env
    monkeypatch.setattr(codex, "repository_env", lambda: {"CODEX_API_KEY": "fixture-key"})
    assert provider.validate() == []
    assert provider.env["CODEX_API_KEY"] == "fixture-key"


def test_codex_modes_have_report_interfaces():
    for provider in ("codex-single", "codex-same-page"):
        row = {"provider": provider, "provider_type": "codex_cli"}
        for language in ("en", "zh"):
            model = method_page_model(provider, provider, [row], language=language)
            assert model["endpoint"] == "codex exec"
            assert method_spec(provider, row, language=language)["request"]["url"] == URL
