#!/usr/bin/env python3
"""Stdio MCP server that exposes one fetch backend as a single tool, ``web_fetch(url)``, for Claude Code's WebFetch.

Claude Code calls it from WebFetch when ``CLAUDE_CODE_WEBFETCH_MCP_TOOL`` names it; the model never sees the tool.
It returns the backend's text to WebFetch, which then treats it exactly like a page it fetched itself.

``--backend lexmount``: Lexmount ``POST /v1/extract`` (LEXMOUNT_API_KEY, LEXMOUNT_PROJECT_ID, optional
LEXMOUNT_BASE_URL). The text is Lexmount's own agent-facing Markdown: the API response rendered by the official
``webfetch-cli`` (LEXMOUNT_WEBFETCH_CLI), replayed to it locally, so the API is called once with the harness's
parameters. ``--backend moli``: the local Moli CLI (MOLI_BINARY, default ``moli``); its standard output, or a note
when the output is binary (the bytes are saved as a file, as WebFetch does for binary content). WebFetch supplies
only the URL; every other request parameter comes from ``--params`` (a JSON file the harness writes per task family).

Standard library only, so Claude Code can start it with any Python 3.11+ interpreter. Claude Code passes its
own environment to MCP servers, so no secret is written to disk. Every call is appended to ``--log`` as JSON
Lines: url, status (HTTP status or process exit code), ok, wall_ms, chars, sha256, and the saved files.
"""
import argparse
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BINARY_NOTE = "[Binary content ({size} bytes) was saved to a local file for the caller.]"

TOOL = {
    "name": "web_fetch",
    "description": "Fetch a public web page by URL and return its main text content as Markdown.",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "The URL to fetch"}},
                    "required": ["url"]},
}


def lexmount(url, timeout, params):
    """Return (text, status, request_id, error_message, artifacts) for one Lexmount extract call."""
    extract = {"url": url, **({"options": params["options"]} if params.get("options") else {})}
    request = urllib.request.Request(
        os.environ.get("LEXMOUNT_BASE_URL", "https://api.lexmount.com").rstrip("/") + "/v1/extract", method="POST",
        data=json.dumps({"extract": extract, **({"use_proxy": True} if params.get("use_proxy") else {})}).encode(),
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
    message = (payload.get("error") or {}).get("message") or "no content returned"
    text = ""
    if status == 200 and isinstance(result.get("main_text"), str) and result["main_text"]:
        text, render_error = official_markdown(payload, url)
        message = render_error or message
    return text, status, payload.get("request_id"), message, {"json": payload}


def official_markdown(payload, url):
    """Render an API response with Lexmount's official CLI by replaying it on a local address. Returns (text, error)."""
    cli = os.environ.get("LEXMOUNT_WEBFETCH_CLI")
    if not cli:
        return "", "LEXMOUNT_WEBFETCH_CLI is not set"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class Replay(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Replay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done = subprocess.run([cli, "extract", "--url", url], capture_output=True, timeout=60,
                              env={**os.environ, "LEXMOUNT_WEBFETCH_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
    except (OSError, subprocess.SubprocessError) as error:
        return "", f"webfetch-cli failed: {error}"
    finally:
        server.shutdown()
    if done.returncode != 0:
        return "", "webfetch-cli failed: " + done.stderr.decode("utf-8", "replace").strip()[-300:]
    return done.stdout.decode("utf-8"), None


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
    except UnicodeDecodeError:  # original file bytes (an image, a PDF): saved as a file, as WebFetch does
        return BINARY_NOTE.format(size=len(done.stdout)), done.returncode, None, message, {"bin": done.stdout}


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
            if args.out_dir:  # the backend's complete output, kept as evidence
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
