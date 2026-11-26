"""Capture and inspect Codex native subagent deliveries without fetching.

The capture functions retain the execution boundary used by the frozen native
single-fetch run (source SHA256
 d6d198f453bb02ab43413b67355e3885047d63eebfac189103da14865b8abfdb).
Native collaboration tools dispatch the agents; this module never starts a CLI
conversation or substitutes a thread for a clean subagent.
"""

import base64
import hashlib
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

# Loaded execution-side so children do not transcribe capture infrastructure.
CAPTURE_SCRIPT = r"""const shellQuote = s => "'" + s.replaceAll("'", "'\\''") + "'";
async function checkedCommand(cmd) {
  const result = await tools.exec_command({cmd, workdir:executionRoot, max_output_tokens:2000});
  if (result.exit_code !== 0) throw new Error("Capture command failed: " + result.output);
  return result;
}
async function prepareCapture(directory, toolName, args) {
  const code = 'const fs=require("node:fs");const d=process.argv[1];'
    + 'fs.writeFileSync(d+"/.raw-response.partial","",{flag:"wx"});'
    + 'fs.writeFileSync(d+"/events.jsonl",process.argv[2]+"\\n",{flag:"wx"});';
  const event = JSON.stringify({
    sequence:1,tool:toolName,parameters:args,time:new Date().toISOString()
  });
  await checkedCommand("node -e " + shellQuote(code) + " "
    + shellQuote(directory) + " " + shellQuote(event));
}
async function persistCapture(nativeReturn, directory, fetchMs) {
  const serialized = JSON.stringify(nativeReturn);
  if (typeof serialized !== "string") throw new Error("Native return is not JSON-serializable");
  for (let offset=0; offset<serialized.length;) {
    let end=Math.min(offset+32768,serialized.length);
    if(end<serialized.length) {
      const last=serialized.charCodeAt(end-1);
      if(last>=0xD800 && last<=0xDBFF) end--;
    }
    await checkedCommand("printf %s " + shellQuote(serialized.slice(offset,end))
      + " >> " + shellQuote(directory+"/.raw-response.partial"));
    offset=end;
  }
  const receipt = await checkedCommand(
    "uv run --frozen --no-cache --no-sync python -m harness.web_fetch.native_subagent "
    + "--save-native-response "
    + shellQuote(directory) + (fetchMs === null ? "" : " --fetch-ms " + String(fetchMs)));
  notify(receipt.output);
}
async function captureNative(directory, toolName, args, acquireOrValue) {
  await prepareCapture(directory, toolName, args);
  let nativeReturn = acquireOrValue, fetchMs = null;
  if (typeof acquireOrValue === "function") {
    const started = Date.now();
    try { nativeReturn = await acquireOrValue(); }
    catch (error) { nativeReturn = {fetch_error: String(error)}; }
    fetchMs = Date.now() - started;
  }
  await persistCapture(nativeReturn, directory, fetchMs);
}
"""


def capture_event(path, event, **fields):
    row = {"event": event, "timestamp": datetime.now(UTC).isoformat(), **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def save_native_response(folder, *, fetch_ms=None):
    """Persist one execution-side native return; never fetch or print its body.

    The caller stages JSON.stringify(return_value) in .raw-response.partial.
    This boundary owns decoding, file blocks and the receipt so an agent need
    not implement encoding in the restricted JavaScript orchestration runtime.
    """
    folder = Path(folder).resolve(strict=True)
    staged = folder / ".raw-response.partial"
    if staged.is_symlink() or not staged.is_file():
        raise ValueError("Missing regular staged response")
    if fetch_ms is not None and (not math.isfinite(fetch_ms) or fetch_ms < 0):
        raise ValueError("Invalid fetch duration")
    started = time.monotonic()
    raw = staged.read_bytes()
    value = json.loads(raw)
    texts, files, blocks = [], [], []
    if isinstance(value, str):
        texts.append(value)
        blocks.append({"index": 0, "type": "text", "path": "answer.txt"})
    elif isinstance(value, dict):
        if isinstance(value.get("output"), str):
            texts.append(value["output"])
            blocks.append({"index": 0, "type": "text", "path": "answer.txt", "source": "output"})
        content = value.get("content", [])
        if not isinstance(content, list):
            raise ValueError("Native content must be an ordered list")
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                raise ValueError("Invalid native block")
            entry = {"index": index, "type": block.get("type")}
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
                entry["path"] = "answer.txt"
            else:
                resource = block.get("resource") if block.get("type") == "resource" else block
                if isinstance(resource, dict):
                    encoded = (
                        resource.get("blob")
                        if block.get("type") == "resource"
                        else resource.get("data")
                    )
                    if isinstance(encoded, str):
                        name = f"block-{index:04d}.bin"
                        files.append((name, base64.b64decode(encoded, validate=True)))
                        entry["path"] = name
                    elif block.get("type") == "resource" and isinstance(resource.get("text"), str):
                        name = f"block-{index:04d}.txt"
                        files.append((name, resource["text"].encode("utf-8")))
                        entry["path"] = name
            blocks.append(entry)
    else:
        raise ValueError("Unsupported native response envelope")

    # Claim only saving, not fetching. A second network action is still governed
    # by the declared collection policy; do not advertise this as a fetch guard.
    with (folder / ".native-save-claimed").open("xb"):
        pass
    artifacts = []
    outputs = [("raw-response.json", raw), ("answer.txt", "\n".join(texts).encode("utf-8")), *files]
    for name, body in outputs:
        with (folder / name).open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        artifacts.append(
            {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        )
    receipt = {
        "status": "saved",
        "saved_at": datetime.now(UTC).isoformat(),
        "path": "answer.txt",
        "artifacts": artifacts,
        "blocks": blocks,
        "fetch_ms": fetch_ms,
        "save_ms": round((time.monotonic() - started) * 1000, 3),
    }
    temporary = folder / ".receipt.partial"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, folder / "receipt.json")
    temporary.unlink()
    staged.unlink()
    return receipt


def dispatch_checks(arguments, *, task_name, message, model, reasoning_effort):
    """Compare a retained spawn_agent call with its frozen dispatch contract."""
    actual = arguments.get("message")
    encrypted = isinstance(actual, str) and actual.startswith("gAAAAA")
    return {
        "task_name_matches": arguments.get("task_name") == task_name,
        "fork_turns_none": arguments.get("fork_turns") == "none",
        "model_matches": arguments.get("model") == model,
        "effort_matches": arguments.get("reasoning_effort") == reasoning_effort,
        "exact_prompt_matches": None if encrypted else actual == message,
    }


def assess_protocol(calls):
    """Project the namespace-aware audit used by the native multiple-fetch run.

    Return API attempts and inventory probes separately. This inspects retained
    tool calls, not physical HTTP requests or arbitrary shell program behavior.
    """
    violations, probes = [], []
    for row in calls:
        payload = row.get("payload", row)
        name, namespace = payload.get("name", ""), payload.get("namespace", "")
        argument = payload.get("arguments", payload.get("input", ""))
        source = argument if isinstance(argument, str) else json.dumps(argument)
        item = {"call_id": payload.get("call_id"), "name": name, "namespace": namespace}
        if namespace == "mcp__cua_repl" or "cua_repl" in name:
            try:
                args = json.loads(source)
            except (ValueError, TypeError):
                args = {}
            code = args.get("code", "") if isinstance(args, dict) else ""
            if isinstance(code, str) and re.fullmatch(
                r"\s*(?:(?:let|const)\s+\w+\s*=\s*)?await\s+cua\.getState\(\s*\)\s*;?\s*",
                code,
            ):
                probes.append({**item, "type": "browser_inventory_probe", "code": code})
            else:
                violations.append({**item, "type": "browser_control_api_attempt", "code": code})
        elif re.search(r"tools\.mcp__cua_repl\w*\s*\(", source):
            violations.append({**item, "type": "nested_browser_control_api_attempt"})
        if re.search(r"tools\.mcp__lexmount__lexmount_fetch\s*\(", source) or name in {
            "lexmount_fetch",
            "mcp__lexmount__lexmount_fetch",
        }:
            violations.append({**item, "type": "lexmount_tool_attempt"})
    return violations, probes


def audit_single_open(events, url):
    """Verify the two retained open routes without double counting extensions.

    Shell commands and other tool routes need separate inspection. The result
    counts native open invocations rather than redirects or transport retries.
    """
    items = [
        event["payload"]["item"]
        for event in events
        if event.get("type") == "event_msg"
        and event.get("payload", {}).get("type") == "item_completed"
    ]
    mcps = [item for item in items if item.get("type") == "McpToolCall"]
    extensions = [
        item
        for item in items
        if item.get("type") == "Extension" and item.get("kind") == "web.search"
    ]
    expected = {"type": "openPage", "url": url}
    if mcps:
        if len(mcps) != 1:
            raise ValueError("Expected exactly one MCP invocation")
        call = mcps[0]
        args = call.get("arguments", {})
        if (
            call.get("server") != "codex_apps"
            or call.get("tool") != "search_service.web_run"
            or not isinstance(args, dict)
            or args.get("open") != [{"ref_id": url}]
            or set(args) - {"open", "response_length"}
        ):
            raise ValueError("Unexpected single-fetch route or arguments")
        # A same-URL extension may be another standalone open. Only an
        # explicit parent association establishes that it belongs to this MCP.
        if extensions and (
            not call.get("id") or any(item.get("parent_id") != call["id"] for item in extensions)
        ):
            raise ValueError("Ambiguous mixed MCP and native open events")
        route = "codex_apps.search_service.web_run"
    else:
        if len(extensions) != 1:
            raise ValueError("Expected exactly one native open")
        route = "web.open"
    if any(item.get("action") != expected for item in extensions):
        raise ValueError("Unexpected native web action")
    return {
        "route": route,
        "invocation_count": 1,
        "nested_extension_events": len(extensions) if mcps else 0,
    }


def trace_telemetry(events):
    """Project tool wall times and the terminal cumulative token receipt."""
    calls, outputs, usage = [], {}, None
    for line, event in enumerate(events, 1):
        payload = event.get("payload", {})
        kind = payload.get("type")
        if event.get("type") == "event_msg" and kind == "token_count":
            info = payload.get("info") or {}
            if info.get("total_token_usage") is not None:
                usage = info["total_token_usage"]
        if event.get("type") != "response_item":
            continue
        if kind in {"function_call_output", "custom_tool_call_output"}:
            outputs[payload.get("call_id")] = event["timestamp"]
        elif kind in {"function_call", "custom_tool_call"}:
            argument = payload.get("arguments", payload.get("input"))
            encoded = (
                argument if isinstance(argument, str) else json.dumps(argument, sort_keys=True)
            )
            calls.append(
                {
                    "source_line": line,
                    "timestamp": event["timestamp"],
                    "namespace": payload.get("namespace", ""),
                    "name": payload.get("name"),
                    "call_id": payload.get("call_id"),
                    "parameters_present": argument is not None,
                    "parameters_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                }
            )
    for call in calls:
        end = outputs.get(call["call_id"])
        call["response_timestamp"] = end
        call["wall_ms"] = (
            (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(call["timestamp"].replace("Z", "+00:00"))
            ).total_seconds()
            * 1000
            if end
            else None
        )
    return {"tool_calls": calls, "terminal_usage": usage, "physical_http_request_count": None}


def delivery_timing(*, started_at, saved_at=None, ended_at=None):
    """Keep delivery latency separate from terminal/cleanup latency.

    saved_at must come from a retained execution-side save receipt, never file
    mtime or a parent poll. Missing delivery is unavailable, even on termination.
    This does not estimate or subtract tokens from the actual usage receipt.
    """

    def timestamp(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timing timestamps require a timezone")
        return parsed

    start = timestamp(started_at)
    saved = timestamp(saved_at) if saved_at else None
    end = timestamp(ended_at) if ended_at else None
    if (saved and saved < start) or (end and end < start) or (saved and end and saved > end):
        raise ValueError("Inconsistent delivery lifecycle timestamps")
    return {
        "delivery_ms": (saved - start).total_seconds() * 1000 if saved else None,
        "terminal_ms": (end - start).total_seconds() * 1000 if end else None,
        "post_delivery_ms": (end - saved).total_seconds() * 1000 if saved and end else None,
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Save a staged native return without fetching")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--save-native-response", type=Path)
    action.add_argument("--capture-script", action="store_true")
    parser.add_argument("--fetch-ms", type=float)
    args = parser.parse_args(argv)
    if args.capture_script:
        print(CAPTURE_SCRIPT)
    else:
        print(json.dumps(save_native_response(args.save_native_response, fetch_ms=args.fetch_ms)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
