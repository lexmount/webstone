"""Model-mediated Web Fetch, transported exclusively through the declared SSH host."""
from __future__ import annotations

import base64
import hashlib
import json
import ipaddress
import re
import shlex
import subprocess
from pathlib import Path

from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_projection import native_answer_projection, project_native_answer, project_native_result_fields
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.providers.base import Provider, terminate_owned_process_tree, await_owned_process_exit
from harness.web_fetch.providers.http_client import HttpResponse

TOOL = {'type': 'web_fetch_20260318', 'name': 'web_fetch', 'allowed_callers': ['direct'],
        'use_cache': False, 'response_inclusion': 'full', 'citations': {'enabled': False}}


class ClaudeFetchProvider(Provider):
    native_configuration_fields = (*Provider.native_configuration_fields, 'model', 'max_tokens', 'tool')

    def __init__(self, name, config):
        super().__init__(name, config)
        self._process = None

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
        envelope = {'body': arguments['json_body'], 'timeout': arguments['timeout'],
                    'max_bytes': arguments['max_bytes'],
                    'expected_ssh_server_ip': self.config['ssh_server_ip'],
                    'remote_env_file': self.config['remote_env_file'],
                    'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest()}
        self._process = subprocess.Popen(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', self.config['ssh_host'],
             shlex.join([self.config['remote_uv'], 'run', '--no-project', '--python', '3.11',
                         'python', self.config['remote_worker']])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        try:
            output, error = self._process.communicate(json.dumps(envelope).encode(), timeout=arguments['timeout'])
        except BaseException:
            self.cancel()
            raise
        if self._process.returncode:
            raise RuntimeError('Remote Claude worker failed: ' + error.decode(errors='replace')[-1200:])
        receipt = json.loads(output)
        if receipt.get('ssh_server') != self.config['ssh_server_ip']:
            raise ValueError('Missing designated remote execution receipt')
        return HttpResponse(receipt['status'], endpoint, receipt.get('headers', {}),
                            base64.b64decode(receipt['body_b64'], validate=True))

    def cancel(self):
        if self._process is not None:
            terminate_owned_process_tree(self._process)
            await_owned_process_exit(self._process)

    def fetch(self, case, timeout, *, native_route_ledger=None):
        self.check_native_call(case)
        endpoint, arguments = self.native_http_arguments(case, timeout)
        response = self.native_http_request(case, self._remote_request, endpoint,
                            native_route_ledger=native_route_ledger, **arguments)
        try:
            payload = json.loads(response.body)
        except ValueError:
            payload = {}
        blocks = payload.get('content', []) if isinstance(payload, dict) else []
        calls = [b for b in blocks if b.get('type') == 'server_tool_use']
        wrong_url = any(b.get('name') != 'web_fetch' or b.get('input', {}).get('url') != case.url for b in calls)
        declaration = native_answer_projection(self.type, case, payload, response.status)
        content, _ = project_native_answer(payload, declaration)
        fields = project_native_result_fields(payload, declaration, request_url=case.url)
        error = ('authentication' if response.status in {401, 403} else
                 'rate_limit' if response.status == 429 else
                 'api_error' if not 200 <= response.status < 300 else
                 'wrong_tool_url' if wrong_url else
                 'tool_error' if not content else None)
        if error:
            content = ''
        metadata = {'_raw_response_b64': base64.b64encode(response.body).decode(),
                    'raw_response_sha256': hashlib.sha256(response.body).hexdigest(),
                    'raw_response_bytes': len(response.body), 'api_http_status': response.status,
                    'native_answer_projection': declaration, 'execution_ssh_server': self.config['ssh_server_ip'],
                    'model': payload.get('model'), 'usage': payload.get('usage'),
                    'stop_reason': payload.get('stop_reason'), 'client_retries': 0,
                    'service_retry_policy': 'provider_default_not_overridden',
                    'model_text_scored': False, 'request_timeout_seconds': timeout,
                    **response.dns_metadata}
        return ProviderResult(ok=error is None, content=content, error_type=error,
            title=fields['title'], final_url=fields['final_url'],
            error_message='Inspect retained native tool response' if error else None, metadata=metadata)
