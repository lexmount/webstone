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
    path.write_text(FAKE_CLAUDE.format(python=sys.executable))
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


@pytest.mark.parametrize("arm,expect_tools,expect_agents", [("builtin", "Agent,WebFetch", False), ("lexmount", "Agent", True), ("moli", "Agent", True)])
def test_arm_commands_differ_only_in_the_fetch_tool(tmp_path, arm, expect_tools, expect_agents):
    command = arms.build_command(arm, "p", tmp_path, claude_bin="claude", model="m")
    assert command[command.index("--tools") + 1] == expect_tools  # the main agent can only delegate
    assert ("--agents" in command) == expect_agents
    if expect_agents:
        agent = json.loads(command[command.index("--agents") + 1])["web-fetch"]
        assert agent["tools"] == [f"mcp__{arm}__web_fetch"] and agent["model"] == "haiku"
        assert agent["mcpServers"][0][arm]["args"][2:4] == ["--backend", arm]
    assert json.loads((tmp_path / "mcp.json").read_text())["mcpServers"] == {}  # main agent sees no MCP tools
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in command  # identical isolation in every arm
    assert ("--disallowedTools" in command) == (arm != "builtin")


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


def test_arm_with_a_different_fetch_tool_than_declared_is_invalid(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ROUTE", "delegated")
    monkeypatch.setenv("LEXMOUNT_API_KEY", "k")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "p")
    record = arms.run_one("lexmount", "c00", "https://example.com/", "p", tmp_path / "r", claude_bin=fake_claude)
    assert "route=delegated_WebFetch" in record["invalid_reasons"]  # built-in WebFetch must not serve this arm


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
    params_file.write_text(json.dumps(params or {}))
    out = subprocess.run([sys.executable, "-I", str(arms.MCP_SERVER), "--backend", backend, "--log", str(log), "--params", str(params_file)],
                         env={**os.environ, **env},
                         capture_output=True, text=True, input="\n".join(json.dumps(x) for x in lines) + "\n", timeout=30).stdout
    replies = {json.loads(l)["id"]: json.loads(l)["result"] for l in out.splitlines()}
    return replies, [json.loads(l) for l in log.read_text().splitlines()]


def test_lexmount_backend_speaks_mcp_and_reports_success_and_failure(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {"LEXMOUNT_API_KEY": "k", "LEXMOUNT_PROJECT_ID": "p", "LEXMOUNT_BASE_URL": f"http://127.0.0.1:{server.server_port}"}
    options = {"engine_preference": "lightmount_domstable", "timeout_ms": 30000}
    replies, rows = _mcp("lexmount", env, tmp_path, ["http://x/good", "http://x/bad"], params={"options": options})
    server.shutdown()
    assert _Stub.seen[0]["extract"] == {"url": "http://x/good", "options": options}  # harness-chosen options reach the API
    assert replies[2]["tools"][0]["name"] == "web_fetch"
    assert replies[3]["content"][0]["text"] == "# T\n\nstub markdown" and replies[3]["isError"] is False
    assert replies[4]["isError"] is True and "status 502" in replies[4]["content"][0]["text"]
    assert [r["status"] for r in rows] == [200, 502] and rows[0]["chars"] == len("# T\n\nstub markdown")


def test_moli_backend_runs_the_local_binary_with_the_harness_chosen_arguments_only(tmp_path):
    fake = tmp_path / "moli"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nif [ "$last" = "https://x/bad" ]; then echo broken >&2; exit 3; fi\necho "args: $*"\n')
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
    path.write_text('#!/bin/sh\necho "moli 1.1.27"\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _task(track, **extra):
    return {"id": "t", "name": "t", "url": "https://example.com/", "category": "c", "expected_any": [], "track": track, **extra}


def test_moli_parameters_follow_the_task_family_and_unconfigurable_tasks_are_reported(fake_moli):
    provider = backend_params.moli_provider(fake_moli)
    web = backend_params.params_for("moli", _task("web"), moli=provider)["argv"]
    dynamic = backend_params.params_for("moli", _task("dynamic", retrieval_hints={"version": 3, "mode": "native"}), moli=provider)["argv"]
    assert web[web.index("--wait-until") + 1] == "done" and "--layout" not in web
    assert dynamic[dynamic.index("--wait-until") + 1] == "domstable" and "--layout" in dynamic and "--with-frames" in dynamic
    with pytest.raises(ValueError, match="input_media_kind"):
        backend_params.params_for("moli", _task("document"), moli=provider)
    found = backend_params.ineligible({"a": _task("web"), "b": _task("document")}, ["builtin", "moli"], moli=provider)
    assert list(found) == ["b"] and found["b"].startswith("moli:")


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
                backend_params.to_case(task), backend_params.CALLER_BUDGET_SECONDS)
        mine = backend_params.params_for("lexmount", task)
        assert sent["body"]["extract"] == {"url": task["url"], **({"options": mine["options"]} if mine else {})}
    native = backend_params.params_for("lexmount", _task("dynamic", retrieval_hints={"version": 3, "mode": "native"}))
    assert native == {"options": {"engine_preference": "lightmount_domstable",
                                  "timeout_ms": int(provider_module.inner_timeout_budget(backend_params.CALLER_BUDGET_SECONDS) * 1000)}}
    assert backend_params.params_for("builtin", _task("web")) == {}


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
    (d / "raw_tool_result.txt").write_text(record["final_text"])  # the tests use final_text as the returned content
    (d / "record.json").write_text(json.dumps(record))


def test_one_chance_per_run_invalid_runs_exclude_the_task_from_every_arm_and_are_never_averaged(tmp_path, monkeypatch):
    case_map = {c: {"id": c, "task_kind": "body", "ground_truth_main_content": "ref"} for c in ("a", "b", "c", "bad")}
    monkeypatch.setattr(scoring, "score_attempt", lambda case, data, ok: {"id": case["id"], "text": data.decode(), "forbidden_requirement_pass": case["id"] != "bad"})
    monkeypatch.setattr(scoring, "task_passed", lambda ok, m, kind: m["text"] == "good" or (m["text"] == "ref" and m["id"] != "bad"))
    (tmp_path / "plan.json").write_text(json.dumps({"args": {}, "versions": {"claude_code": "v"}, "dataset": {"id": "d", "sha256": "0" * 64},
                                                    "cases": list(case_map), "runs": []}))
    for case in case_map:
        _save(tmp_path, _record(case, "builtin", final_text="good" if case == "a" else "no", subagent_tokens=1000))
        _save(tmp_path, _record(case, "moli", route="m", subagent_tokens=200))
        # lexmount broke the protocol on task b (usage report withheld)
        bad = case == "b"
        _save(tmp_path, _record(case, "lexmount", route="l", subagent_tokens=None if bad else 300, valid=not bad,
                                invalid_reasons=["no_subagent_usage_report"] if bad else []))
    # task c, lexmount: the first attempt hit a network failure; it was retried once and the retry is the only result
    c_dir = tmp_path / "runs" / "c__lexmount__r1__a1"
    c_dir.rename(tmp_path / "runs" / "c__lexmount__r1__a2")
    _save(tmp_path, _record("c", "lexmount", route="l", subagent_tokens=5000, fetch_failed=True, final_text=""), attempt=1)
    summary = scoring.score_run(tmp_path, case_map, price_per_1000_usd=0.75)
    assert summary["controls"]["unsatisfiable"] == ["bad"] and summary["controls"]["control_not_applicable"] == []
    assert summary["included_pairs"] == 2  # a and c; b is dropped for every arm, bad is unsatisfiable
    assert summary["excluded_pairs"] == [{"case_id": "b", "rep": 1, "reasons": {"lexmount": ["no_subagent_usage_report"]}}]
    lex, built, moli = summary["arms"]["lexmount"], summary["arms"]["builtin"], summary["arms"]["moli"]
    assert (lex["runs"], lex["invalid_runs"], lex["compared_runs"]) == (4, 1, 2) and built["compared_runs"] == moli["compared_runs"] == 2
    assert lex["subagent_tokens"]["n"] == 2 and lex["subagent_tokens"]["mean"] == 300  # neither the invalid run nor the retried attempt is averaged in
    assert lex["retried_runs"] == 1 and lex["retry_reasons"] == {"fetch_failed": 1}
    assert lex["retry_cost"] == {"attempts": 1, "fetch_calls": 1, "fetch_api_cost_usd": pytest.approx(0.00075),
                                 "subagent_tokens": 5000, "haiku_model_cost_usd": 0.001}
    assert built["subagent_tokens"]["mean"] == 1000 and moli["subagent_tokens"]["mean"] == 200
    assert lex["fetch_calls"] == 4 and lex["fetch_api_cost_usd"] == pytest.approx(4 * 0.00075)  # final attempts; the retry is accounted separately
    assert moli["fetch_api_cost_usd"] == 0.0 and built["fetch_api_cost_usd"] == 0.0
    assert (built["passed"], moli["passed"], lex["passed"]) == (1, 2, 2)
    assert summary["paired"]["builtin|moli"] == {"pairs": 2, "only_builtin": 0, "only_moli": 1, "both_pass": 1, "both_fail": 0}


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
