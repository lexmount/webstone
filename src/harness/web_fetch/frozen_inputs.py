"""Serve frozen HTML inputs to measured methods without network access.

Benchmarks whose inputs are frozen pages use this environment: an HTTPS fixture
on loopback that serves the exact bytes, an optional loopback proxy that keeps
each original URL, and a probe proving the measured child cannot reach anything
else. Nothing here depends on which benchmark supplied the pages.
"""
from __future__ import annotations

import json
import select
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from harness.platform_capabilities import sandbox_exec_command
from harness.record_io import bytes_sha256
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.headless import LOOPBACK_SANDBOX_PROFILE, engine_definition
from harness.web_fetch.runner import classify_exception

CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def input_contract(preserve_original_origin: bool) -> dict:
    """The recorded description of how frozen bytes reached the method."""
    return {
        "body": "official html UTF-8 bytes without mutation",
        "origin": ("original URL retained while frozen bytes are served by a loopback-only proxy"
                   if preserve_original_origin else "original URL retained as evidence metadata only"),
        "subresources": "blocked",
        "javascript": "inline page-authored JavaScript may run; external script and data access is blocked",
        "network_policy": ("local engines run under a loopback-only OS sandbox; "
                           "hosted engines reject subresource types"),
        "csp_response_header": CSP,
    }


@dataclass(frozen=True)
class HostedHtmlRoute:
    """A hosted engine's inline-HTML route; its service enforces input isolation."""

    provider: CloudflareMarkdownProvider
    isolation: dict

    def fetch_html(self, case, html: str, timeout: float) -> ProviderResult:
        return self.provider.fetch_html(case, html, timeout)


def hosted_html_route(name: str, config: dict) -> HostedHtmlRoute | None:
    """Select the inline-HTML route when the configured engine declares a hosted backend."""
    hosted = engine_definition(config.get("engine")).hosted
    if hosted is None:
        return None
    return HostedHtmlRoute(
        CloudflareMarkdownProvider(name, {
            "account_id": config["account_id"],
            "api_token": config["api_token"],
            "browser": hosted.browser,
            "max_bytes": config.get("max_bytes", 20_000_000),
        }),
        {"mode": "cloudflare_reject_external_resource_types"},
    )


def provider_version(identity: dict) -> str | None:
    reported = identity.get("reported_version") or ""
    for token in reported.split():
        if token[:1].isdigit() and "." in token:
            return token
    return None


def check_reported_version(name: str, identity: dict, expected: str | None) -> None:
    if expected and provider_version(identity) != expected:
        raise ValueError(
            f"{name}: expected version {expected}, got {identity.get('reported_version') or 'unavailable'}"
        )


def public_metadata(value: dict) -> dict:
    """Keep diagnostics while excluding private/duplicated raw payloads."""
    return {key: item for key, item in value.items() if not key.startswith("_")}


def fetch_frozen(provider, hosted, case, html: str, timeout: float) -> ProviderResult:
    """Convert adapter exceptions into one inspectable physical-call result.

    A hosted method receives the frozen bytes directly; a local method fetches
    ``case.url`` from the fixture.
    """
    try:
        if hosted is not None:
            return hosted.fetch_html(case, html, timeout)
        return provider.fetch(case, timeout)
    except Exception as error:
        error_type, error_message = classify_exception(error)
        return ProviderResult(
            ok=False,
            content="",
            error_type=error_type,
            error_message=error_message,
            metadata={"exception_class": type(error).__name__},
        )


@contextmanager
def fixture_server(records: list[dict], preserve_original_origin: bool = False):
    bodies = {}
    path_fallbacks = {}
    ambiguous_paths = set()
    hosts = set()
    for record in records:
        if not preserve_original_origin:
            bodies["/case/" + record["track_id"]] = record["html"].encode("utf-8")
            continue
        parsed = urlsplit(record["url"])
        host = parsed.hostname
        if not host:
            raise ValueError("Frozen record URL lacks a hostname: " + record["track_id"])
        hosts.add(host)
        path_key = (host.casefold(), parsed.path or "/")
        key = (*path_key, parsed.query)
        if key in bodies:
            raise ValueError("Frozen fixture URL is not unique: " + record["url"])
        bodies[key] = record["html"].encode("utf-8")
        if path_key in path_fallbacks:
            ambiguous_paths.add(path_key)
        else:
            path_fallbacks[path_key] = bodies[key]

    def frozen_body(host_header: str, request_path: str):
        host = host_header.rsplit(":", 1)[0].strip("[]").casefold()
        parsed = urlsplit(request_path)
        path_key = (host, parsed.path or "/")
        exact = bodies.get((*path_key, parsed.query))
        if exact is not None:
            return exact
        return None if path_key in ambiguous_paths else path_fallbacks.get(path_key)

    def write_frozen(handler, body):
        if body is None:
            handler.send_error(404)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Content-Security-Policy", CSP)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            body = (frozen_body(self.headers.get("Host", ""), self.path)
                    if preserve_original_origin else bodies.get(urlsplit(self.path).path))
            write_frozen(self, body)

        def log_message(self, _format, *_args):
            return

    openssl = shutil.which("openssl")
    if not openssl:
        raise ValueError("openssl is required for the HTTPS frozen-input fixture")
    with tempfile.TemporaryDirectory(prefix="frozen-input-tls-") as directory:
        tls = Path(directory)
        ca_key, ca_cert = tls / "ca-key.pem", tls / "ca.pem"
        leaf_key, leaf_csr, leaf_cert = tls / "leaf-key.pem", tls / "leaf.csr", tls / "leaf.pem"
        san = (",".join("IP:" + host if host.replace(".", "").isdigit() else "DNS:" + host
                        for host in sorted(hosts)) if preserve_original_origin
               else "DNS:localhost,IP:127.0.0.1")
        extensions = tls / "leaf-extensions.cnf"
        extensions.write_text(
            "subjectAltName=" + san + "\nbasicConstraints=critical,CA:FALSE\n"
            "extendedKeyUsage=serverAuth\n", encoding="utf-8")
        commands = [
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
             "-out", str(ca_cert), "-days", "1", "-subj", "/CN=Webstone-Frozen-Input-CA",
             "-addext", "basicConstraints=critical,CA:TRUE"],
            [openssl, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(leaf_key),
             "-out", str(leaf_csr), "-subj", "/CN=Webstone-Frozen-Origin",
             "-addext", "subjectAltName=" + san],
            [openssl, "x509", "-req", "-in", str(leaf_csr), "-CA", str(ca_cert),
             "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(leaf_cert),
             "-days", "1", "-extfile", str(extensions)],
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
            if completed.returncode:
                raise ValueError("Failed to create HTTPS fixture certificate: " + completed.stderr[-1000:])
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(leaf_cert, leaf_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_CONNECT(self):  # noqa: N802 - BaseHTTPRequestHandler API
                upstream = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
                self.send_response(200, "Connection Established")
                self.end_headers()
                peers = (self.connection, upstream)
                try:
                    while True:
                        ready, _, _ = select.select(peers, (), (), 5)
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = upstream if source is self.connection else self.connection
                            target.sendall(data)
                finally:
                    upstream.close()

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlsplit(self.path)
                request_path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                write_frozen(self, frozen_body(parsed.netloc, request_path))

            def log_message(self, _format, *_args):
                return

        proxy = proxy_thread = None
        if preserve_original_origin:
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()
        try:
            endpoint = (f"http://127.0.0.1:{proxy.server_port}" if proxy
                        else f"https://localhost:{server.server_port}")
            yield endpoint, ca_cert
        finally:
            if proxy and proxy_thread:
                proxy.shutdown()
                proxy.server_close()
                proxy_thread.join(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)



def verify_loopback_sandbox(endpoint: str, ca_cert: Path, record: dict,
                            preserve_original_origin: bool = False) -> dict:
    """Prove that the measured child can read the fixture but cannot connect externally."""
    parsed = urlsplit(endpoint)
    origin = urlsplit(record["url"])
    path = origin.path or "/" if preserve_original_origin else "/case/" + record["track_id"]
    direct_probe = (
        "import errno,hashlib,json,socket,ssl,sys;"
        "host,port,path,expected,ca=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4],sys.argv[5];"
        "s=ssl.create_default_context(cafile=ca).wrap_socket(socket.create_connection((host,port),2),server_hostname=host);"
        "s.sendall(('GET '+path+' HTTP/1.0\\r\\nHost: '+host+'\\r\\n\\r\\n').encode());"
        "response=b'';"
        "\nwhile True:\n chunk=s.recv(65536)\n if not chunk: break\n response+=chunk\n"
        "s.close();body=response.split(b'\\r\\n\\r\\n',1)[1];"
        "result={'loopback_body_sha256':hashlib.sha256(body).hexdigest()};"
        "\ntry:\n socket.create_connection(('1.1.1.1',443),1);result['external_connect_errno']=0\n"
        "except OSError as exc:\n result['external_connect_errno']=exc.errno\n"
        "print(json.dumps(result));"
        "sys.exit(0 if result['loopback_body_sha256']==expected and result['external_connect_errno'] in (errno.EPERM,errno.EACCES) else 9)"
    )
    proxy_probe = (
        "import errno,hashlib,json,socket,ssl,sys;"
        "proxy,port,host,path,expected,ca=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4],sys.argv[5],sys.argv[6];"
        "raw=socket.create_connection((proxy,port),2);"
        "raw.sendall(('CONNECT '+host+':443 HTTP/1.0\\r\\nHost: '+host+'\\r\\n\\r\\n').encode());"
        "head=b'';"
        "\nwhile b'\\r\\n\\r\\n' not in head:\n head+=raw.recv(4096)\n"
        "s=ssl.create_default_context(cafile=ca).wrap_socket(raw,server_hostname=host);"
        "s.sendall(('GET '+path+' HTTP/1.0\\r\\nHost: '+host+'\\r\\n\\r\\n').encode());"
        "response=b'';"
        "\nwhile True:\n chunk=s.recv(65536)\n if not chunk: break\n response+=chunk\n"
        "s.close();body=response.split(b'\\r\\n\\r\\n',1)[1];"
        "result={'loopback_body_sha256':hashlib.sha256(body).hexdigest()};"
        "\ntry:\n socket.create_connection(('1.1.1.1',443),1);result['external_connect_errno']=0\n"
        "except OSError as exc:\n result['external_connect_errno']=exc.errno\n"
        "print(json.dumps(result));"
        "sys.exit(0 if result['loopback_body_sha256']==expected and result['external_connect_errno'] in (errno.EPERM,errno.EACCES) else 9)"
    )
    probe = proxy_probe if preserve_original_origin else direct_probe
    probe_args = [origin.hostname or "", path] if preserve_original_origin else [path]
    completed = subprocess.run(
        [*sandbox_exec_command(LOOPBACK_SANDBOX_PROFILE), sys.executable, "-c", probe,
         parsed.hostname or "127.0.0.1", str(parsed.port), *probe_args,
         bytes_sha256(record["html"].encode("utf-8")), str(ca_cert)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if completed.returncode != 0:
        raise ValueError("Loopback-only network isolation probe failed: " + completed.stderr.strip())
    result = json.loads(completed.stdout)
    return {
        "mode": "macos_sandbox_exec_loopback_only",
        "profile_sha256": bytes_sha256(LOOPBACK_SANDBOX_PROFILE.encode()),
        **result,
    }

