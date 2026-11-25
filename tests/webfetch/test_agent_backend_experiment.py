"""Offline behavior tests for the agent fetch backend experiment (no network, no model calls)."""
import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from harness.web_fetch.agent_backend import arms, backend_params, experiment, scoring

FAKE_CLAUDE = r'''#!{python}
import json, os, sys
route = os.environ.get("FAKE_ROUTE", "delegated")
debug = sys.argv[sys.argv.index("--debug-file") + 1]
open(debug + ".deadline", "w").write(os.environ.get("CLAUDE_CODE_WEBFETCH_DEADLINE_MS", ""))
def emit(o): print(json.dumps(o))
def agent(i): emit({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": i, "name": "Agent", "input": {{}}}}]}}}})
USAGE = "report\nagentId: x\n<usage>subagent_tokens: 321\ntool_uses: 1\nduration_ms: 4500</usage>"
emit({{"type": "system", "subtype": "init", "claude_code_version": "9.9.9"}})
agent("a1")
if route in ("delegated", "double", "fetcherror"):
    emit({{"type": "assistant", "parent_tool_use_id": "a1", "message": {{"content": [{{"type": "tool_use", "id": "w1", "name": "WebFetch", "input": {{}}}}]}}}})
    emit({{"type": "user", "parent_tool_use_id": "a1", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "w1", "is_error": route == "fetcherror", "content": "boom" if route == "fetcherror" else "Fetched x\n<fetched-web-content>\nPAGE BODY\n</fetched-web-content>"}}]}}}})
    open(debug, "w").write("[INFO] [Stall] tool_dispatch_end tool=WebFetch toolUseId=w1 outcome=ok durationMs=1234\n")
    emit({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "a1", "content": USAGE}}]}}}})
    if route == "double":
        agent("a2")
elif route == "async":  # Claude Code launched the subagent in the background: no usage report
    emit({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "a1", "content": "Async agent launched successfully."}}]}}}})
elif route == "bash":
    emit({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "b1", "name": "Bash", "input": {{"command": "curl -sL https://example.com"}}}}]}}}})
emit({{"type": "result", "duration_ms": 1000, "duration_api_ms": 900, "num_turns": 2, "is_error": False,
      "total_cost_usd": 0.01, "result": "final answer",
      "usage": {{"input_tokens": 3, "output_tokens": 7, "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13}}}})
'''


@pytest.fixture
def fake_claude(tmp_path):
    path = tmp_path / "claude"
    path.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def cases(n=12):
    return {f"c{i:02d}": {"id": f"c{i:02d}", "category": f"cat{i % 4}", "task_kind": "body", "url": "https://example.com/"}
            for i in range(n)}


def test_selection_is_deterministic_stratified_and_honors_include():
    first = experiment.select_cases(cases(), limit=6, seed=1, include=["c05"])
    assert first == experiment.select_cases(cases(), limit=6, seed=1, include=["c05"])
    assert "c05" in first and len(first) == 6
    assert len({cases()[c]["category"] for c in first}) == 4
    with pytest.raises(ValueError):
        experiment.select_cases(cases(), case_ids=["missing"])


def test_excluded_tasks_are_never_sampled_and_conflicts_are_rejected():
    picked = experiment.select_cases(cases(), limit=0, exclude=["c01", "c02"])
    assert "c01" not in picked and "c02" not in picked and len(picked) == 10
    with pytest.raises(ValueError):
        experiment.select_cases(cases(), limit=3, include=["c01"], exclude=["c01"])
    with pytest.raises(ValueError):
        experiment.select_cases(cases(), limit=3, exclude=["missing"])


def test_tasks_a_backend_cannot_be_configured_for_are_dropped_for_every_arm():
    picked = experiment.select_cases(cases(), limit=0, ineligible={"c03": "moli: no media kind"})
    assert "c03" not in picked and len(picked) == 11
    with pytest.raises(ValueError, match="not configurable"):
        experiment.select_cases(cases(), case_ids=["c03"], ineligible={"c03": "moli: no media kind"})


def test_plan_rotates_arm_order_and_covers_every_pair():
    arm_names = ["builtin", "lexmount", "moli"]
    plan = experiment.make_plan(sorted(cases()), arm_names, reps=2, seed=7)
    assert {(r["case_id"], r["rep"], r["arm"]) for r in plan} == {
        (c, rep, a) for c in cases() for rep in (1, 2) for a in arm_names}
    firsts = [plan[i]["arm"] for i in range(0, len(plan), 3)]
    assert len(set(firsts)) == 3  # order is rotated, not fixed
    with pytest.raises(ValueError):
        experiment.make_plan(["c00"], ["nope"], 1, 1)


@pytest.mark.parametrize("arm", ["builtin", "lexmount", "moli"])
def test_arm_commands_are_identical_and_only_the_server_behind_webfetch_differs(tmp_path, arm):
    command = arms.build_command(arm, "p", tmp_path, claude_bin="claude", model="m")
    servers = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    builtin = arms.build_command("builtin", "p", tmp_path, claude_bin="claude", model="m")
    assert command == builtin  # same chain, same flags, same files
    assert command[command.index("--tools") + 1] == "Agent,WebFetch" and "--agents" not in command  # stock web-fetch agent
    env = arms.arm_env(arm, {"timeout_seconds": 45})
    assert env["CLAUDE_CODE_WEBFETCH_DEADLINE_MS"] == "45000"
    if arm == "builtin":
        assert servers == {} and "CLAUDE_CODE_WEBFETCH_MCP_TOOL" not in env
    else:
        assert servers[arm]["args"][2:4] == ["--backend", arm]
        assert json.loads(env["CLAUDE_CODE_WEBFETCH_MCP_TOOL"]) == {"server": arm, "tool": "web_fetch"}
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in command  # identical isolation in every arm


def test_the_builtin_tool_gets_the_fetch_time_limit_as_its_deadline(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ROUTE", "delegated")
    monkeypatch.delenv("CLAUDE_CODE_WEBFETCH_DEADLINE_MS", raising=False)
    arms.run_one("builtin", "c00", "https://example.com/", "prompt", tmp_path / "r", claude_bin=fake_claude,
                 params={"timeout_seconds": 45})
    assert (tmp_path / "r" / "debug.log.deadline").read_text(encoding="utf-8") == "45000"


def test_valid_run_records_the_reported_subagent_usage(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ROUTE", "delegated")
    record = arms.run_one("builtin", "c00", "https://example.com/", "prompt", tmp_path / "r", claude_bin=fake_claude)
    assert record["valid"] and record["invalid_reasons"] == [] and record["claude_code_version"] == "9.9.9"
    assert (record["subagent_tokens"], record["subagent_duration_ms"], record["subagent_tool_uses"]) == (321, 4500, 1)
    assert record["fetch_tool_ms"] == 1234 and record["route"] == "delegated_WebFetch"
    assert (record["input_tokens"], record["output_tokens"]) == (3, 7) and record["raw_tool_result_chars"] > 0


def test_a_failed_fetch_call_is_recorded_without_making_the_run_invalid(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ROUTE", "fetcherror")
    record = arms.run_one("builtin", "c00", "https://example.com/", "p", tmp_path / "r", claude_bin=fake_claude)
    assert record["fetch_failed"] is True and record["valid"] is True  # a backend failure is a result, not a protocol break


@pytest.mark.parametrize("raw,flagged,expected", [("Fetch failed (status 1): timeout", False, True), ("", False, True), ("ok page", True, True),
                                                  ("The server returned HTTP 502 Bad Gateway.", False, True),
                                                  ("Claude Code is unable to fetch from www.bbc.com", False, True),
                                                  ("Fetched x\n<fetched-web-content>body</fetched-web-content>", False, False),
                                                  ("# Title\n\nbody", False, False)])
def test_fetch_failure_rule_is_the_same_for_every_arm(raw, flagged, expected):
    assert arms.fetch_failed(raw, flagged) is expected


@pytest.mark.parametrize("route,reason", [("async", "no_subagent_usage_report"), ("double", "agent_launches=2"),
                                          ("bash", "route=bash_network"), ("none", "route=none")])
def test_a_run_that_breaks_the_protocol_is_invalid_with_no_substitute_measurement(tmp_path, fake_claude, monkeypatch, route, reason):
    monkeypatch.setenv("FAKE_ROUTE", route)
    record = arms.run_one("builtin", "c00", "https://example.com/", "p", tmp_path / "r", claude_bin=fake_claude)
    assert not record["valid"] and reason in record["invalid_reasons"]
    if route == "async":
        assert record["subagent_tokens"] is None  # never filled in from other data


def test_every_arm_must_delegate_to_webfetch(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ROUTE", "delegated")
    monkeypatch.setenv("LEXMOUNT_WEBFETCH_CLI", fake_claude)
    monkeypatch.setenv("LEXMOUNT_API_KEY", "k")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "p")
    record = arms.run_one("lexmount", "c00", "https://example.com/", "p", tmp_path / "r", claude_bin=fake_claude)
    assert record["route"] == "delegated_WebFetch" and not any(r.startswith("route=") for r in record["invalid_reasons"])
    monkeypatch.setenv("FAKE_ROUTE", "bash")
    record = arms.run_one("lexmount", "c00", "https://example.com/", "p", tmp_path / "s", claude_bin=fake_claude)
    assert "route=bash_network" in record["invalid_reasons"]


def test_backends_require_their_prerequisites(tmp_path, fake_claude, monkeypatch):
    for name in arms.LEXMOUNT_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="LEXMOUNT_API_KEY"):
        arms.run_one("lexmount", "c00", "https://example.com/", "p", tmp_path / "a", claude_bin=fake_claude)
    monkeypatch.setenv("MOLI_BINARY", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="MOLI_BINARY"):
        arms.run_one("moli", "c00", "https://example.com/", "p", tmp_path / "b", claude_bin=fake_claude)


class _Stub(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Stub.seen.append(body)
        ok = "good" in body["extract"]["url"]
        payload = {"result": {"main_text": "stub markdown", "title": "T"}, "request_id": "r"} if ok else {"error": {"message": "nope"}}
        data = json.dumps(payload).encode()
        self.send_response(200 if ok else 502)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _mcp(backend, env, tmp_path, urls, params=None):
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    lines += [{"jsonrpc": "2.0", "id": 3 + i, "method": "tools/call", "params": {"arguments": {"url": u}}} for i, u in enumerate(urls)]
    log = tmp_path / "calls.jsonl"
    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps({"timeout_seconds": 20, **(params or {})}), encoding="utf-8")
    out = subprocess.run([sys.executable, "-I", str(arms.MCP_SERVER), "--backend", backend, "--log", str(log), "--params", str(params_file), "--out-dir", str(tmp_path / "out")],
                         env={**os.environ, **env},
                         capture_output=True, text=True, input="\n".join(json.dumps(x) for x in lines) + "\n", timeout=30).stdout
    replies = {json.loads(l)["id"]: json.loads(l)["result"] for l in out.splitlines()}
    return replies, [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]


FAKE_LEXMOUNT_CLI = r'''#!{python}
import json, os, sys, urllib.request
url = sys.argv[sys.argv.index("--url") + 1]
request = urllib.request.Request(os.environ["LEXMOUNT_WEBFETCH_BASE_URL"] + "/v1/extract", method="POST",
                                 data=json.dumps({{"extract": {{"url": url}}}}).encode())
payload = json.loads(urllib.request.urlopen(request).read())
print("# WebFetch Extract Result\n\n- **Title:** " + payload["result"]["title"] + "\n\n## Main Text\n\n" + payload["result"]["main_text"])
'''


def test_lexmount_backend_calls_the_api_once_and_returns_the_official_markdown(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cli = tmp_path / "webfetch-cli"
    cli.write_text(FAKE_LEXMOUNT_CLI.format(python=sys.executable), encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    env = {"LEXMOUNT_API_KEY": "k", "LEXMOUNT_PROJECT_ID": "p", "LEXMOUNT_BASE_URL": f"http://127.0.0.1:{server.server_port}",
           "LEXMOUNT_WEBFETCH_CLI": str(cli)}
    options = {"engine_preference": "lightmount_domstable", "timeout_ms": 30000}
    _Stub.seen.clear()
    replies, rows = _mcp("lexmount", env, tmp_path, ["http://x/good", "http://x/bad"], params={"options": options, "use_proxy": True})
    server.shutdown()
    official = "# WebFetch Extract Result\n\n- **Title:** T\n\n## Main Text\n\nstub markdown\n"
    assert replies[3]["content"][0]["text"] == official and replies[3]["isError"] is False  # what WebFetch receives
    assert (tmp_path / "out" / rows[0]["file"]).read_text(encoding="utf-8") == official and rows[0]["ok"] is True
    assert len(_Stub.seen) == 2 and _Stub.seen[0] == {"extract": {"url": "http://x/good", "options": options}, "use_proxy": True}  # one API call per fetch; the CLI only replays it
    assert replies[2]["tools"][0]["name"] == "web_fetch"
    assert replies[4]["isError"] is True and "status 502" in replies[4]["content"][0]["text"] and rows[1]["sha256"] is None
    assert json.loads((tmp_path / "out" / rows[1]["payload"]).read_text(encoding="utf-8")) == {"error": {"message": "nope"}}


def test_moli_backend_runs_the_local_binary_with_the_harness_chosen_arguments_only(tmp_path):
    fake = tmp_path / "moli"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nif [ "$last" = "https://x/bad" ]; then echo broken >&2; exit 3; fi\necho "args: $*"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    params = {"argv": ["fetch", "--wait-until", "domstable"]}
    replies, rows = _mcp("moli", {"MOLI_BINARY": str(fake)}, tmp_path, ["https://x/good", "https://x/bad"], params=params)
    assert replies[3]["isError"] is False
    assert replies[3]["content"][0]["text"].strip() == "args: fetch --wait-until domstable https://x/good"
    assert replies[4]["isError"] is True and "status 3" in replies[4]["content"][0]["text"] and "broken" in replies[4]["content"][0]["text"]
    assert [r["status"] for r in rows] == [0, 3]


def test_moli_backend_refuses_to_run_without_configured_arguments(tmp_path):
    replies, rows = _mcp("moli", {"MOLI_BINARY": "/bin/echo"}, tmp_path, ["https://x/good"], params={})
    assert replies[3]["isError"] is True and "no moli arguments" in replies[3]["content"][0]["text"]  # no silent default


@pytest.fixture
def fake_moli(tmp_path):
    path = tmp_path / "moli-bin"
    path.write_text('#!/bin/sh\necho "moli 1.1.27"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _task(track, **extra):
    return {"id": "t", "name": "t", "url": "https://example.com/", "category": "c", "expected_any": [], "track": track, **extra}


def test_moli_parameters_follow_the_task_family_and_every_task_is_configurable(fake_moli):
    provider = backend_params.moli_provider(fake_moli)
    web = backend_params.params_for("moli", _task("web"), timeout_seconds=45, moli=provider)["argv"]
    dynamic = backend_params.params_for("moli", _task("dynamic", retrieval_hints={"version": 3, "mode": "native"}),
                                        timeout_seconds=45, moli=provider)["argv"]
    assert web[web.index("--wait-until") + 1] == "done" and "--layout" not in web
    assert dynamic[dynamic.index("--wait-until") + 1] == "domstable" and "--layout" in dynamic and "--with-frames" in dynamic
    # documents carry no input media declaration in the current edition: one family setting, moli's automatic output
    document = backend_params.params_for("moli", _task("document"), timeout_seconds=45, moli=provider)["argv"]
    assert "--dump" not in document and document[document.index("--wait-until") + 1] == "done"
    for argv in (web, dynamic, document):  # one time limit for every family; moli times out inside the hard stop
        assert [argv[argv.index(flag) + 1] for flag in ("--timeout-ms", "--http-timeout", "--http-connect-timeout")] == ["40500"] * 3
    assert backend_params.ineligible({"a": _task("web"), "b": _task("document")}, ["builtin", "moli"],
                                     timeout_seconds=45, moli=provider) == {}


def test_one_fetch_time_limit_reaches_every_backend(tmp_path, fake_moli):
    for backend in ("builtin", "lexmount", "moli"):
        for task in (_task("web"), _task("document"), _task("dynamic", retrieval_hints={"version": 3, "mode": "native"})):
            params = backend_params.params_for(backend, task, timeout_seconds=45,
                                               moli=backend_params.moli_provider(fake_moli))
            assert params["timeout_seconds"] == 45
            if backend == "lexmount":
                assert params["options"]["timeout_ms"] == 40500
    with pytest.raises(ValueError):
        backend_params.params_for("builtin", _task("web"), timeout_seconds=0)


def test_lexmount_parameters_match_what_the_provider_sends(monkeypatch):
    from harness.web_fetch.providers import lexmount as provider_module
    hints = {"version": 1, "wait_until": "domcontentloaded",
             "completion": {"selector": ".q", "minimum_count": 3, "timeout_ms": 30000, "delay_ms": 250}}
    sent = {}

    def fake_request(url, **kwargs):
        sent["body"] = kwargs["json_body"]
        raise RuntimeError("stop after capturing the request")
    monkeypatch.setattr(provider_module, "request", fake_request)
    for track, extra in (("dynamic", {"retrieval_hints": hints}), ("web", {})):
        task = _task(track, **extra)
        with pytest.raises(RuntimeError):
            provider_module.LexmountProvider("lexmount", {"api_key": "k", "project_id": "p"}).fetch(
                backend_params.to_case(task), 45)
        mine = backend_params.params_for("lexmount", task, timeout_seconds=45)
        if track == "dynamic":  # the provider's dynamic request, unchanged
            assert sent["body"]["extract"] == {"url": task["url"], "options": mine["options"]}
        else:  # other tasks add only the shared time limit to the provider's plain request
            assert sent["body"]["extract"] == {"url": task["url"]} and mine["options"] == {"timeout_ms": 40500}
    native = backend_params.params_for("lexmount", _task("dynamic", retrieval_hints={"version": 3, "mode": "native"}),
                                       timeout_seconds=45)
    assert native == {"timeout_seconds": 45, "use_proxy": True, "options": {"engine_preference": "lightmount_domstable", "timeout_ms": 40500}}
    assert backend_params.params_for("builtin", _task("web"), timeout_seconds=45) == {"timeout_seconds": 45}


def _record(case, arm, **over):
    base = {"arm": arm, "case_id": case, "rep": 1, "exit_code": 0, "timed_out": False, "is_error": False, "wall_ms": 1000,
            "fetch_tool_ms": 500, "llm_cost_usd": 0.02, "input_tokens": 1, "output_tokens": 1, "cache_creation_tokens": 0,
            "cache_read_tokens": 0, "backend_calls": 1 if arm != "builtin" else 0, "route": "x", "final_text": "good",
            "subagent_tokens": 100, "subagent_duration_ms": 2000, "subagent_tool_uses": 1, "haiku_model_cost_usd": 0.001,
            "valid": True, "invalid_reasons": [], "fetch_failed": False}
    base.update(over)
    return base


def _save(root, record, attempt=1):
    d = root / "runs" / f"{record['case_id']}__{record['arm']}__r1__a{attempt}"
    d.mkdir(parents=True)
    (d / "markdown.txt").write_text(record["final_text"], encoding="utf-8")  # the tests use final_text as the content that is scored
    (d / "record.json").write_text(json.dumps(record), encoding="utf-8")


def test_one_chance_per_run_every_task_counts_and_invalid_runs_are_never_averaged(tmp_path, monkeypatch):
    case_map = {c: {"id": c, "task_kind": "body"} for c in ("a", "b", "c", "d")}
    monkeypatch.setattr(scoring, "evaluate_response", lambda case, response, **kw: {"passed": response == b"good"})
    (tmp_path / "plan.json").write_text(json.dumps({"args": {}, "versions": {"claude_code": "v"}, "dataset": {"id": "d", "sha256": "0" * 64},
                                                    "cases": list(case_map), "runs": []}), encoding="utf-8")
    for case in case_map:
        _save(tmp_path, _record(case, "builtin", final_text="good" if case == "a" else "no", subagent_tokens=1000))
        _save(tmp_path, _record(case, "moli", route="m", subagent_tokens=200))
        # lexmount broke the protocol on task b (usage report withheld): not passed, the task still counts
        bad = case == "b"
        _save(tmp_path, _record(case, "lexmount", route="l", subagent_tokens=None if bad else 300, valid=not bad,
                                invalid_reasons=["no_subagent_usage_report"] if bad else []))
    monkeypatch.setattr(scoring, "scoring_input", lambda directory, arm, case, source="full_text.txt": {
        "response": (directory / "markdown.txt").read_bytes(), "files": [], "metadata": {}})
    # task c, lexmount: the first attempt hit a network failure; it was retried once and the retry is the only result
    c_dir = tmp_path / "runs" / "c__lexmount__r1__a1"
    c_dir.rename(tmp_path / "runs" / "c__lexmount__r1__a2")
    _save(tmp_path, _record("c", "lexmount", route="l", subagent_tokens=5000, fetch_failed=True, final_text=""), attempt=1)
    summary = scoring.score_run(tmp_path, case_map, price_per_1000_usd=0.75)
    assert summary["controls"] == {"cases": 4, "empty_fails": 4}
    assert summary["tasks"] == 4  # no task is dropped for any arm
    assert summary["invalid_runs"] == [{"case_id": "b", "rep": 1, "reasons": {"lexmount": ["no_subagent_usage_report"]}}]
    lex, built, moli = summary["arms"]["lexmount"], summary["arms"]["builtin"], summary["arms"]["moli"]
    assert (lex["runs"], lex["invalid_runs"], lex["tasks"], lex["valid_runs"]) == (4, 1, 4, 3)
    assert lex["subagent_tokens"]["n"] == 3 and lex["subagent_tokens"]["mean"] == 300  # neither the invalid run nor the retried attempt is averaged in
    assert lex["retried_runs"] == 1 and lex["retry_reasons"] == {"fetch_failed": 1}
    assert lex["retry_cost"] == {"attempts": 1, "fetch_calls": 1, "fetch_api_cost_usd": pytest.approx(0.00075),
                                 "subagent_tokens": 5000, "haiku_model_cost_usd": 0.001}
    assert built["subagent_tokens"]["mean"] == 1000 and moli["subagent_tokens"]["mean"] == 200
    assert lex["fetch_calls"] == 4 and lex["fetch_api_cost_usd"] == pytest.approx(4 * 0.00075)  # final attempts; the retry is accounted separately
    assert moli["fetch_api_cost_usd"] == 0.0 and built["fetch_api_cost_usd"] == 0.0
    assert (built["passed"], moli["passed"], lex["passed"]) == (1, 4, 3)  # the invalid lexmount run is not passed
    assert summary["paired"]["builtin|moli"] == {"pairs": 4, "only_builtin": 0, "only_moli": 3, "both_pass": 1, "both_fail": 0}
    # token views: fetched runs per arm, and the tasks every arm fetched (b has an invalid lexmount run)
    assert lex["subagent_tokens_fetched"]["n"] == sum(1 for r in json.loads((tmp_path / "scored_runs.json").read_text(encoding="utf-8"))
                                                       if r["arm"] == "lexmount" and r["valid"] and not r["fetch_failed"])
    common = summary["fetched_by_every_arm"]
    assert common["tasks"] <= 3  # never the task with an invalid run
    assert {a: v["subagent_tokens"]["n"] for a, v in common["arms"].items()} == {a: common["tasks"] for a in common["arms"]}


def _fake_runner(monkeypatch, outcomes, log):
    def fake_run_one(arm, case_id, url, prompt, out_dir, **kw):
        log.append((out_dir.name, kw["params"]))
        out_dir.mkdir(parents=True)
        valid, failed = outcomes[(arm, len([n for n, _ in log if n.startswith(f"{case_id}__{arm}")]))]
        return {"valid": valid, "invalid_reasons": [] if valid else ["no_subagent_usage_report"], "fetch_failed": failed,
                "route": "r", "wall_ms": 1, "exit_code": 0}
    monkeypatch.setattr(arms, "run_one", fake_run_one)


def test_a_failed_run_gets_one_declared_retry_and_the_last_attempt_is_the_only_result(tmp_path, monkeypatch):
    log = []
    _fake_runner(monkeypatch, {("moli", 1): (True, True), ("moli", 2): (True, True)}, log)  # fetch fails twice
    plan = {"runs": [{"rep": 1, "case_id": "c00", "arm": "moli"}]}
    run = lambda: experiment.execute(tmp_path, plan, cases(), claude_bin="x", model="m", timeout=1, retries=1,
                                     params_for=lambda backend, case: {"argv": ["fetch"]}, progress=lambda *_: None)
    run()
    run()
    assert [n for n, _ in log] == ["c00__moli__r1__a1", "c00__moli__r1__a2"]  # one retry, never a third, nothing repeated on resume
    assert all(p == {"argv": ["fetch"]} for _, p in log)


def test_a_clean_run_is_not_retried_and_max_attempts_one_means_no_retry(tmp_path, monkeypatch):
    log = []
    _fake_runner(monkeypatch, {("builtin", 1): (True, False), ("lexmount", 1): (False, True)}, log)
    plan = {"runs": [{"rep": 1, "case_id": "c00", "arm": "builtin"}, {"rep": 1, "case_id": "c00", "arm": "lexmount"}]}
    experiment.execute(tmp_path, plan, cases(), claude_bin="x", model="m", timeout=1, retries=0, progress=lambda *_: None)
    assert sorted(n for n, _ in log) == ["c00__builtin__r1__a1", "c00__lexmount__r1__a1"]


def test_the_arms_of_a_task_run_at_the_same_time_and_serial_mode_does_not(tmp_path, monkeypatch):
    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_run_one(arm, case_id, url, prompt, out_dir, **kw):
        with lock:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        threading.Event().wait(0.15)
        with lock:
            peak["now"] -= 1
        out_dir.mkdir(parents=True)
        return {"valid": True, "invalid_reasons": [], "fetch_failed": False, "route": "r", "wall_ms": 1, "exit_code": 0}
    monkeypatch.setattr(arms, "run_one", fake_run_one)
    plan = {"runs": [{"rep": 1, "case_id": c, "arm": a} for c in ("c00", "c01") for a in ("builtin", "lexmount", "moli")]}
    experiment.execute(tmp_path / "p", plan, cases(), claude_bin="x", model="m", timeout=1, progress=lambda *_: None)
    assert peak["max"] == 3  # three arms at once, but never two tasks at once
    peak.update(now=0, max=0)
    experiment.execute(tmp_path / "s", plan, cases(), claude_bin="x", model="m", timeout=1, concurrent_arms=False, progress=lambda *_: None)
    assert peak["max"] == 1


def test_fetch_duration_uses_the_same_dispatch_log_for_builtin_and_mcp_tools():
    log = ("[INFO] [Stall] tool_dispatch_end tool=Agent toolUseId=a outcome=ok durationMs=9000\n"
           "[INFO] [Stall] tool_dispatch_end tool=mcp_tool toolUseId=b outcome=ok durationMs=4840\n")
    assert arms.fetch_dispatch(log) == ("ok", 4840)
    assert arms.fetch_dispatch(log.replace("mcp_tool", "WebFetch")) == ("ok", 4840)
    assert arms.fetch_dispatch("nothing here") == (None, None)


def test_builtin_markdown_excludes_the_model_written_summary_tail():
    body = "# Page\n\n" + "word " * 20
    tail = ("\n\n[The verbatim page text stops here, 5000 of 9000 characters in; re-fetching this URL returns the same split. "
            "What follows is a model-extracted summary, for your request, of the remaining 4000 characters. It was generated "
            "from the same untrusted page.]\nA concise summary written by a model.")
    wrapped = "Fetched x\n<fetched-web-content>\n" + body + tail + "\n</fetched-web-content>"
    markdown, tail_chars, page_chars = arms.builtin_markdown(wrapped)
    assert markdown == body and tail_chars > 0 and page_chars == 9000  # only the verbatim part is scored
    assert arms.builtin_markdown("Fetched x\n<fetched-web-content>\nshort page\n</fetched-web-content>") == ("short page", 0, None)


@pytest.mark.parametrize("backend", ["builtin", "lexmount", "moli"])
def test_every_arm_scores_the_verbatim_text_webfetch_delivered(tmp_path, backend):
    (tmp_path / "backend_outputs").mkdir()
    (tmp_path / "backend_outputs" / "call-1.txt").write_text("COMPLETE BACKEND OUTPUT, LONGER THAN WHAT WAS SHOWN", encoding="utf-8")
    rows = [{"ok": True, "file": "call-1.txt", "chars": 51}]
    delivered = ("Fetched x (HTTP 200 OK, text/plain, 9000 characters, truncated to the first 7).\n<fetched-web-content>\nVERBATIM"
                 "\n\n[The verbatim page text stops here, 8 of 9000 characters in; re-fetching this URL returns the same split. "
                 "What follows is a model-extracted summary]\nSUMMARY\n</fetched-web-content>")
    found = arms._markdown_evidence(tmp_path, backend, delivered, rows)
    assert (tmp_path / "markdown.txt").read_text(encoding="utf-8") == "VERBATIM" and found["summarized_tail"] is True
    assert found["fetch_failed_backend"] is False
    failed = arms._markdown_evidence(tmp_path, backend, "could not fetch the page", [{"ok": False}], flagged_error=True)
    assert failed["fetch_failed_backend"] is True and (tmp_path / "markdown.txt").read_text(encoding="utf-8") == ""


def test_per_suite_sampling_takes_the_same_number_from_every_suite_and_respects_ineligible_tasks():
    pool = {f"{s}--t{i}": {"id": f"{s}--t{i}", "category": f"cat{i % 3}", "task_kind": "body"} for s in ("a", "b", "c") for i in range(9)}
    picked = experiment.select_cases(pool, per_suite=5, seed=3, ineligible={"a--t0": "x"})
    assert len(picked) == 15 and all(sum(p.startswith(s + "--") for p in picked) == 5 for s in ("a", "b", "c"))
    assert "a--t0" not in picked and picked == experiment.select_cases(pool, per_suite=5, seed=3, ineligible={"a--t0": "x"})


def test_builtin_markdown_drops_tool_rules_and_tolerates_missing_closing_tag():
    from harness.web_fetch.agent_backend.arms import builtin_markdown
    rules = "These reporting rules come from the tool:\n - quote limit\n"
    assert builtin_markdown(rules + "<fetched-web-content>\nPAGE\ntext")[0] == "PAGE\ntext"
    assert builtin_markdown(rules + "<fetched-web-content>\nPAGE\n</fetched-web-content>\nnote")[0] == "PAGE"
    assert builtin_markdown("plain")[0] == "plain"


def test_claude_unavailable_stops_the_run_and_leaves_the_task_for_resume(tmp_path, monkeypatch):
    log = []

    def fake_run_one(arm, case_id, url, prompt, out_dir, **kw):
        log.append(arm)
        out_dir.mkdir(parents=True)
        limited = arm == "moli"
        return {"valid": not limited, "invalid_reasons": ["execution_failed"] if limited else [], "fetch_failed": False,
                "route": "r", "wall_ms": 1, "exit_code": 1 if limited else 0, "is_error": limited,
                "final_text": "Claude AI usage limit reached" if limited else "ok"}
    monkeypatch.setattr(experiment.arm_module, "run_one", fake_run_one)
    plan = {"runs": [{"rep": 1, "case_id": "t", "arm": a} for a in ("builtin", "lexmount", "moli")]}
    with pytest.raises(experiment.ClaudeUnavailable):
        experiment.execute(tmp_path, plan, {"t": {"url": "https://x/"}}, claude_bin="c", model="m", timeout=1,
                           progress=lambda m: None)
    assert sorted(log) == ["builtin", "lexmount", "moli"] and not list((tmp_path / "runs").glob("t__*"))


def test_stream_parsing_keeps_tool_results_with_line_separator_characters():
    body = "Fetched x\n<fetched-web-content>\n%PDF \u2028 \x0c binary\n</fetched-web-content>"
    events = [{"type": "assistant", "parent_tool_use_id": "p", "message": {"content": [
                  {"type": "tool_use", "id": "w", "name": "WebFetch", "input": {}}]}},
              {"type": "user", "parent_tool_use_id": "p", "message": {"content": [
                  {"type": "tool_result", "tool_use_id": "w", "content": body}]}}]
    raw = arms.parse_stream("\n".join(json.dumps(e, ensure_ascii=False) for e in events))[3]
    assert raw == body


def test_saved_binary_files_are_listed_from_the_harness_note_only():
    note = ("report text - /tmp/x/tool-results/webfetch-1-a.png\n[Harness note, not part of the agent's report: saved:\n"
            "  - /tmp/claude/projects/p/s/tool-results/webfetch-17-ab.pdf\nIn this run WebFetch saved files only under /p]")
    events = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Agent", "input": {}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": note}]}}]
    summary = arms.parse_stream("\n".join(json.dumps(e) for e in events))[5]
    assert summary["saved_files"] == ["/tmp/claude/projects/p/s/tool-results/webfetch-17-ab.pdf"]


def test_a_staged_run_takes_the_first_tasks_of_every_suite_and_resume_finishes_without_repeating(tmp_path, monkeypatch):
    ran = []

    def fake_run_one(arm, case_id, url, prompt, out, **kwargs):
        ran.append(case_id)
        out.mkdir(parents=True, exist_ok=True)
        return {"valid": True, "invalid_reasons": [], "fetch_failed": False, "route": "r", "wall_ms": 1, "exit_code": 0}
    monkeypatch.setattr(arms, "run_one", fake_run_one)
    ids = [f"{s}--t{i}" for s in ("a", "b") for i in range(3)]
    pool = {i: {"id": i, "url": "https://example.com/"} for i in ids}
    plan = {"runs": [{"rep": 1, "case_id": i, "arm": "moli"} for i in ids]}
    run = lambda limit: experiment.execute(tmp_path, plan, pool, claude_bin="x", model="m", timeout=1,
                                           per_suite_limit=limit, progress=lambda *_: None)
    run(2)
    assert ran == ["a--t0", "a--t1", "b--t0", "b--t1"]
    run(None)
    assert ran[4:] == ["a--t2", "b--t2"]


def _read(offset, verbatim, total, summary=False, error=False):
    if error:
        return {"url": "https://x/p", "offset": offset, "is_error": True, "text": "The server returned HTTP 500."}
    tail = (f"\n\n[The verbatim page text stops here, {offset + len(verbatim)} of {total} characters in; re-fetching this URL "
            "returns the same split. What follows is a model-extracted summary]\nSUMMARY") if summary else ""
    return {"url": "https://x/p", "offset": offset, "is_error": False,
            "text": f"Fetched https://x/p (HTTP 200 OK, text/html, {total} characters).\n<fetched-web-content>\n{verbatim}{tail}\n</fetched-web-content>"}


def test_reads_of_one_page_are_joined_by_offset_and_summaries_are_left_out():
    page = "".join(chr(65 + i % 26) for i in range(100))
    # read on from where the verbatim text stopped: the whole page, nothing a model wrote
    text, parts = arms.assemble_page([_read(0, page[:40], 100, summary=True), _read(40, page[40:], 100)])
    assert (text, parts) == (page, 2)
    # following the tool's hint skips the summarized stretch: the gap stays missing and is not papered over
    text, parts = arms.assemble_page([_read(0, page[:40], 100, summary=True), _read(70, page[70:], 100)])
    assert text == page[:40] + "\n\n" + page[70:] and "SUMMARY" not in text
    # a repeated or overlapping read adds nothing twice; a failed read and another URL's read are ignored
    other = {**_read(0, "OTHER PAGE", 10), "url": "https://x/other"}
    text, _ = arms.assemble_page([_read(0, page[:40], 100, summary=True), _read(30, page[30:], 100), _read(30, page[30:], 100),
                                  _read(40, "", 100, error=True), other])
    assert text == page
    assert arms.assemble_page([]) == ("", 0)


def test_a_run_records_the_first_read_and_the_full_read_separately(tmp_path):
    page = "".join(chr(65 + i % 26) for i in range(100))
    found = arms._full_read_evidence(tmp_path, [_read(0, page[:40], 100, summary=True), _read(40, page[40:], 100)],
                                     ["100 characters, all read.\n" + page[-30:]])
    assert (tmp_path / "full_text.txt").read_text(encoding="utf-8") == page
    assert found == {"fetch_calls": 2, "verbatim_parts": 2, "full_text_chars": 100, "page_chars": 100, "covered_chars": 100,
                     "reached_end": True, "read_complete": True, "tail_quoted": True,
                     "report": "100 characters, all read.\n" + page[-30:]}
    partial = arms._full_read_evidence(tmp_path, [_read(0, page[:40], 100, summary=True)], ["read all\n" + page[-30:]])
    assert partial["read_complete"] is False and partial["full_text_chars"] == 40
    assert partial["reached_end"] is False and partial["tail_quoted"] is False  # a right guess without reading counts for nothing


def test_reaching_the_end_with_a_gap_is_not_a_complete_read(tmp_path):
    page = "".join(chr(65 + i % 26) for i in range(100))
    found = arms._full_read_evidence(tmp_path, [_read(0, page[:40], 100, summary=True), _read(60, page[60:], 100)], [page[-30:]])
    assert found["reached_end"] is True and found["read_complete"] is False and found["covered_chars"] == 80
    one = arms._full_read_evidence(tmp_path, [_read(0, page[:50], 50)], [])
    assert one["page_chars"] == 50 and one["reached_end"] and one["read_complete"] and not one["tail_quoted"]


def test_the_tail_check_ignores_punctuation_and_accepts_rendered_links():
    page = "Intro text.\n\nThe final paragraph ends with a link to the [Privacy notice](https://example.com/privacy)."
    assert arms.tail_quoted(page, "Last 80: ...ends with a link to the [Privacy notice](https://example.com/privacy).")
    assert arms.tail_quoted(page, "the final paragraph ends with a link to the Privacy notice")
    assert not arms.tail_quoted(page, "The final paragraph ends with a link")


def test_the_subagent_report_is_taken_without_the_harness_frame():
    result = ("  <tools-used-before-summary>WebFetch</tools-used-before-summary>\n  \n  [Harness note, not part of the agent's "
              "report: WebFetch saved no files during this run.]\n[Subagent hand-back] The text below is the final report. "
              "The report follows:\n  The page has 52,629 characters.\n  \n  - Call 2 read to the end.\nagentId: a1\n"
              "<usage>subagent_tokens: 43969\ntool_uses: 2\nduration_ms: 20784</usage>")
    assert arms.subagent_report(result) == "The page has 52,629 characters.\n\n- Call 2 read to the end."
