"""Remote-only, one-call transport; never import this module to perform a call."""
import base64
import hashlib
import http.client
import json
import ipaddress
import math
import os
from pathlib import Path
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request


RUNTIME_SOURCES = ('harness/env_file.py', 'harness/distribution.py',
                   'harness/http_client.py',
                   'harness/call_diagnostics.py')


class TransportTrace:
    """Retain transport stages without request credentials or model content."""
    def __init__(self, key):
        self.key = key
        self.events = []

    def emit(self, source, event, **fields):
        if event not in {'http_request_started', 'http_headers_received',
                         'http_response_received', 'http_request_failed'}:
            return
        safe = {name: value for name, value in fields.items()
                if name in {'status', 'phase', 'elapsed_ms', 'error_type', 'message'}}
        safe = {name: value.replace(self.key, '[REDACTED]') if isinstance(value, str) else value
                for name, value in safe.items()}
        self.events.append({'event': event, **safe})


def runtime_sources(root):
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in RUNTIME_SOURCES}


def main(startup_budget=30.0):
    if not math.isfinite(startup_budget) or startup_budget <= 0:
        raise ValueError('Invalid remote startup budget')
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    address = ipaddress.ip_address(connection[2])
    if address.is_loopback or address.is_unspecified:
        raise RuntimeError('Refusing Claude access through a local SSH target')
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runtime_root = Path(__file__).parent/'src'
    if not (runtime_root/'harness/env_file.py').is_file():
        runtime_root = Path(__file__).parents[3]
    shared = runtime_sources(runtime_root)
    ready_started = time.monotonic()
    signal.signal(signal.SIGALRM, lambda *_: sys.exit('Remote call deadline exceeded'))
    signal.setitimer(signal.ITIMER_REAL, startup_budget)
    print(json.dumps({'kind': 'claude_fetch_ready', 'ssh_server': connection[2],
                      'worker_sha256': source_hash, 'runtime_sources': shared}), flush=True)
    envelope = json.load(sys.stdin)
    if connection[2] != envelope['expected_ssh_server_ip']:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    if envelope['worker_sha256'] != source_hash:
        raise RuntimeError('Remote worker source differs from frozen local source')
    if envelope.get('runtime_sources') != shared:
        raise RuntimeError('Remote shared runtime differs from frozen local source')
    # The remote hard deadline also stops work after an SSH disconnection.
    # Anchor to readiness, not receipt: this conservatively accounts for both
    # transport directions without comparing clocks on different machines.
    envelope_budget = float(envelope['timeout'])
    if not math.isfinite(envelope_budget) or envelope_budget <= 0:
        raise ValueError('Invalid remote request budget')
    budget = ready_started + min(startup_budget, envelope_budget) - time.monotonic()
    if budget <= 0:
        raise TimeoutError('No remote API budget remains')
    signal.setitimer(signal.ITIMER_REAL, budget)
    sys.path.insert(0, str(runtime_root))
    from harness.env_file import parse_env_file
    from harness.call_diagnostics import diagnostic_context
    from harness.http_client import request
    env = parse_env_file(envelope['remote_env_file'])
    key = env.get('ANTHROPIC_API_KEY')
    if not key or env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/') != 'https://api.anthropic.com':
        raise RuntimeError('Missing remote credential or unexpected endpoint')
    if not key.isascii() or any(ord(char) <= 32 or ord(char) >= 127 for char in key):
        raise RuntimeError('Remote credential is not a valid HTTP header value')
    trace = TransportTrace(key)
    environment = {'tls_library': ssl.OPENSSL_VERSION,
                   'https_proxy_configured': bool(urllib.request.getproxies().get('https'))}
    try:
        with diagnostic_context(trace, names=()):
            response = request('https://api.anthropic.com/v1/messages', method='POST',
                json_body=envelope['body'], headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
                timeout=budget, max_bytes=int(envelope['max_bytes']), allow_redirects=False)
    except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
        reason = getattr(error, 'reason', error)
        phase = next((e['phase'] for e in reversed(trace.events) if 'phase' in e), 'connect_or_headers')
        print(json.dumps({'kind': 'claude_fetch_transport_error', 'ssh_server': connection[2],
            'remote_api_budget_seconds': budget, 'transport_events': trace.events,
            'https_environment': environment, 'transport_error': {
                'phase': phase, 'exception_type': type(error).__name__,
                'reason_type': type(reason).__name__,
                'message': str(reason).replace(key, '[REDACTED]')}}))
        return
    except Exception:
        # Header validation and third-party exceptions can include credentials
        # in repr/bytes form. Do not transfer their unsanitized traceback.
        raise RuntimeError('Remote HTTPS request failed before a response was available') from None
    if key.encode() in response.body or key in json.dumps(response.headers):
        raise RuntimeError('Credential detected in response; refusing transfer')
    print(json.dumps({'ssh_server': connection[2], 'status': response.status,
                      'remote_api_budget_seconds': budget, 'headers': response.headers,
                      'transport_events': trace.events, 'https_environment': environment,
                      'body_b64': base64.b64encode(response.body).decode()}))


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) == 2 else 30.0)
