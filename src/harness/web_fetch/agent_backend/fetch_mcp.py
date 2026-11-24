#!/usr/bin/env python3
"""Stdio MCP server that exposes one fetch backend as a single tool, ``web_fetch(url)``.

``--backend lexmount``: Lexmount ``POST /v1/extract`` (LEXMOUNT_API_KEY, LEXMOUNT_PROJECT_ID, optional
LEXMOUNT_BASE_URL). ``--backend moli``: the local Moli CLI (MOLI_BINARY, default ``moli``). The agent supplies only the URL; every
other request parameter comes from ``--params`` (a JSON file the harness writes per task family).

Standard library only, so Claude Code can start it with any Python 3.11+ interpreter. Claude Code passes its
own environment to MCP servers, so no secret is written to disk. Every call is appended to ``--log`` as JSON
Lines: url, status (HTTP status or process exit code), ok, wall_ms, chars, sha256. With ``--out-dir`` the complete
output of each successful call is also saved, because Claude Code replaces a result above its size limit with an
error pointing to a file and the backend's own output is what the benchmark scores.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOOL = {
    "name": "web_fetch",
    "description": "Fetch a public web page by URL and return its main text content as Markdown.",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "The URL to fetch"}},
                    "required": ["url"]},
    # Claude Code saves a tool result above its size limit to a file and the main agent then launches a second subagent
    # to read it. The tool declares the largest limit so every backend's full output reaches the subagent inline.
    "_meta": {"anthropic/maxResultSizeChars": 500000},
}


def lexmount(url, timeout, params):
    """Return (text, status, request_id, error_message, artifacts) for one Lexmount extract call."""
    extract = {"url": url, **({"options": params["options"]} if params.get("options") else {})}
    request = urllib.request.Request(
        os.environ.get("LEXMOUNT_BASE_URL", "https://api.lexmount.com").rstrip("/") + "/v1/extract", method="POST",
        data=json.dumps({"extract": extract}).encode(),
        headers={"X-API-Key": os.environ.get("LEXMOUNT_API_KEY", ""), "X-Project-Id": os.environ.get("LEXMOUNT_PROJECT_ID", ""),
                 "content-type": "application/json"})
    status, payload = None, {}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, payload = response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            payload = json.loads(error.read())
        except ValueError:
            payload = {}
    except Exception as error:  # network failure: report it to the agent, keep the server alive
        payload = {"error": {"message": str(error)}}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    text = result.get("main_text") if isinstance(result.get("main_text"), str) else ""
    if text:  # the agent sees every content field the API returns; the scorer reads the saved payload
        images = [i for i in result.get("images") or [] if isinstance(i, dict) and i.get("src")]
        links = [l for l in result.get("links") or [] if isinstance(l, dict) and l.get("href")]
        if images:
            text += "\n\nImages:\n" + "\n".join(f"- ![{i.get('alt') or ''}]({i['src']})" for i in images)
        if links:
            text += "\n\nLinks:\n" + "\n".join(f"- [{l.get('text') or ''}]({l['href']})" for l in links)
    message = (payload.get("error") or {}).get("message") or "no content returned"
    return text, status, payload.get("request_id"), message, {"json": payload}


def moli(url, timeout, params):
    """Return (text, exit_code, None, error_message, artifacts) for one local Moli fetch with the harness-chosen arguments."""
    if not params.get("argv"):
        return "", -1, None, "no moli arguments were configured for this run", {}
    try:
        done = subprocess.run([os.environ.get("MOLI_BINARY", "moli"), *params["argv"], url],
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return "", -1, None, str(error), {}
    message = done.stderr.decode("utf-8", "replace").strip()[-300:] or "no content returned"
    try:
        return done.stdout.decode("utf-8"), done.returncode, None, message, {}
    except UnicodeDecodeError:  # original file bytes (for example an image): delivered as a file, described to the agent
        note = f"Fetched binary content: {len(done.stdout)} bytes, saved as a file by the fetch tool."
        return note, done.returncode, None, message, {"bin": done.stdout}


BACKENDS = {"lexmount": lexmount, "moli": moli}


def fetch(backend, url, params):
    """Return (text, is_error, log_row, artifacts). ``log_row["ok"]`` is the backend's own verdict, not the agent's view.

    ``params["timeout_seconds"]`` is the hard stop of the call, the same for every backend.
    """
    started = time.time()
    text, status, request_id, message, artifacts = BACKENDS[backend](url, float(params["timeout_seconds"]), params)
    ok = bool(text) and status in (0, 200)
    row = {"url": url, "status": status, "ok": ok, "wall_ms": round((time.time() - started) * 1000), "chars": len(text),
           "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if ok else None, "request_id": request_id}
    return (text if ok else f"Fetch failed (status {status}): {message}"), not ok, row, artifacts


def serve(args):
    params = json.loads(open(args.params, encoding="utf-8").read()) if args.params else {}
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        if not raw.strip():
            continue
        message = json.loads(raw)
        ident, method = message.get("id"), message.get("method")
        if method == "initialize":
            version = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
            send({"jsonrpc": "2.0", "id": ident, "result": {
                "protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": args.backend, "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            url = ((message.get("params") or {}).get("arguments") or {}).get("url", "")
            text, is_error, row, artifacts = fetch(args.backend, url, params)
            if args.out_dir:  # the backend's complete output, whatever Claude Code shows the agent
                os.makedirs(args.out_dir, exist_ok=True)
                stem = f"call-{sum(1 for _ in open(args.log, encoding='utf-8')) + 1 if os.path.exists(args.log) else 1}"
                if "json" in artifacts:  # the API response, kept for failed calls too
                    row["payload"] = stem + ".json"
                    with open(os.path.join(args.out_dir, row["payload"]), "w", encoding="utf-8") as handle:
                        json.dump(artifacts["json"], handle, ensure_ascii=False)
                if not is_error:
                    row["file"] = stem + ".txt"
                    with open(os.path.join(args.out_dir, row["file"]), "w", encoding="utf-8") as handle:
                        handle.write(text)
                    if "bin" in artifacts:
                        row["binary"] = stem + ".bin"
                        with open(os.path.join(args.out_dir, row["binary"]), "wb") as handle:
                            handle.write(artifacts["bin"])
            with open(args.log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            send({"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})
        elif ident is not None:
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=sorted(BACKENDS))
    parser.add_argument("--log", required=True)
    parser.add_argument("--params", required=True, help="JSON file with the harness-chosen request parameters for this run")
    parser.add_argument("--out-dir", help="directory that receives each successful call's complete output")
    serve(parser.parse_args())
