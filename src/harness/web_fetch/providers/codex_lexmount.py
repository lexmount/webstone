"""Lexmount Auto tool for the Codex CLI provider."""

import base64
import hashlib
import json
import re
import sys
import time
from http.client import HTTPException
from pathlib import Path

from harness.web_fetch.config import repository_env
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_projection import (
    native_answer_projection,
    project_native_answer,
    project_native_result_fields,
)
from harness.web_fetch.providers.http_client import ResponseTooLargeError, request

BUDGET = 200_000
MAX_RESPONSE_BYTES = 20_000_000
ENDPOINT = "https://api.lexmount.com/v1/extract"
CREDENTIALS = ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")
REPEAT_ERROR = "The case has already sent its Lexmount request"
TOOL = {
    "name": "lexmount_fetch",
    "description": "Fetch the assigned URL once with Lexmount Extract Auto.",
    "inputSchema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
}
PRAGMA = re.compile(r'^// @exec:\s*\{\s*"max_output_tokens"\s*:\s*200000\s*\}\r?\n')
TRUNCATION = re.compile(
    r"…[\d,]+ tokens truncated…|\[Code Mode session output limit reached:[^\]]*\]"
)


def call_source(url):
    return (
        f'// @exec: {{"max_output_tokens":{BUDGET}}}\n'
        f"const r = await tools.mcp__lexmount__lexmount_fetch({json.dumps({'url': url})}); text(r);"
    )


def _answer(text, error=False):
    return {"content": [{"type": "text", "text": text}], **({"isError": True} if error else {})}


def serve(directory):
    """Serve one assigned URL. Secrets live in the repository .env, outside MCP arguments."""
    case = json.loads((directory / "case.json").read_bytes())
    sent = False
    for line in sys.stdin:
        event = json.loads(line)
        if "id" not in event:
            continue
        method, params = event["method"], event.get("params", {})
        if method == "initialize":
            result = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "webstone-lexmount", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "ping":
            result = {}
        elif method != "tools/call":
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": event["id"],
                        "error": {"code": -32601, "message": "Unsupported method"},
                    }
                ),
                flush=True,
            )
            continue
        elif params.get("name") != TOOL["name"] or params.get("arguments") != {"url": case["url"]}:
            result = _answer("Unexpected tool or URL", error=True)
        elif sent or (directory / "dispatched").exists():
            result = _answer(REPEAT_ERROR, error=True)
        else:
            sent = True
            result = _fetch_once(directory, case)
        print(json.dumps({"jsonrpc": "2.0", "id": event["id"], "result": result}), flush=True)


def _fetch_once(directory, case):
    remaining = case["deadline"] - time.monotonic()
    if remaining <= 0:
        return _answer("Case deadline expired", error=True)
    credentials = repository_env()
    if any(not credentials.get(name) for name in CREDENTIALS):
        return _answer("Missing Lexmount credentials in the repository .env", error=True)
    try:
        with (directory / "dispatched").open("x"):
            pass
    except FileExistsError:
        return _answer(REPEAT_ERROR, error=True)
    try:
        response = request(
            ENDPOINT,
            method="POST",
            timeout=remaining,
            max_bytes=MAX_RESPONSE_BYTES,
            allow_redirects=False,
            headers={
                "X-API-Key": credentials[CREDENTIALS[0]],
                "X-Project-Id": credentials[CREDENTIALS[1]],
            },
            json_body={"extract": {"url": case["url"], "options": {"engine_preference": "auto"}}},
        )
    except ResponseTooLargeError as exc:
        (directory / "receipt.json").write_text(
            json.dumps({"status": exc.status, "response_limit_bytes": exc.max_bytes})
        )
        return _answer("Lexmount HTTP response exceeded the byte limit", error=True)
    except (OSError, HTTPException) as exc:
        cause = getattr(exc, "reason", exc)
        code = getattr(cause, "errno", None)
        failure = {"transport_error": type(cause).__name__, "transport_error_code": code}
        (directory / "receipt.json").write_text(json.dumps(failure))
        return _answer("Lexmount request ended before an HTTP response", error=True)
    raw = response.body
    secrets = [
        credentials[name].encode()
        for name in (*CREDENTIALS, "CODEX_API_KEY")
        if credentials.get(name)
    ]
    if any(secret in raw for secret in secrets):
        (directory / "receipt.json").write_text(
            json.dumps({"status": response.status, "credential_reflection": True})
        )
        return _answer("Unsafe credential reflection in Lexmount response", error=True)
    (directory / "response.bin").write_bytes(raw)
    (directory / "receipt.json").write_text(json.dumps({"status": response.status}))
    return _answer(raw.decode("utf-8", errors="replace"))


def _string_prefix(source):
    match = re.match(r'(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*', source)
    return json.loads('"' + match[0] + '"').encode("utf-8", "ignore").decode("utf-8")


def _object_prefix(source, *, result=False):
    """Read complete returned fields and a visible prefix of main_text."""
    remaining = source.lstrip()
    fields = {}
    if not remaining.startswith("{"):
        return fields
    remaining = remaining[1:].lstrip()
    decode = json.JSONDecoder().raw_decode
    while remaining and not remaining.startswith("}"):
        name = None
        try:
            name, end = decode(remaining)
            remaining = remaining[end:].lstrip()
            if not isinstance(name, str) or not remaining.startswith(":"):
                break
            remaining = remaining[1:].lstrip()
            if not result and name == "result":
                return {"result": _object_prefix(remaining, result=True)}
            value, end = decode(remaining)
        except ValueError:
            if result and name == "main_text" and remaining.startswith('"'):
                fields[name] = _string_prefix(remaining[1:])
            break
        remaining = remaining[end:].lstrip()
        if not remaining or remaining[0] not in ",}":
            break
        fields[name] = value
        if remaining[0] == "}":
            break
        remaining = remaining[1:].lstrip()
    return fields


def _delivered_payload(output, raw):
    """Validate the system tool output against the MCP response before task projection."""
    wire = raw.decode("utf-8", errors="replace")
    canonical = json.dumps(_answer(wire), ensure_ascii=False, separators=(",", ":"))
    prefix = '{"content":[{"type":"text","text":"'
    for block in output.get("output", []):
        blob = block.get("text", "")
        if blob.startswith("Warning: truncated output"):
            blob = blob.partition("\n\n")[2]
        if blob == canonical:
            return json.loads(wire), False
        marker = TRUNCATION.search(blob)
        if marker and blob.startswith(prefix) and canonical.startswith(blob[: marker.start()]):
            try:
                retained = blob[: marker.start()]
                trailing = blob[marker.end() :]
                if trailing and canonical.endswith(trailing):
                    payload = json.loads(json.loads(blob)["content"][0]["text"])
                    result = payload.get("result")
                    if isinstance(result, dict) and marker[0] in result.get("main_text", ""):
                        result["main_text"] = result["main_text"].replace(marker[0], "\n", 1)
                        return payload, True
            except (TypeError, ValueError, KeyError, IndexError):
                pass
            return _object_prefix(_string_prefix(retained[len(prefix) :])), True
    return {}, False


def parse_returns(case, events, items, directory):
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes()) if receipt_path.is_file() else {}
    raw_path = directory / "response.bin"
    raw = raw_path.read_bytes() if raw_path.is_file() else None
    status = receipt.get("status")
    mcp = [
        e["item"]
        for e in events
        if e.get("type") == "item.completed"
        and e.get("item", {}).get("type") in {"mcp_tool_call", "web_search"}
    ]
    calls = [i for i in items if i.get("type", "").endswith("_call")]
    outputs = {i.get("call_id"): i for i in items if i.get("type") == "custom_tool_call_output"}
    metadata = {"answer_format": "plain_text", "requested_engine": "auto", **receipt}

    def fail(kind, message):
        return ProviderResult(
            False, "", status_code=status, error_type=kind, error_message=message, metadata=metadata
        )

    if (
        not calls
        or not mcp
        or any(
            i.get("type") != "mcp_tool_call"
            or i.get("server") != "lexmount"
            or i.get("tool") != TOOL["name"]
            or i.get("arguments") != {"url": case.url}
            for i in mcp
        )
    ):
        return fail("codex_lexmount_violation", "Expected only the assigned Lexmount tool")
    first, *later = mcp
    primary = calls[0]
    pattern = re.compile(
        r"const\s+(\w+)\s*=\s*await\s+tools\.mcp__lexmount__lexmount_fetch\(\s*(\{.*\})\s*\)"
        r"\s*;\s*text\(\s*\1\s*\)\s*;?\s*",
        re.S,
    )
    for call in calls:
        js = call.get("input", "")
        match = pattern.fullmatch(PRAGMA.sub("", js, count=1).strip())
        try:
            args = json.loads(match[2], object_pairs_hook=_unique_object) if match else None
        except ValueError:
            args = None
        if (
            call.get("type") != "custom_tool_call"
            or call.get("name") != "exec"
            or args != {"url": case.url}
        ):
            return fail("codex_lexmount_violation", "Unexpected Codex tool source")
    if not PRAGMA.match(primary.get("input", "")):
        return fail("codex_lexmount_budget", "First tool call omitted the required output budget")
    if len(calls) != len(mcp) or any(not _blocked_repeat(i) for i in later):
        return fail("codex_lexmount_violation", "Unverified additional tool invocation")
    metadata["blocked_later_attempts"] = len(later)
    if "response_limit_bytes" in receipt:
        return fail("lexmount_response_too_large", "Lexmount HTTP response exceeded the byte limit")
    if raw is None or status is None:
        return fail("lexmount_transport_error", "Lexmount did not return an HTTP response")
    if not 200 <= status < 300:
        return fail("lexmount_http_error", f"Lexmount returned HTTP {status}")
    if first.get("status") != "completed":
        return fail("codex_lexmount_violation", "First Lexmount tool result did not complete")
    try:
        payload, truncated = _delivered_payload(outputs.get(primary.get("call_id"), {}), raw)
    except ValueError:
        payload, truncated = {}, False
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("result"), dict)
        or not payload["result"]
    ):
        return fail("codex_lexmount_empty_output", "No verifiable Lexmount result reached Codex")
    declaration = native_answer_projection("lexmount", case, payload, status)
    content, fields = project_native_answer(payload, declaration)
    # Bind scoring to the verified tool payload. The private diagnostic log
    # separately retains the original HTTP response for transport audits.
    witness = json.dumps(payload).encode() if truncated else raw
    metadata.update(
        fields,
        native_answer_projection=declaration,
        tool_output_truncated=truncated,
        _raw_response_b64=base64.b64encode(witness).decode(),
        raw_response_sha256=hashlib.sha256(witness).hexdigest(),
        raw_response_bytes=len(witness),
    )
    return ProviderResult(
        True,
        content,
        status_code=status,
        metadata=metadata,
        **project_native_result_fields(payload, declaration),
    )


def _unique_object(pairs):
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError("Duplicate key in tool argument")
    return values


def _blocked_repeat(item):
    result = item.get("result") or {}
    return any(block.get("text") == REPEAT_ERROR for block in result.get("content", []))


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
