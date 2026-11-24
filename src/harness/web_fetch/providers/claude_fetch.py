"""Model-mediated Web Fetch, transported exclusively through the declared SSH host."""
from __future__ import annotations

import base64
import hashlib
import json
import ipaddress
import os
import re
import selectors
import shlex
import subprocess
import time
from pathlib import Path

from harness.web_fetch.models import ProviderResult
from harness.call_diagnostics import emit
from harness.web_fetch.native_projection import native_answer_projection, project_native_answer, project_native_result_fields
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.providers.base import Provider, terminate_owned_process_tree, await_owned_process_exit
from harness.http_client import HttpResponse
from harness.web_fetch.workers.claude_fetch_worker import runtime_sources
from harness.record_io import parse_utf8_json

TOOL = {'type': 'web_fetch_20260318', 'name': 'web_fetch', 'allowed_callers': ['direct'],
        'use_cache': False, 'response_inclusion': 'full', 'citations': {'enabled': False}}
EXECUTION_LOCATION = 'configured_ssh_worker'


class RemoteClaudeTransportError(RuntimeError):
    """A verified remote worker observed a transport failure, not an API response."""
    def __init__(self, receipt):
        self.receipt = receipt
        error = receipt['transport_error']
        super().__init__(f"Remote HTTPS {error['phase']}: {error['reason_type']}: {error['message']}")


def _read_remote_ready(stream, deadline):
    """Bound SSH/worker startup with the same deadline as the API call."""
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while len(data) <= 8192:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError('Remote Claude startup exceeded the call deadline')
            chunk = os.read(stream.fileno(), 8193 - len(data))
            if not chunk:
                raise RuntimeError('Remote Claude worker closed before readiness')
            data.extend(chunk)
            if b'\n' in data:
                return json.loads(data)
    raise ValueError('Remote Claude readiness exceeds its protocol limit')


class ClaudeFetchProvider(Provider):
    native_configuration_fields = (*Provider.native_configuration_fields, 'model', 'max_tokens', 'tool')

    def __init__(self, name, config):
        super().__init__(name, config)
        self._process = None
        self._call_started = None

    def begin_call(self):
        self._call_started = time.monotonic()

    @property
    def cancellation_cleanup_seconds(self):
        # This grace only reaps a cancelled worker; it never extends answer time.
        return .5

    def validate(self):
        errors = []
        server = self.config.get('ssh_server_ip', '')
        try:
            address = ipaddress.ip_address(server)
            if address.is_loopback or address.is_unspecified:
                raise ValueError('Local server is forbidden')
            host = str(self.config.get('ssh_host', ''))
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*@' + re.escape(server), host) is None:
                raise ValueError('SSH target differs from the declared server')
        except ValueError:
            errors.append('Claude requires the designated SSH host and nonlocal server IP')
        for key in ('remote_worker', 'remote_env_file', 'remote_uv'):
            if not str(self.config.get(key, '')).startswith('/'):
                errors.append(key + ' must be an absolute remote path')
        if self.config.get('api_key'):
            errors.append('Claude credentials must remain on the SSH host')
        if self.config.get('dns_mode', 'system') != 'system':
            errors.append('Claude SSH transport requires remote system DNS')
        if self.config.get('tool', TOOL) != TOOL:
            errors.append('Unsupported Claude Web Fetch tool contract')
        return errors

    def native_route(self, case):
        return NativeRoute('http', 'https://api.anthropic.com/v1/messages',
                           ('content.content.content', 'content.content.url'))

    def native_http_arguments(self, case, timeout):
        body = {'model': self.config.get('model', 'claude-haiku-5-5'),
                'max_tokens': self.config.get('max_tokens', 1024),
                'tools': [TOOL], 'tool_choice': {'type': 'tool', 'name': 'web_fetch'},
                'system': 'Call web_fetch for exactly the user URL. Do not search, visit other URLs, summarize, or rewrite the fetched content.',
                'messages': [{'role': 'user', 'content': case.url}]}
        return 'https://api.anthropic.com/v1/messages', self.native_http_options(body, {}, timeout)

    def _remote_request(self, endpoint, **arguments):
        errors = self.validate()
        if errors:
            raise ValueError('; '.join(errors))
        # This path has no local HTTP client, key lookup, proxy, or fallback.
        worker = Path(__file__).parents[1] / 'workers/claude_fetch_worker.py'
        shared = runtime_sources(Path(__file__).parents[3])
        emit('remote_http_request_prepared', url=endpoint, method=arguments['method'],
             json_body=arguments['json_body'], timeout=arguments['timeout'],
             execution_location=EXECUTION_LOCATION)
        deadline = getattr(self, '_call_deadline', time.monotonic() + arguments['timeout'])
        envelope = {'body': arguments['json_body'],
                    'max_bytes': arguments['max_bytes'],
                    'expected_ssh_server_ip': self.config['ssh_server_ip'],
                    'remote_env_file': self.config['remote_env_file'],
                    'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
                    'runtime_sources': shared}
        startup_budget = deadline - time.monotonic()
        if startup_budget <= 0:
            raise TimeoutError('No remote startup budget remains')
        self._process = subprocess.Popen(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', self.config['ssh_host'],
             shlex.join([self.config['remote_uv'], 'run', '--frozen', '--no-sync',
                         '--project', str(Path(self.config['remote_worker']).parent), '--python', '3.11',
                         'python', self.config['remote_worker'], str(startup_budget)])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        try:
            ready = _read_remote_ready(self._process.stdout, deadline)
            if (ready.get('kind') != 'claude_fetch_ready'
                    or ready.get('ssh_server') != self.config['ssh_server_ip']
                    or ready.get('worker_sha256') != envelope['worker_sha256']
                    or ready.get('runtime_sources') != shared):
                raise ValueError('Remote Claude readiness differs from the declared server or source')
            emit('ssh_worker_ready', execution_location=EXECUTION_LOCATION,
                 worker_sha256=ready['worker_sha256'])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('No API budget remains after SSH startup')
            envelope['timeout'] = remaining
            output, _ = self._process.communicate(json.dumps(envelope).encode(), timeout=remaining)
        except BaseException:
            self.cancel()
            raise
        emit('ssh_process_completed', exit_code=self._process.returncode)
        if self._process.returncode:
            raise RuntimeError(f'Remote Claude worker failed with exit code {self._process.returncode}')
        receipt = json.loads(output)
        if receipt.get('ssh_server') != self.config['ssh_server_ip']:
            raise ValueError('Missing designated remote execution receipt')
        self._remote_execution_receipt = {
            'remote_api_budget_seconds': receipt.get('remote_api_budget_seconds'),
            'remote_worker_sha256': ready['worker_sha256'],
            'remote_runtime_sources': shared,
            'remote_transport_events': receipt.get('transport_events', []),
            'remote_https_environment': receipt.get('https_environment', {}),
            'deadline_policy': 'shared_caller_deadline_with_ready_anchored_remote_stop'}
        for event in receipt.get('transport_events', []):
            emit('remote_transport_observed', execution_location=EXECUTION_LOCATION, observation=event)
        if receipt.get('kind') == 'claude_fetch_transport_error':
            raise RemoteClaudeTransportError(receipt)
        body = base64.b64decode(receipt['body_b64'], validate=True)
        emit('remote_http_response_received', status=receipt['status'], url=endpoint,
             headers=receipt.get('headers', {}), body_bytes=len(body),
             body=body.decode('utf-8', errors='replace'),
             execution_location=EXECUTION_LOCATION)
        return HttpResponse(receipt['status'], endpoint, receipt.get('headers', {}), body)

    def cancel(self):
        if self._process is not None:
            terminate_owned_process_tree(self._process)
            await_owned_process_exit(self._process)

    def fetch(self, case, timeout, *, native_route_ledger=None):
        started = self._call_started if self._call_started is not None else time.monotonic()
        self._call_started = None
        self._call_deadline = started + timeout
        self.check_native_call(case)
        endpoint, arguments = self.native_http_arguments(case, timeout)
        try:
            response = self.native_http_request(case, self._remote_request, endpoint,
                                native_route_ledger=native_route_ledger, **arguments)
        except RemoteClaudeTransportError as error:
            return ProviderResult(ok=False, content='', error_type='network', error_message=str(error),
                metadata={'execution_location': EXECUTION_LOCATION, 'client_retries': 0,
                          'remote_transport_error': error.receipt['transport_error'],
                          **getattr(self, '_remote_execution_receipt', {})})
        try:
            decoded = parse_utf8_json(response.body)
        except ValueError:
            decoded = None
        payload = decoded if isinstance(decoded, dict) else {}
        blocks = payload.get('content', [])
        malformed = (not isinstance(decoded, dict) or not isinstance(blocks, list)
                     or any(not isinstance(block, dict) for block in blocks))
        blocks = [] if malformed else blocks
        calls = [b for b in blocks if b.get('type') == 'server_tool_use']
        malformed = malformed or any(not isinstance(b.get('input'), dict) for b in calls)
        wrong_url = any(b.get('name') != 'web_fetch' or
                        (b.get('input') if isinstance(b.get('input'), dict) else {}).get('url') != case.url for b in calls)
        declaration = native_answer_projection(self.type, case, payload, response.status) if isinstance(decoded, dict) else None
        content = project_native_answer(payload, declaration)[0] if declaration else ''
        fields = (project_native_result_fields(payload, declaration, request_url=case.url)
                  if declaration else {'title': None, 'final_url': None})
        error = ('authentication' if response.status in {401, 403} else
                 'rate_limit' if response.status == 429 else
                 'api_error' if not 200 <= response.status < 300 else
                 'malformed_response' if malformed else
                 'wrong_tool_url' if wrong_url else
                 'tool_error' if not content else None)
        if error:
            content = ''
        metadata = {'_raw_response_b64': base64.b64encode(response.body).decode(),
                    'raw_response_sha256': hashlib.sha256(response.body).hexdigest(),
                    'raw_response_bytes': len(response.body), 'api_http_status': response.status,
                    **({'native_answer_projection': declaration} if declaration else {}),
                    'execution_location': EXECUTION_LOCATION,
                    'model': payload.get('model'), 'usage': payload.get('usage'),
                    'stop_reason': payload.get('stop_reason'), 'client_retries': 0,
                    'service_retry_policy': 'provider_default_not_overridden',
                    'model_text_scored': False, 'request_timeout_seconds': timeout,
                    **getattr(self, '_remote_execution_receipt', {}),
                    **response.dns_metadata}
        return ProviderResult(ok=error is None, content=content, error_type=error,
            title=fields['title'], final_url=fields['final_url'],
            error_message='Inspect retained native tool response' if error else None, metadata=metadata)
