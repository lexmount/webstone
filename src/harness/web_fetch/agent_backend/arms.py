"""Run one fetch task through Claude Code under one experiment arm.

Arms are data. Three backends are registered:

* ``builtin``: stock Claude Code. WebFetch gets the page itself.
* ``lexmount``: WebFetch gets the page through Lexmount ``/v1/extract``, served by the stdlib MCP server in
  ``fetch_mcp.py``.
* ``moli``: WebFetch gets the page through the local Moli CLI, served by the same MCP server.

Every arm runs the same Claude Code chain: the main agent delegates to the stock ``web-fetch`` subagent, which
calls the stock WebFetch tool. Claude Code's ``CLAUDE_CODE_WEBFETCH_MCP_TOOL`` setting makes WebFetch retrieve the
page through a named MCP tool, which the model never sees; that retrieval is the only difference between arms.
Everything after it (WebFetch's wrapper, its verbatim window, the model summary of the rest, the subagent's report)
is Claude Code's own and identical in every arm. Add a backend by adding an entry and an MCP server branch.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

MCP_SERVER = pathlib.Path(__file__).with_name("fetch_mcp.py")
FETCH_TOOLS = {"WebFetch", "mcp__lexmount__web_fetch", "mcp__moli__web_fetch"}
LEXMOUNT_ENV = ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID", "LEXMOUNT_WEBFETCH_CLI")
DEBUG_END = re.compile(r"tool_dispatch_end tool=(\S+) toolUseId=\S+ outcome=(\S+) durationMs=(\d+)")
AGENT_USAGE = re.compile(r"<usage>subagent_tokens: (\d+)\s+tool_uses: (\d+)\s+duration_ms: (\d+)</usage>")
# Claude Code's note listing the original bytes WebFetch saved for binary content (a PDF, an image).
SAVED_FILE = re.compile(r"^[ \t]*- (/\S+/tool-results/webfetch-[^/\s]+)$", re.M)
# Identical for every arm. Claude Code hands page reading to the web-fetch subagent only while its web-fetch-agent
# mode is on, which otherwise follows a remotely served feature flag; the experiment pins it on. A background subagent
# gives no usage report, so background tasks are off.
FIXED_ENV = {"CLAUDE_CODE_WEB_FETCH_AGENT": "1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
NETWORK_COMMAND = re.compile(r"\b(curl|wget|httpx|urllib|requests\.get|http\.client|lynx|w3m)\b")

ARMS = {
    "builtin": {"backend": "builtin", "provider_type": None},
    "lexmount": {"backend": "lexmount", "provider_type": "lexmount"},
    "moli": {"backend": "moli", "provider_type": "moli_webfetch"},
}


def claude_version(claude_bin: str = "claude") -> str:
    try:
        done = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, timeout=30)
        return done.stdout.strip() or done.stderr.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return f"unavailable: {error}"


def _server(backend: str, log: pathlib.Path, params: pathlib.Path) -> dict:
    return {"command": sys.executable,
            "args": ["-I", str(MCP_SERVER), "--backend", backend, "--log", str(log), "--params", str(params),
                     "--out-dir", str(log.parent / "backend_outputs")]}


def build_command(arm: str, prompt: str, out: pathlib.Path, *, claude_bin: str, model: str) -> list[str]:
    """The same command for every arm; only the MCP server behind WebFetch differs (see ``arm_env``)."""
    backend = ARMS[arm]["backend"]
    servers = {} if backend == "builtin" else {
        backend: _server(backend, out / "backend_calls.jsonl", out / "backend_params.json")}
    (out / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return [claude_bin, "-p", prompt, "--model", model,
            "--output-format", "stream-json", "--verbose", "--debug-file", str(out / "debug.log"),
            # Isolation: no user CLAUDE.md or memory, no skills, no saved session, MCP only from this file.
            "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence",
            "--strict-mcp-config", "--mcp-config", str(out / "mcp.json"),
            # Claude Code's own chain: Agent for the main agent, WebFetch for its stock web-fetch subagent.
            "--tools", "Agent,WebFetch", "--allowedTools", "WebFetch"]


def arm_env(arm: str, params: dict | None) -> dict:
    """Environment that differs by arm: the MCP tool WebFetch retrieves pages through, and the fetch time limit."""
    backend = ARMS[arm]["backend"]
    env = {} if backend == "builtin" else {
        "CLAUDE_CODE_WEBFETCH_MCP_TOOL": json.dumps({"server": backend, "tool": "web_fetch"})}
    if (params or {}).get("timeout_seconds"):  # WebFetch's own deadline, the same limit in every arm
        env["CLAUDE_CODE_WEBFETCH_DEADLINE_MS"] = str(int(float(params["timeout_seconds"]) * 1000))
    return env


def parse_stream(text: str):
    """Return (init, result, tool_calls, raw_fetch_result, bash_commands, subagent) from stream-json output.

    ``subagent`` holds what Claude Code reports for the delegated subagent(s): the ``<usage>`` block that
    ends each Agent tool result (tokens, tool uses, duration). There is no substitute for a missing block;
    ``validate`` marks such a run invalid.
    """
    uses, calls, bash = {}, [], []
    reported = []
    fetch_error = False
    init = result = raw = None
    saved: list[str] = []
    reads: list[dict] = []  # every fetch result in order: the offset it was asked from and its text
    for line in text.split("\n"):  # JSON strings may hold U+2028 or form feeds, which splitlines() breaks on
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        if event.get("type") == "result":
            result = event
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        subagent = bool(event.get("parent_tool_use_id"))
        for block in content:
            if block.get("type") == "tool_use":
                uses[block["id"]] = block
                calls.append({"tool": block["name"], "sub": subagent})
                if block["name"] == "Bash":
                    bash.append(str((block.get("input") or {}).get("command", ""))[:300])
            elif (block.get("type") == "tool_result" and not subagent
                  and uses.get(block["tool_use_id"], {}).get("name") == "Agent"):
                text = block["content"] if isinstance(block["content"], str) else "".join(
                    x.get("text", "") for x in block["content"] if isinstance(x, dict))
                found = AGENT_USAGE.search(text)
                saved.extend(SAVED_FILE.findall(text.split("Harness note", 1)[1]) if "Harness note" in text else [])
                if found:
                    reported.append({"tokens": int(found.group(1)), "tool_uses": int(found.group(2)),
                                     "duration_ms": int(found.group(3))})
            elif block.get("type") == "tool_result" and uses.get(block["tool_use_id"], {}).get("name") in FETCH_TOOLS:
                body = block["content"]
                text_ = body if isinstance(body, str) else "".join(x.get("text", "") for x in body if isinstance(x, dict))
                call = uses[block["tool_use_id"]].get("input") or {}
                reads.append({"url": call.get("url"), "offset": int(call.get("offset") or 0),
                              "is_error": bool(block.get("is_error")), "text": text_})
                if raw is None:  # the first fetch result is the page the task asked for
                    fetch_error, raw = bool(block.get("is_error")), text_
    summary = {"agent_launches": sum(1 for c in calls if c["tool"] == "Agent" and not c["sub"]), "reported": reported,
               "fetch_error": fetch_error, "saved_files": saved, "reads": reads}
    return init, result, calls, raw, bash, summary


def fetch_dispatch(debug_text: str):
    """Return (outcome, duration_ms) of the fetch tool call from Claude Code's debug log.

    Every arm calls WebFetch, so every arm has the same measurement point, the tool dispatch duration.
    """
    for match in DEBUG_END.finditer(debug_text):  # the first fetch call, the one the scored result comes from
        if match.group(1) in FETCH_TOOLS | {"mcp_tool"}:
            return match.group(2), int(match.group(3))
    return None, None


SUMMARY_MARKER = re.compile(r"\n*\[The verbatim page text stops here, (\d+) of (\d+) characters in;")
WRAPPER = re.compile(r"^.*?<fetched-web-content>\n?(.*?)(?:</fetched-web-content>.*)?$", re.S)  # the tool's rules precede the tag; the closing tag may be absent


def builtin_markdown(raw: str) -> tuple[str, int, int | None]:
    """Return (verbatim markdown, characters of model-written summary, page length the tool reported).

    Above its size limit the built-in tool returns the first part of the page verbatim and then a summary of the
    rest written by a model, announced by a fixed line. The summary is not page content and is never scored.
    """
    match = WRAPPER.search(raw)
    inner = match.group(1) if match else raw
    marker = SUMMARY_MARKER.search(inner)
    if not marker:  # WebFetch puts one line break between the page text and the closing tag
        return (inner[:-1] if match and inner.endswith("\n") else inner), 0, None
    return inner[:marker.start()], len(inner) - marker.start(), int(marker.group(2))


def assemble_page(reads: list[dict]) -> tuple[str, int]:
    """Join the verbatim parts of every read of the first URL by their offsets. Returns (text, parts used).

    WebFetch reads on through a long page with ``offset``, a character position in the same cached page text, so the
    verbatim parts of several calls are slices of one text. Model-written summaries are never included. A gap that no
    call read verbatim stays missing: the parts on either side are joined with a blank line.
    """
    if not reads:
        return "", 0
    parts = {}
    for read in reads:
        if read["url"] != reads[0]["url"] or fetch_failed(read["text"], read["is_error"]):
            continue
        verbatim = builtin_markdown(read["text"])[0]
        if verbatim and len(verbatim) > len(parts.get(read["offset"], "")):
            parts[read["offset"]] = verbatim
    text, end = "", 0
    for offset in sorted(parts):
        piece = parts[offset]
        if offset < end:  # overlaps what is already assembled
            piece = piece[end - offset:]
        elif offset > end and text:
            text += "\n\n"
        text += piece
        end = max(end, offset + len(parts[offset]))
    return text, len(parts)


def classify_route(calls: list[dict], bash: list[str]) -> str:
    """Name the route the model actually took for this request."""
    fetch = [c for c in calls if c["tool"] in FETCH_TOOLS]
    if any(c["sub"] for c in fetch):
        return "delegated_" + next(c["tool"] for c in fetch if c["sub"])
    if fetch:
        return "direct_" + fetch[0]["tool"]
    if any(NETWORK_COMMAND.search(command) for command in bash):
        return "bash_network"
    if any(c["tool"] == "WebSearch" for c in calls):
        return "websearch"
    return "none"


def fetch_failed(raw: str | None, tool_flagged_error: bool) -> bool:
    """Did the fetch tool call itself fail (network, timeout, HTTP error, refusal)? Same rule for every arm.

    This is the only condition, besides a protocol violation, that earns the single declared retry.
    """
    text = raw or ""
    return (tool_flagged_error or not text or text.startswith("Fetch failed")
            or text.startswith("The server returned HTTP") or "is unable to fetch from" in text[:200])


def _markdown_evidence(out: pathlib.Path, backend: str, raw: str | None, rows: list[dict], flagged_error: bool = False) -> dict:
    """Write ``markdown.txt``, the content that is scored: the same rule in every arm.

    It is the verbatim part of WebFetch's result, the page text the subagent received; a model-written summary of
    the rest of a long page is excluded and counted. In the MCP arms the backend's complete output is also kept
    (``backend_outputs``) as diagnostic evidence; it is not scored.
    """
    failed = fetch_failed(raw, flagged_error)
    markdown, tail, page_chars = ("", 0, None) if failed else builtin_markdown(raw or "")
    good = [r for r in rows if r.get("ok")]
    evidence = {"summary_tail_chars": tail, "page_chars_reported": page_chars, "summarized_tail": bool(tail),
                "backend_output_chars": good[-1].get("chars") if good else None}
    (out / "markdown.txt").write_text(markdown, encoding="utf-8")
    return {"markdown_chars": len(markdown), "fetch_failed_backend": failed, **evidence}


def _full_read_evidence(out: pathlib.Path, reads: list[dict], first_page_chars: int | None, first_chars: int) -> dict:
    """Write ``full_text.txt``: the verbatim text of every read joined by offset, and say how much of the page it is."""
    text, parts = assemble_page(reads)
    (out / "full_text.txt").write_text(text, encoding="utf-8")
    total = first_page_chars or first_chars  # WebFetch states the page length only when it cuts the page
    return {"fetch_calls": len(reads), "verbatim_parts": parts, "full_text_chars": len(text),
            "read_complete": bool(text) and len(text) >= total}


def validate(record: dict, arm: str) -> list[str]:
    """Protocol conformance of one run. A run that fails any check is invalid, never repaired.

    The comparison is only fair when every run follows the same chain: exactly one subagent launch that
    Claude Code reports usage for, and that subagent called the arm's fetch tool.
    """
    reasons = []
    if record["exit_code"] != 0 or record["timed_out"] or record.get("is_error"):
        reasons.append("execution_failed")
    if record["agent_launches"] != 1:
        reasons.append(f"agent_launches={record['agent_launches']}")
    if record["subagent_tokens"] is None:
        reasons.append("no_subagent_usage_report")
    if record["route"] != "delegated_WebFetch":
        reasons.append(f"route={record['route']}")
    if record["fetch_tool_ms"] is None:
        reasons.append("no_fetch_dispatch_record")
    return reasons


def run_one(arm: str, case_id: str, url: str, prompt: str, out_dir, *, claude_bin: str = "claude",
            model: str = "claude-sonnet-5-5", timeout: int = 420, params: dict | None = None,
            extra_env: dict | None = None) -> dict:
    """Execute one arm on one task and persist ``record.json`` plus raw evidence in ``out_dir``."""
    out = pathlib.Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)  # empty working directory: no project instructions leak in
    (out / "backend_params.json").write_text(json.dumps(params or {}), encoding="utf-8")
    env = {**os.environ, **FIXED_ENV, **(extra_env or {})}
    env.update(arm_env(arm, params))
    if ARMS[arm]["backend"] == "lexmount":
        missing = [name for name in LEXMOUNT_ENV if not env.get(name)]
        if missing:
            raise RuntimeError("missing environment variables for the Lexmount backend: " + ", ".join(missing))
    if ARMS[arm]["backend"] == "moli" and not shutil.which(env.get("MOLI_BINARY", "moli"), path=env.get("PATH")):
        raise RuntimeError("the moli backend needs MOLI_BINARY (or moli on PATH) to be an executable")
    command = build_command(arm, prompt, out, claude_bin=claude_bin, model=model)
    started, timed_out = time.time(), False
    try:
        done = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
        stdout, stderr, code = done.stdout, done.stderr, done.returncode
    except subprocess.TimeoutExpired as error:
        timed_out, code = True, None
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
    wall_ms = round((time.time() - started) * 1000)
    (out / "stream.jsonl").write_text(stdout, encoding="utf-8")
    (out / "stderr.txt").write_text(stderr, encoding="utf-8")
    init, result, calls, raw, bash, delegated = parse_stream(stdout)
    (out / "raw_tool_result.txt").write_text(raw or "", encoding="utf-8")
    saved_files = []
    for source in delegated["saved_files"]:  # copy the delivered bytes into the run's evidence
        path = pathlib.Path(source)
        if path.is_file():
            (out / "saved_files").mkdir(exist_ok=True)
            shutil.copyfile(path, out / "saved_files" / path.name)
            saved_files.append({"file": path.name, "bytes": path.stat().st_size,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    debug = out / "debug.log"
    tool_outcome, tool_ms = fetch_dispatch(debug.read_text(errors="replace", encoding="utf-8")) if debug.exists() else (None, None)
    usage = (result or {}).get("usage") or {}
    models = (result or {}).get("modelUsage") or {}
    haiku = sum(v.get("costUSD") or 0 for k, v in models.items() if "haiku" in k)
    reported = delegated["reported"]
    record = {
        # What the delegated subagent consumed (the quantity under test), exactly as Claude Code reports it.
        "subagent_tokens": sum(r["tokens"] for r in reported) if reported else None,
        "subagent_duration_ms": sum(r["duration_ms"] for r in reported) if reported else None,
        "subagent_tool_uses": sum(r["tool_uses"] for r in reported) if reported else None,
        "agent_launches": delegated["agent_launches"],
        "haiku_model_cost_usd": haiku,  # includes small auxiliary calls made by the main session
        "arm": arm, "case_id": case_id, "url": url, "model": model,
        "claude_code_version": (init or {}).get("claude_code_version"),
        "exit_code": code, "timed_out": timed_out, "wall_ms": wall_ms,
        "duration_ms": (result or {}).get("duration_ms"), "duration_api_ms": (result or {}).get("duration_api_ms"),
        "num_turns": (result or {}).get("num_turns"), "is_error": (result or {}).get("is_error"),
        "llm_cost_usd": (result or {}).get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "model_usage": (result or {}).get("modelUsage"),
        "fetch_tool_ms": tool_ms, "fetch_tool_outcome": tool_outcome,
        "route": classify_route(calls, bash), "bash_commands": bash, "tool_calls": calls,
        "raw_tool_result_chars": len(raw or ""), "saved_files": saved_files, "final_text": (result or {}).get("result"),
    }
    log = out / "backend_calls.jsonl"
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").split("\n") if x.strip()] if log.exists() else []
    record.update(_markdown_evidence(out, ARMS[arm]["backend"], raw, rows, delegated["fetch_error"]))
    record.update(_full_read_evidence(out, delegated["reads"], record["page_chars_reported"], record["markdown_chars"]))
    record["backend_calls"] = len(rows)
    record["backend_wall_ms"] = sum(row["wall_ms"] for row in rows)
    record["fetch_failed"] = record["fetch_failed_backend"]  # WebFetch reported a failure or delivered nothing
    record["invalid_reasons"] = validate(record, arm)
    record["valid"] = not record["invalid_reasons"]
    (out / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record
