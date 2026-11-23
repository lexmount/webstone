import base64
import json
import subprocess
from importlib.resources import files
from types import SimpleNamespace
from uuid import UUID

import pytest

from harness.web_fetch.call_boundary import fetch_with_deadline
from harness.web_fetch.methods import method_page_model
from harness.web_fetch.providers import codex
from harness.web_fetch.report_interfaces import method_spec
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.scoring_regressions import load_scoring_regressions

URL = "https://example.com/"
THREAD = str(UUID(int=1))


def trace(url=URL, *continuations):
    events, session = [], []
    for i, target in enumerate((url, *continuations)):
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "web_search",
                    "action": {"type": "other"} if i else {"type": "open_page", "url": url},
                    "results": [{"type": "text_result", "ref_id": f"view{i}", "url": url}],
                },
            }
        )
        entry = {"ref_id": target}
        if i:
            entry["lineno"] = i
        args = json.dumps({"open": [entry], "response_length": "long"})
        call = {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": str(i),
            "input": f"const r = await tools.web__run({args}); text(r)",
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
    session[2]["payload"]["input"] = session[2]["payload"]["input"].replace(', "lineno": 1', "")
    assert not codex.parse_returns(URL, "same-page", events, session).ok


def page_result(url=URL, title="Observed title"):
    return {"type": "text_result", "ref_id": "view0", "url": url, "title": title}


@pytest.mark.parametrize(
    ("result_url", "control_id"),
    [
        (URL, "native-open-returned-page-title"),
        ("https://example.com/other", "native-open-unowned-page-title"),
    ],
)
def test_structured_title_uses_the_shared_scorer(result_url, control_id):
    controls = files("harness.benchmarks").joinpath("scoring-regressions.json")
    control = load_scoring_regressions(controls)[control_id]
    events, session = trace()
    events[0]["item"].update(
        action={"type": "open_page", "url": URL}, results=[page_result(result_url)]
    )
    result = codex.parse_returns(URL, "single", events, session)
    assert result.ok == control["ok"] and result.content == control["artifact"]
    assert result.metadata.get("page_information") == control["metadata"].get("page_information")
    assert result.final_url is None
    metrics = score_attempt(
        control["case"], result.content.encode(), ok=result.ok, metadata=result.metadata
    )
    assert task_passed(result.ok, metrics, "body") == control["expected_task_pass"]


def test_native_title_keeps_the_title_from_an_escape_equivalent_page():
    url = URL + "Python_(programming_language)"
    events, _ = trace(url)
    escaped = URL + "Python_%28programming_language%29"
    events[0]["item"].update(
        action={"type": "open_page", "url": escaped}, results=[page_result(escaped)]
    )
    assert codex.native_open_title(url, events) == "Observed title"
    events[0]["item"]["results"] = [page_result(URL + "Python_%2Fprogramming_language")]
    assert codex.native_open_title(url, events) is None


def test_native_title_cannot_be_inferred_from_a_followup_or_conflicting_result():
    events, session = trace(URL, "view0")
    events[0]["item"].update(
        action={"type": "open_page", "url": URL}, results=[page_result(title=None)]
    )
    events[1]["item"].update(action={"type": "open_page", "url": URL}, results=[page_result()])
    assert codex.native_open_title(URL, events) is None
    events[0]["item"]["results"] = [page_result(), page_result(title="Another title")]
    assert codex.native_open_title(URL, events) is None
    events[0]["item"].update(action={"type": "search"}, results=[page_result()])
    assert "page_information" not in codex.parse_returns(URL, "same-page", events, session).metadata
    events[0]["item"]["action"] = {"type": "open_page", "url": "https://example.com/other"}
    assert codex.native_open_title(URL, events) is None


def jsonl(records):
    return b"".join(json.dumps(record).encode() + b"\n" for record in records)


def make_provider():
    return codex.CodexProvider("codex-single", {"mode": "single", "expected_version": "1.2.3"})


def install_process(monkeypatch, provider, turns, *, stderr=b"fixture stderr\xff"):
    cmds = []
    executable = codex.Path(provider.work.name) / "fixture-codex"
    executable.touch()
    monkeypatch.setattr(codex.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(codex.subprocess, "check_output", lambda *a, **k: b"codex-cli 1.2.3\n")
    monkeypatch.setattr(codex, "repository_env", lambda: {"CODEX_API_KEY": "fixture-key"})

    def launch(cmd, **kwargs):
        cmds.append(cmd)
        assert kwargs["env"]["CODEX_API_KEY"] == "fixture-key"
        assert kwargs["env"]["HOME"] == provider.work.name
        assert "CODEX_API_KEY" not in provider.env
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
            return stdout, stderr

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


def test_preflight_binds_binary_and_repository_credentials(tmp_path, monkeypatch):
    provider = make_provider()
    executable = tmp_path / "codex"
    executable.touch()
    monkeypatch.setattr(codex.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(codex.subprocess, "check_output", lambda *a, **k: b"codex-cli 0.0.0\n")
    assert provider.validate() == ["Codex CLI 1.2.3"]
    monkeypatch.setattr(codex.subprocess, "check_output", lambda *a, **k: b"codex-cli 1.2.3\n")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-key")
    monkeypatch.setattr(codex, "repository_env", lambda: {})
    assert provider.validate() == ["CODEX_API_KEY in the repository .env"]
    assert "CODEX_API_KEY" not in provider.env
    monkeypatch.setattr(codex, "repository_env", lambda: {"CODEX_API_KEY": "fixture-key"})
    assert provider.validate() == []
    assert provider.reported_version == "codex-cli 1.2.3"
    assert "CODEX_API_KEY" not in provider.env
    executable.write_bytes(b"new executable")
    with pytest.raises(ValueError, match="changed after preflight"):
        provider._run("Smoke test", 1, "fixture-key")


def test_codex_modes_have_report_interfaces():
    for provider in ("codex-single", "codex-same-page"):
        row = {"provider": provider, "provider_type": "codex_cli"}
        for language in ("en", "zh"):
            model = method_page_model(provider, provider, [row], language=language)
            assert model["endpoint"] == "codex exec"
            assert method_spec(provider, row, language=language)["request"]["url"] == URL


def test_continuation_rejects_a_foreign_reference_even_when_tool_lists_it():
    events, session = trace(URL, "view1")
    events[0]["item"]["results"].append(
        {"type": "text_result", "ref_id": "view1", "url": URL + "another-page"}
    )
    assert (
        codex.parse_returns(URL, "same-page", events, session).error_type == "codex_open_violation"
    )


def test_continuation_cannot_score_a_return_from_another_page():
    events, session = trace(URL, "view0")
    events[1]["item"]["results"][0]["url"] = URL + "another-page"
    result = codex.parse_returns(URL, "same-page", events, session)
    assert result.error_type == "codex_open_violation"
    assert not result.content


def test_continuation_handles_native_path_escapes_without_equating_path_separators():
    url = URL + "Python_(programming_language)"
    events, session = trace(url, "view0")
    events[0]["item"]["results"][0]["url"] = URL + "Python_%28programming_language%29"
    assert codex.parse_returns(url, "same-page", events, session).ok
    events[0]["item"]["results"][0]["url"] = URL + "Python_%2Fprogramming_language"
    assert not codex.parse_returns(url, "same-page", events, session).ok


def test_duplicate_json_keys_cannot_override_the_declared_target():
    events, session = trace()
    call = session[0]["payload"]
    call["input"] = call["input"].replace(
        '"ref_id":', '"ref_id": "https://other.example/", "ref_id":'
    )
    assert not codex.parse_returns(URL, "single", events, session).ok


def test_codex_runtime_secret_cannot_enter_diagnostics_or_body(monkeypatch):
    provider = make_provider()
    events, session = trace()
    session[1]["payload"]["output"][0]["text"] = "L0: fixture-key"
    stdout = jsonl(
        [
            {"type": "thread.started", "thread_id": THREAD},
            *events,
            {"type": "turn.completed", "usage": {"input_tokens": 100}},
        ]
    )
    install_process(
        monkeypatch, provider, iter([(stdout, jsonl(session), False)]), stderr=b"echo fixture-key"
    )
    result = provider.fetch(EvalCase("one", "One", URL, "static", []), 1)
    evidence = diagnostic(result)
    assert evidence["stderr"] == "echo [REDACTED]"
    assert "fixture-key" not in evidence["session_jsonl"]
    assert result.content == "[REDACTED]"


def test_parser_programming_error_is_not_counted_as_provider_evidence_failure(monkeypatch):
    provider = make_provider()
    events, session = trace()
    stdout = jsonl(
        [
            {"type": "thread.started", "thread_id": THREAD},
            *events,
            {"type": "turn.completed", "usage": {"input_tokens": 100}},
        ]
    )
    install_process(monkeypatch, provider, iter([(stdout, jsonl(session), False)]))

    def bug(*args):
        raise AttributeError("synthetic programming error")

    monkeypatch.setattr(codex, "parse_returns", bug)
    with pytest.raises(AttributeError, match="synthetic programming error"):
        provider.fetch(EvalCase("one", "One", URL, "static", []), 1)


def lexmount_fixture(tmp_path, *, repeat=False):
    from harness.web_fetch.providers import codex_lexmount

    case = EvalCase("one", "One", URL, "static", [])
    payload = {"result": {"main_text": "first reply", "links": [{"href": URL}]}}
    raw = json.dumps(payload).encode()
    (tmp_path / "receipt.json").write_text('{"status": 200}')
    (tmp_path / "response.bin").write_bytes(raw)
    item = {
        "type": "mcp_tool_call",
        "server": "lexmount",
        "tool": "lexmount_fetch",
        "status": "completed",
        "arguments": {"url": URL},
    }
    events = [{"type": "item.completed", "item": item}]
    call = {
        "type": "custom_tool_call",
        "name": "exec",
        "call_id": "first",
        "input": codex_lexmount.call_source(URL),
    }
    wrapped = json.dumps(
        {"content": [{"type": "text", "text": raw.decode()}]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    output = {"type": "custom_tool_call_output", "call_id": "first", "output": [{"text": wrapped}]}
    items = [call, output]
    if repeat:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    **item,
                    "status": "failed",
                    "result": {"content": [{"type": "text", "text": codex_lexmount.REPEAT_ERROR}]},
                },
            }
        )
        items.extend([{**call, "call_id": "second"}, {**output, "call_id": "second"}])
    return case, events, items


def test_lexmount_uses_first_return_even_after_blocked_repeat(tmp_path):
    from harness.web_fetch.providers import codex_lexmount

    case, events, items = lexmount_fixture(tmp_path, repeat=True)
    result = codex_lexmount.parse_returns(case, events, items, tmp_path)
    assert result.ok and result.content == "first reply"
    assert result.metadata["links"] == [{"href": URL}]
    assert result.metadata["blocked_later_attempts"] == 1
    items[1]["output"][0]["text"] = items[1]["output"][0]["text"].replace("first reply", "forged")
    assert not codex_lexmount.parse_returns(case, events, items, tmp_path).ok


def test_lexmount_only_scores_observed_fields_after_truncation(tmp_path):
    from pathlib import Path

    from harness.web_fetch.providers import codex_lexmount
    from harness.web_fetch.runner import persist_result_artifacts

    case, events, items = lexmount_fixture(tmp_path)
    block = items[1]["output"][0]
    blob = block["text"]
    block["text"] = blob[: blob.index(" reply")] + "…5 tokens truncated…"
    result = codex_lexmount.parse_returns(case, events, items, tmp_path)
    assert result.ok and result.content == "first"
    assert result.metadata["tool_output_truncated"]
    assert "links" not in result.metadata
    artifact = persist_result_artifacts(
        tmp_path, Path("artifacts/first.txt"), result, request_url=URL
    )
    assert artifact.read_text() == "first"
    witness = tmp_path / result.metadata["raw_response_path"]
    assert json.loads(witness.read_bytes()) == {"result": {"main_text": "first"}}


def test_lexmount_scores_both_retained_sides_of_middle_truncation(tmp_path):
    from harness.web_fetch.providers import codex_lexmount

    case, events, items = lexmount_fixture(tmp_path)
    block = items[1]["output"][0]
    block["text"] = block["text"].replace("st re", "…5 tokens truncated…")
    result = codex_lexmount.parse_returns(case, events, items, tmp_path)
    assert result.ok and result.content == "fir\nply"
    assert result.metadata["tool_output_truncated"]
    assert result.metadata["links"] == [{"href": URL}]


@pytest.mark.parametrize("truncated", [False, True])
def test_lexmount_records_unparseable_depth_as_provider_failure(tmp_path, truncated):
    import sys

    from harness.web_fetch.providers import codex_lexmount

    case, events, items = lexmount_fixture(tmp_path)
    depth = sys.getrecursionlimit() + 20
    raw = '{"result":{"nested":' + "[" * depth + "0" + "]" * depth + ',"main_text":"safe"}}'
    (tmp_path / "response.bin").write_bytes(raw.encode())
    wrapped = json.dumps(
        {"content": [{"type": "text", "text": raw}]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if truncated:
        wrapped = wrapped[: wrapped.index("main_text")] + "…5 tokens truncated…"
    items[1]["output"][0]["text"] = wrapped
    result = codex_lexmount.parse_returns(case, events, items, tmp_path)
    assert not result.ok and result.status_code == 200
    assert result.error_type == "lexmount_uninspectable_response"
    assert result.content == ""


def test_lexmount_first_call_requires_budget(tmp_path):
    from harness.web_fetch.providers import codex_lexmount

    case, events, items = lexmount_fixture(tmp_path)
    items[0]["input"] = items[0]["input"].split("\n", 1)[1]
    assert (
        codex_lexmount.parse_returns(case, events, items, tmp_path).error_type
        == "codex_lexmount_budget"
    )


def test_lexmount_bridge_sends_one_auto_request(monkeypatch, tmp_path):
    import io
    import time

    from harness.web_fetch.providers import codex_lexmount
    from harness.web_fetch.providers.http_client import HttpResponse

    (tmp_path / "case.json").write_text(
        json.dumps({"url": URL, "deadline": time.monotonic() + 300})
    )
    calls = []
    monkeypatch.setattr(
        codex_lexmount,
        "repository_env",
        lambda: {
            "LEXMOUNT_API_KEY": "fixture-key",
            "LEXMOUNT_PROJECT_ID": "fixture-project",
        },
    )

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return HttpResponse(200, url, {}, b'{"result":{"main_text":"first"}}')

    monkeypatch.setattr(codex_lexmount, "request", request)
    requests = [
        {
            "id": i,
            "method": "tools/call",
            "params": {
                "name": "lexmount_fetch",
                "arguments": {"url": url},
            },
        }
        for i, url in enumerate(("https://wrong.invalid", URL, URL))
    ]
    monkeypatch.setattr(
        codex_lexmount.sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)))
    )
    out = io.StringIO()
    monkeypatch.setattr(codex_lexmount.sys, "stdout", out)
    codex_lexmount.serve(tmp_path)
    replies = [json.loads(line)["result"] for line in out.getvalue().splitlines()]
    assert [reply.get("isError", False) for reply in replies] == [True, False, True]
    assert replies[-1]["content"][0]["text"] == codex_lexmount.REPEAT_ERROR
    assert len(calls) == 1
    url, args = calls[0]
    assert url == codex_lexmount.ENDPOINT
    assert args["method"] == "POST" and not args["allow_redirects"]
    assert args["json_body"] == {"extract": {"url": URL, "options": {"engine_preference": "auto"}}}
    assert args["max_bytes"] == 20_000_000 and 290 < args["timeout"] <= 300


@pytest.mark.parametrize("oversized", [False, True])
def test_lexmount_bridge_caps_http_body(monkeypatch, tmp_path, oversized):
    import time
    from types import SimpleNamespace

    from harness.web_fetch.providers import codex_lexmount, http_client

    case, events, items = lexmount_fixture(tmp_path)
    response_path = tmp_path / "response.bin"
    receipt_path = tmp_path / "receipt.json"
    raw = response_path.read_bytes()
    response_path.unlink()
    receipt_path.unlink()
    limit = len(raw)
    if oversized:
        raw += b" "
    reads = []
    closed = []

    def read(size=-1):
        reads.append(size)
        return raw if size < 0 else raw[:size]

    response = SimpleNamespace(
        status=200,
        headers={},
        geturl=lambda: codex_lexmount.ENDPOINT,
        read=read,
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(
        http_client.urllib.request,
        "build_opener",
        lambda handler: SimpleNamespace(open=lambda request, timeout: response),
    )
    monkeypatch.setattr(codex_lexmount, "MAX_RESPONSE_BYTES", limit)
    monkeypatch.setattr(
        codex_lexmount,
        "repository_env",
        lambda: {"LEXMOUNT_API_KEY": "fixture-key", "LEXMOUNT_PROJECT_ID": "fixture-project"},
    )
    tool_reply = codex_lexmount._fetch_once(
        tmp_path,
        {"url": URL, "deadline": time.monotonic() + 10},
    )
    assert reads == [limit + 1] and closed == [True]
    if oversized:
        assert tool_reply["isError"]
        assert not response_path.exists()
        events[0]["item"]["status"] = "failed"
        result = codex_lexmount.parse_returns(case, events, items, tmp_path)
        assert not result.ok and result.status_code == 200
        assert result.error_type == "lexmount_response_too_large"
        assert result.metadata["response_limit_bytes"] == limit
    else:
        assert response_path.read_bytes() == raw
        assert not tool_reply.get("isError")
        assert codex_lexmount.parse_returns(case, events, items, tmp_path).ok


@pytest.mark.parametrize(
    "raw, rejection",
    [
        (rb'{"result":{"main_text":"fixture-key"}}', "credential_reflection"),
        (rb'{"result":{"main_text":"\u0066ixture-key"}}', "credential_reflection"),
        (rb'{"result":{"main_text":"safe","\u0066ixture-key":"value"}}', "credential_reflection"),
        (rb'{"result":{"nested":["\u0066ixture-key"]}}', "credential_reflection"),
        (
            rb'{"result":{"main_text":"\u0066ixture-key","main_text":"safe"}}',
            "credential_reflection",
        ),
        (rb'{"result":{"main_text":"\u0066ixture-key"}', "uninspectable_response"),
        (rb'{"result":{"main_text":"some\nplain text"}}', None),
    ],
)
def test_lexmount_bridge_inspects_credentials_before_retaining_body(
    monkeypatch, tmp_path, raw, rejection
):
    import time

    from harness.web_fetch.providers import codex_lexmount, http_client

    monkeypatch.setattr(
        codex_lexmount,
        "repository_env",
        lambda: {"LEXMOUNT_API_KEY": "fixture-key", "LEXMOUNT_PROJECT_ID": "fixture-project"},
    )
    monkeypatch.setattr(
        codex_lexmount,
        "request",
        lambda *args, **kwargs: http_client.HttpResponse(200, codex_lexmount.ENDPOINT, {}, raw),
    )
    reply = codex_lexmount._fetch_once(
        tmp_path,
        {"url": URL, "deadline": time.monotonic() + 10},
    )
    response_path = tmp_path / "response.bin"
    receipt = json.loads((tmp_path / "receipt.json").read_bytes())
    if rejection:
        assert reply["isError"] and "fixture-key" not in json.dumps(reply)
        assert not response_path.exists()
        assert receipt == {"status": 200, rejection: True}
    else:
        assert not reply.get("isError")
        assert response_path.read_bytes() == raw and receipt == {"status": 200}


def test_lexmount_provider_public_entry_resumes_same_thread(monkeypatch):
    provider = codex.CodexProvider(
        "codex-lexmount-auto", {"mode": "lexmount-auto", "expected_version": "1.2.3"}
    )

    def turns():
        for total in (100, 250):
            case, events, items = lexmount_fixture(provider._tool_directory)
            stdout = jsonl(
                [
                    {"type": "thread.started", "thread_id": THREAD},
                    *events,
                    {"type": "turn.completed", "usage": {"input_tokens": total}},
                ]
            )
            session = jsonl([{"type": "response_item", "payload": item} for item in items])
            yield stdout, session, False

    commands = install_process(monkeypatch, provider, turns())
    monkeypatch.setattr(
        codex,
        "repository_env",
        lambda: {
            "CODEX_API_KEY": "fixture-key",
            "LEXMOUNT_API_KEY": "fixture-lexmount-key",
            "LEXMOUNT_PROJECT_ID": "fixture-project",
        },
    )
    case = EvalCase("one", "One", URL, "static", [])
    first, second = [fetch_with_deadline(provider, case, 300)[0] for _ in range(2)]
    assert first.ok and second.ok and second.metadata["usage_delta"] == {"input_tokens": 150}
    assert commands[0][1:3] == ["exec", "--json"]
    assert commands[1][1:4] == ["exec", "resume", THREAD]
    for command in commands:
        assert 'web_search="disabled"' in command
        assert "tool_output_token_limit=200000" in command
        assert "mcp_servers.lexmount.tool_timeout_sec=300" in command
        assert "fixture-lexmount-key" not in " ".join(command)


def test_lexmount_mode_has_report_contract():
    name = "codex-lexmount-auto"
    row = {"provider": name, "provider_type": "codex_cli"}
    for language in ("en", "zh"):
        assert (
            "Lexmount Extract Auto"
            in method_page_model(name, name, [row], language=language)["contract"]
        )
        request = method_spec(name, row, language=language)["request"]["extract"]
        assert request["options"] == {"engine_preference": "auto"}
