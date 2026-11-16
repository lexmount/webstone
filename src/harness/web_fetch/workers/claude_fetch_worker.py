"""Remote-only, one-call transport; never import this module to perform a call."""
import base64
import hashlib
import json
import ipaddress
import os
from pathlib import Path
import signal
import sys
import urllib.error
import urllib.request


def main():
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) != 4:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    envelope = json.load(sys.stdin)
    if connection[2] != envelope['expected_ssh_server_ip']:
        raise RuntimeError('Refusing Claude access outside designated SSH server')
    address = ipaddress.ip_address(connection[2])
    if address.is_loopback or address.is_unspecified:
        raise RuntimeError('Refusing Claude access through a local SSH target')
    if envelope['worker_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError('Remote worker source differs from frozen local source')
    # The remote hard deadline also stops work after an SSH disconnection.
    signal.signal(signal.SIGALRM, lambda *_: sys.exit('Remote call deadline exceeded'))
    budget = max(.1, float(envelope['timeout']) - 1)
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
                          'headers': dict(response.headers), 'body_b64': base64.b64encode(raw).decode()}))


if __name__ == '__main__':
    main()
