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
import urllib.error
import urllib.request


def main():
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    address = ipaddress.ip_address(connection[2])
    if address.is_loopback or address.is_unspecified:
        raise RuntimeError('Refusing Claude access through a local SSH target')
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    ready_started = time.monotonic()
    print(json.dumps({'kind': 'claude_fetch_ready', 'ssh_server': connection[2],
                      'worker_sha256': source_hash}), flush=True)
    envelope = json.load(sys.stdin)
    if connection[2] != envelope['expected_ssh_server_ip']:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    if envelope['worker_sha256'] != source_hash:
        raise RuntimeError('Remote worker source differs from frozen local source')
    # The remote hard deadline also stops work after an SSH disconnection.
    # Anchor to readiness, not receipt: this conservatively accounts for both
    # transport directions without comparing clocks on different machines.
    signal.signal(signal.SIGALRM, lambda *_: sys.exit('Remote call deadline exceeded'))
    budget = ready_started + float(envelope['timeout']) - time.monotonic()
    if budget <= 0:
        raise TimeoutError('No remote API budget remains')
    signal.setitimer(signal.ITIMER_REAL, budget)
    env = {}
    for line in Path(envelope['remote_env_file']).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            name, value = line.removeprefix('export ').split('=', 1)
            env[name.strip()] = value.strip().strip('\"\'')
    key = env.get('ANTHROPIC_API_KEY')
    if not key or env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/') != 'https://api.anthropic.com':
        raise RuntimeError('Missing remote credential or unexpected endpoint')
    request = urllib.request.Request('https://api.anthropic.com/v1/messages',
        data=json.dumps(envelope['body']).encode(), headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(request, timeout=budget)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(int(envelope['max_bytes']) + 1)
        if len(raw) > int(envelope['max_bytes']):
            raise RuntimeError('Remote response exceeds declared byte budget')
        if key.encode() in raw:
            raise RuntimeError('Credential detected in response; refusing transfer')
        print(json.dumps({'ssh_server': connection[2], 'status': response.status,
                          'remote_api_budget_seconds': budget,
                          'headers': dict(response.headers), 'body_b64': base64.b64encode(raw).decode()}))


if __name__ == '__main__':
    main()
