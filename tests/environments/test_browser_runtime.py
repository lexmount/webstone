"""Browser launch contracts independent of benchmark task or agent implementation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from harness.environments.browser import start_browser_container, wait_cdp


@pytest.mark.parametrize("cpus", [None, 1.5])
def test_launch_supports_an_additional_browser_with_explicit_resources(cpus):
    client = MagicMock()
    start_browser_container(
        client,
        engine="third-browser",
        image="example/browser@sha256:pinned",
        cdp_port=9333,
        run_id="test",
        cpus=cpus,
        memory="2g",
        network="isolated-test",
    )
    args, options = client.containers.run.call_args
    assert args == ("example/browser@sha256:pinned",)
    assert options["ports"] == {"9333/tcp": ("127.0.0.1", None)}
    assert options["mem_limit"] == "2g"
    assert options["network"] == "isolated-test"
    assert options["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert options["security_opt"] == ["no-new-privileges"]
    assert options.get("nano_cpus") == (None if cpus is None else 1_500_000_000)


def test_cdp_readiness_uses_the_published_address_and_preserves_websocket_path():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/json/version"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "webSocketDebuggerUrl": "ws://container.internal:9333/devtools/browser/id?token=test",
                    }
                ).encode()
            )

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{server.server_port}"
        assert wait_cdp(f"http://{host}", timeout_seconds=2) == (
            f"ws://{host}/devtools/browser/id?token=test"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cdp_timeout_includes_browser_container_logs():
    container = MagicMock()
    container.logs.return_value = b"browser could not start"
    with pytest.raises(RuntimeError, match="browser could not start"):
        wait_cdp("http://127.0.0.1:1", timeout_seconds=0, container=container)
    container.reload.assert_called_once()


def test_local_browser_replay_preserves_navigation_and_mutation_evidence(tmp_path):
    import argparse
    import os
    from pathlib import Path
    from types import MappingProxyType
    from urllib.parse import urlsplit

    from webarena_verified.types.tracing import NetworkEvent

    from harness.environments.webarena.har import CdpHarRecorder
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool
    from harness.trajectory_replay.remote_execution import start_local_browser, stop_browser

    binary = os.environ.get("WEBSTONE_TEST_BROWSER_BINARY")
    engine = os.environ.get("WEBSTONE_TEST_BROWSER_ENGINE")
    if not binary or not engine:
        pytest.skip("Set browser engine and binary for the local replay evidence smoke")
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(("GET", self.path, ""))
            body = (b'<a id="next" href="/next">Next</a>'
                    b'<form method="post" action="/save">'
                    b'<input id="name" name="name"><button id="save">Save</button></form>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            received.append(("POST", self.path, body))
            self.send_response(302)
            self.send_header("Location", "/saved")
            self.send_header("Set-Cookie", "mage-messages=saved; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    args = argparse.Namespace(engine=engine, binary=Path(binary), host_resolver_rules=None)
    process, endpoint = start_local_browser(args, str(tmp_path / "browser-profile"))
    tool = None
    try:
        tool = PlaywrightReplayTool(endpoint, engine=engine, navigation_wait_until="load")
        recorder = CdpHarRecorder(capture_transport=tool.har_capture_transport,
                                  capture_new_pages=tool._supports_multiple_pages,
                                  playwright_request_transform=tool.har_request_transform)
        recorder.start(tool._context, tool._page)
        for call in [
            {"action": "navigate", "url": f"http://127.0.0.1:{server.server_port}/"},
            {"action": "click", "selector": "#next"},
            {"action": "fill", "selector": "#name", "text": "Ada"},
            {"action": "click", "selector": "#save"},
        ]:
            result = tool.execute({**call, "timeout_ms": 5000})
            assert result.status == "success", result.result
        tool._page.wait_for_timeout(100)
        output = tmp_path / "network.har"
        recorder.stop(tool._context, output)
        entries = json.loads(output.read_text(encoding="utf-8"))["log"]["entries"]
        by_request = {(entry["request"]["method"], urlsplit(entry["request"]["url"]).path): entry
                      for entry in entries}
        assert ("GET", "/next", "") in received
        assert ("POST", "/save", "name=Ada") in received
        navigation = by_request[("GET", "/next")]
        assert navigation["response"]["status"] == 200
        assert NetworkEvent(data=MappingProxyType(navigation)).is_navigation_event
        saved = by_request[("POST", "/save")]
        assert saved["request"]["postData"]["text"] == "name=Ada"
        assert saved["response"]["status"] == 302
        assert saved["response"]["redirectURL"] == "/saved"
        assert saved["response"]["cookies"] == [{"name": "mage-messages", "value": "saved"}]
        assert by_request[("GET", "/saved")]["response"]["status"] == 200
        assert not recorder.errors
    finally:
        if tool is not None:
            tool.close()
        stop_browser(process)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_single_session_replay_keeps_mutation_request_and_response(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness.environments.webarena.har import CdpHarRecorder
    from harness.trajectory_replay import playwright_tool

    page, context, browser, runtime = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    handlers = {}
    page.on.side_effect = lambda event, callback: handlers.__setitem__(event, callback)
    context.new_page.return_value = page
    context.pages = [page]
    browser.new_context.return_value = context
    runtime.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr(playwright_tool, "sync_playwright",
                        lambda: SimpleNamespace(start=lambda: runtime))
    tool = playwright_tool.PlaywrightReplayTool("http://127.0.0.1:1", engine="lightpanda")
    request = SimpleNamespace(method="POST", url="http://example.test/save",
                              headers={"content-type": "application/json"},
                              post_data='{"name":"Ada"}', resource_type="xhr")
    request.all_headers = lambda: request.headers
    response = SimpleNamespace(request=request, status=200, status_text="OK",
                               headers={"content-type": "application/json"},
                               body=lambda: b'{"saved":true}')
    response.all_headers = lambda: response.headers
    recorder = CdpHarRecorder(capture_transport=tool.har_capture_transport,
                              capture_new_pages=tool._supports_multiple_pages,
                              playwright_request_transform=tool.har_request_transform)
    try:
        recorder.start(tool._context, tool._page)
        handlers["request"](request)
        handlers["response"](response)
        handlers["requestfinished"](request)
        output = tmp_path / "network.har"
        recorder.stop(tool._context, output)
        entries = json.loads(output.read_text(encoding="utf-8"))["log"]["entries"]
        assert len(entries) == 1
        assert entries[0]["request"]["postData"]["text"] == '{"name":"Ada"}'
        assert entries[0]["response"]["content"]["text"] == '{"saved":true}'
        context.new_cdp_session.assert_not_called()
        assert not recorder.errors
    finally:
        tool.close()
