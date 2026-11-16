"""Remote-only, one-call transport; never import this module to perform a call."""
import base64
import hashlib
import json
import ipaddress
import os
from pathlib import Path
import signal
import sys
import time


RUNTIME_SOURCES = ('harness/env_file.py', 'harness/distribution.py',
                   'harness/web_fetch/providers/http_client.py')


def runtime_sources(root):
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in RUNTIME_SOURCES}


def main():
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
    signal.signal(signal.SIGALRM, lambda *_: sys.exit('Remote call deadline exceeded'))
    budget = ready_started + float(envelope['timeout']) - time.monotonic()
    if budget <= 0:
        raise TimeoutError('No remote API budget remains')
    signal.setitimer(signal.ITIMER_REAL, budget)
    sys.path.insert(0, str(runtime_root))
    from harness.env_file import parse_env_file
    from harness.web_fetch.providers.http_client import request
    env = parse_env_file(envelope['remote_env_file'])
    key = env.get('ANTHROPIC_API_KEY')
    if not key or env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/') != 'https://api.anthropic.com':
        raise RuntimeError('Missing remote credential or unexpected endpoint')
    response = request('https://api.anthropic.com/v1/messages', method='POST',
        json_body=envelope['body'], headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
        timeout=budget, max_bytes=int(envelope['max_bytes']), allow_redirects=False)
    if key.encode() in response.body or key in json.dumps(response.headers):
        raise RuntimeError('Credential detected in response; refusing transfer')
    print(json.dumps({'ssh_server': connection[2], 'status': response.status,
                      'remote_api_budget_seconds': budget, 'headers': response.headers,
                      'body_b64': base64.b64encode(response.body).decode()}))


if __name__ == '__main__':
    main()
