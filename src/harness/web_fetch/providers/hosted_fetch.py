"""URL-only hosted native retrieval; no Agent or browser-session fallback.

``HostedFetchProvider`` owns the shared call, evidence and failure flow. Each
vendor is a small subclass that declares its endpoint, request body, answer
fields and the few rules where its envelope differs.
"""
from __future__ import annotations

import base64
import hashlib
import uuid

from lexbench_webfetch.models import EvalCase
from harness.record_io import parse_utf8_json
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.native_projection import json_object, native_answer_projection, project_native_files
from harness.web_fetch.network_dns import validate_dns_mode
from harness.web_fetch.providers.base import Provider
from harness.http_client import inner_timeout_budget, request


def _http_status(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


class HostedFetchProvider(Provider):
    """Shared flow for a hosted fetch API that returns one JSON envelope."""

    # The shared runner owns declared failure retries; the adapter calls once.
    native_configuration_fields = (*Provider.native_configuration_fields,
        'render', 'debug', 'proxies', 'proxy_country', 'engine', 'proxy', 'output_format',
        'no_cache', 'return_format', 'retain_images', 'no_gfm', 'keep_img_data_url',
        'with_images_summary', 'with_links_summary', 'embedded_media_keep_img_data_url')
    capability_flags = frozenset({'malformed_envelope_is_evidence'})

    # Vendor description.
    endpoint: str
    route_fields: tuple[str, ...]
    answer_prefix: tuple[str, ...] = ()  # object holding the answer fields
    content_field = 'content'
    title_field: str | None = None
    final_url_field: str | None = None
    delivers_files = False  # file tasks receive the native file
    raw_file_output = False  # file tasks request the vendor's raw format
    forbidden_error = 'authentication'  # error type for HTTP 403
    retain_service_error = False  # keep a structured service error in metadata

    def native_route(self, case):
        return NativeRoute('http', self.endpoint, self.route_fields)

    def validate(self) -> list[str]:
        errors = [] if self.config.get("api_key") else ["api_key"]
        errors += self._option_errors()
        try:
            validate_dns_mode(self.config.get("dns_mode", "system"))
        except ValueError as error:
            errors.append(str(error))
        output_format = self.config.get("output_format", "markdown")
        if output_format != "markdown":
            errors.append("unsupported output_format; this adapter requires markdown")
        return errors

    def _option_errors(self) -> list[str]:
        return []

    def native_http_arguments(self, case, timeout):
        budget = inner_timeout_budget(timeout, native_default=True)
        output_format = self.config.get("output_format", "markdown")
        if output_format != "markdown":
            raise ValueError("This adapter requires markdown output")
        body: dict = {"url": case.url}
        headers = self._request(case, str(self.config.get("api_key", "")), output_format, body)
        return self.config.get("endpoint", self.endpoint), self.native_http_options(body, headers, budget)

    def _request(self, case, key, output_format, body) -> dict:
        """Add vendor fields to ``body`` and return the request headers."""
        return {"Authorization": f"Bearer {key}"}

    # Envelope reading.
    def _answer(self, payload):
        for key in self.answer_prefix:
            payload = json_object(payload.get(key))
        return payload

    def _upstream_status(self, payload):
        return None

    def _service_error(self, payload, native_file_delivery) -> bool:
        return bool(payload.get("error"))

    def _record_upstream_status(self, metadata, status) -> None:
        """Declare whether the envelope reports the target page's HTTP status."""

    def _envelope_complete(self, status, title) -> bool:
        return status is not None

    @staticmethod
    def _delivered_file(payload) -> tuple[str, bool]:
        """Field holding a delivered file and whether it is base64 encoded."""
        raise NotImplementedError

    @staticmethod
    def _file_encoding_valid(payload) -> bool:
        raise NotImplementedError

    def _call_metadata(self, body) -> dict:
        return {}

    @classmethod
    def native_answer_selection(cls, selection):
        """Pre-call answer selection; part of the frozen policy identity."""
        payload = selection.payload
        successful_http = selection.successful_http
        prefix = list(cls.answer_prefix)
        answer = payload
        for key in prefix:
            answer = json_object(answer.get(key))
        declaration = {'schema_version': 2, 'content_pointer': [*prefix, cls.content_field],
                       'content_format': ('text' if successful_http and isinstance(answer.get(cls.content_field), str)
                                          else 'empty'),
                       'optional_content': True, 'result_fields': {
                           key: {'pointer': [*prefix, source], 'format': 'text_or_none'}
                           for key, source in (('title', cls.title_field), ('final_url', cls.final_url_field))
                           if source}}
        if selection.policy_version >= 2 and selection.task_kind == 'file' and cls.delivers_files:
            declaration['content_format'] = 'empty'
            file_field, binary = cls._delivered_file(payload)
            file_value = payload.get(file_field)
            if (successful_http and cls._file_encoding_valid(payload) and not payload.get('error')
                    and isinstance(file_value, str) and file_value):
                try:
                    delivered = base64.b64decode(file_value, validate=True) if binary else file_value.encode('utf-8')
                except (ValueError, UnicodeError):
                    delivered = b''
                if delivered:
                    declaration['files'] = {'pointer': [file_field],
                                            'format': 'base64_file' if binary else 'utf8_file'}
        return declaration

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        self.check_native_call(case)
        if case.task_kind not in {"body", "structure", "resource", "file"}:
            return ProviderResult(False, "", error_type="unsupported_task",
                                  error_message="This adapter does not recognize the task kind")
        budget = inner_timeout_budget(timeout, native_default=True)
        endpoint, arguments = self.native_http_arguments(case, budget)
        body = arguments['json_body']
        file_task = case.task_kind == "file"
        output_format = "raw" if file_task and self.raw_file_output else self.config.get("output_format", "markdown")
        native_file_delivery = file_task and self.delivers_files
        response = self.native_http_request(case, request, endpoint,
            native_route_ledger=native_route_ledger, **arguments)
        metadata = {
            "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
            "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
            "raw_response_bytes": len(response.body),
            "api_http_status": response.status, "output_contract": output_format,
            **response.dns_metadata,
            "client_retries": 0, "retrieval_hints_applied": False,
            "service_retry_policy": "provider_default_not_overridden",
            "request_timeout_seconds": budget,
        }
        # API envelopes are evidence, never page content.
        api_ok = 200 <= response.status < 300
        content, status, final_url, title, malformed, service_error, files, payload = self._read_response(
            response, case, metadata, native_file_delivery, output_format)
        if not api_ok:
            # Keep error envelopes in raw evidence, not in scored page text.
            content = ""
            error_type = (self.forbidden_error if response.status == 403 else
                          "authentication" if response.status == 401 else
                          "rate_limit" if response.status == 429 else
                          "quota" if response.status == 402 else "api_error")
            message = f"Fetch API returned HTTP {response.status}; inspect retained response"
        elif service_error:
            error_type, message = "api_error", "Fetch service reported an error or binary output; inspect retained response"
        elif malformed:
            error_type, message = "invalid_response", "Fetch API returned an invalid response envelope"
        elif status is not None and not 200 <= status < 300:
            error_type, message = "http_error", f"Target page returned HTTP {status}"
        elif not (files if native_file_delivery else content.strip()):
            error_type, message = "empty_content", "Fetch API returned empty file" if native_file_delivery else "Fetch API returned empty Markdown"
        else:
            error_type = message = None
        if isinstance(payload, dict):
            metadata['native_answer_projection'] = native_answer_projection(
                self.type, case, payload, response.status, interface=output_format)
        metadata.update(self._call_metadata(body))
        if file_task:
            metadata['native_file_delivery'] = native_file_delivery
            metadata['file_bytes_delivered'] = sum(len(item['data']) for item in files)
        metadata['upstream_status'] = status
        return ProviderResult(
            ok=error_type is None, content=content, status_code=status,
            final_url=final_url, title=title if isinstance(title, str) else None,
            error_type=error_type, error_message=message, metadata=metadata, files=files,
        )

    def _read_response(self, response, case, metadata, native_file_delivery, output_format):
        api_ok = 200 <= response.status < 300
        content, status, final_url, title = "", None, None, None
        malformed = service_error = False
        files = []
        try:
            payload = parse_utf8_json(response.body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return content, status, final_url, title, True, service_error, files, payload
        answer = self._answer(payload)
        content = answer.get(self.content_field)
        title = answer.get(self.title_field) if self.title_field else None
        if self.final_url_field and isinstance(answer.get(self.final_url_field), str):
            final_url = answer[self.final_url_field]
        status = _http_status(self._upstream_status(payload))
        service_error = self._service_error(payload, native_file_delivery)
        self._record_upstream_status(metadata, status)
        metadata["request_id"] = payload.get("id") if isinstance(payload.get("id"), str) else None
        malformed = not isinstance(content, str) or not self._envelope_complete(status, title)
        if native_file_delivery:
            declaration = native_answer_projection(self.type, case, payload, response.status, interface=output_format)
            file_bytes = project_native_files(payload, declaration)
            file_field, _ = self._delivered_file(payload)
            raw_file = payload.get(file_field)
            malformed = status is None or not isinstance(raw_file, str) or (bool(raw_file) and not file_bytes and api_ok and not service_error)
            headers = payload.get('headers') if isinstance(payload.get('headers'), dict) else {}
            content_type = payload.get('contentType') or next((v for k, v in headers.items() if k.lower() == 'content-type'), None)
            files = [{'data': data, 'target_url': case.url, 'source_url': final_url or case.url,
                      'content_type': content_type} for data in file_bytes]
            content = ''
        content = content if isinstance(content, str) else ""
        if self.retain_service_error and isinstance(payload.get("error"), dict):
            metadata["service_error"] = payload["error"]
        return content, status, final_url, title, malformed, service_error, files, payload


class BrightDataUnlockerProvider(HostedFetchProvider):
    endpoint = "https://api.brightdata.com/request"
    route_fields = ('response_body',)
    delivers_files = True
    raw_file_output = True
    # Raw mode returns the selected Markdown or the native file directly.
    capability_flags = frozenset({'raw_answer_bytes', 'raw_http_evidence'})

    def native_route(self, case):
        if case.task_kind == 'file':
            return NativeRoute('http', self.endpoint, ('original_bytes',))
        return super().native_route(case)

    def _option_errors(self):
        errors = [] if self.config.get("zone") else ["zone"]
        if "render" in self.config and not isinstance(self.config["render"], bool):
            errors.append("render must be boolean")
        if "debug" in self.config and not isinstance(self.config["debug"], bool):
            errors.append("debug must be boolean")
        return errors

    def _request(self, case, key, output_format, body):
        body.update(zone=self.config["zone"], format="raw")
        if case.task_kind != "file":
            body["data_format"] = "markdown"
        # Native Web Unlocker option, applied uniformly from the declared
        # config. It adds no caller-side browser, wait, or follow-up fetch.
        if "render" in self.config:
            body["render"] = "true" if self.config["render"] else "false"
        if "debug" in self.config:
            body["debug"] = self.config["debug"]
        return {"Authorization": f"Bearer {key}"}

    @staticmethod
    def native_answer_selection(selection):
        """Pre-call answer selection; part of the frozen policy identity."""
        successful_http = selection.successful_http
        if selection.policy_version >= 3 and selection.task_kind == 'file':
            return {'schema_version': 2, 'source_format': 'bytes', 'content_format': 'empty',
                    **({'files': {'format': 'raw_bytes'}} if successful_http else {})}
        return {'schema_version': 2, 'source_format': 'bytes',
                'content_format': 'utf8' if successful_http else 'empty'}

    def _read_response(self, response, case, metadata, native_file_delivery, output_format):
        api_ok = 200 <= response.status < 300
        content, malformed, files = "", False, []
        if native_file_delivery:
            # Web Unlocker raw returns the native unmodified response. Do
            # not decode, re-download, or manufacture the requested file.
            content_type = next((v for k, v in response.headers.items()
                                 if k.lower() == 'content-type'), None)
            files = [{'data': response.body, 'target_url': case.url,
                      'source_url': case.url, 'content_type': content_type}] if api_ok and response.body else []
        else:
            try:
                content = response.body.decode("utf-8") if api_ok else ""
            except UnicodeDecodeError:
                malformed = True
        metadata["native_answer_projection"] = native_answer_projection(
            self.type, case, {}, response.status)
        metadata["upstream_http_status_available"] = False
        return content, None, None, None, malformed, False, files, None

    def _call_metadata(self, body):
        return {"render": self.config.get("render")}


class BrowserUseFetchProvider(HostedFetchProvider):
    endpoint = "https://fetch.browser-use.com/fetch"
    route_fields = ('body', 'final_url')
    content_field = 'body'
    final_url_field = 'final_url'
    delivers_files = True

    def __init__(self, name, config):
        super().__init__(name, config)
        self._session_id = str(uuid.uuid4())

    def begin_call(self):
        self._session_id = str(uuid.uuid4())

    def normalize_native_http_body(self, body, case):
        if not super().normalize_native_http_body(body, case):
            return False
        # Native fetch-use requires a fresh opaque UUID per physical invocation.
        # Normalize only this session token, never task or extraction parameters.
        if 'session_id' in body:
            token = body['session_id']
            if not isinstance(token, str) or str(uuid.UUID(token)) != token:
                raise ValueError('Native fetch session must be a canonical UUID')
            body['session_id'] = '<ephemeral-session-id>'
        return True

    def native_route(self, case):
        route = super().native_route(case)
        if case.task_kind == 'file':
            return NativeRoute('http', self.endpoint, (*route.answer_fields, 'body_base64'))
        return route

    def fetch(self, case, timeout, *, native_route_ledger=None):
        # Direct diagnostic invocations also get a fresh session.
        self.begin_call()
        return super().fetch(case, timeout, native_route_ledger=native_route_ledger)

    def _request(self, case, key, output_format, body):
        body.update(method="GET", output_format=output_format,
                    follow_redirects=True, max_redirects=10,
                    proxy_country=self.config.get("proxy_country", "US"),
                    session_id=self._session_id)
        return {"X-Browser-Use-API-Key": key}

    def _upstream_status(self, payload):
        return payload.get("status_code")

    def _service_error(self, payload, native_file_delivery):
        return bool(payload.get("error")) or (bool(payload.get("is_binary")) and not native_file_delivery)

    @staticmethod
    def _delivered_file(payload):
        binary = payload.get('is_binary') is True
        return ('body_base64' if binary else 'body'), binary

    @staticmethod
    def _file_encoding_valid(payload):
        return type(payload.get('is_binary', False)) is bool

    def _call_metadata(self, body):
        return {"proxy_country": body["proxy_country"]}


class BrowserbaseFetchProvider(HostedFetchProvider):
    endpoint = "https://api.browserbase.com/v1/fetch"
    route_fields = ('content',)
    delivers_files = True
    raw_file_output = True

    def _option_errors(self):
        return [] if isinstance(self.config.get("proxies", False), bool) else ["proxies must be boolean"]

    def _request(self, case, key, output_format, body):
        body.update(format="raw" if case.task_kind == "file" else output_format, allowRedirects=True,
                    proxies=self.config.get("proxies", True))
        return {"X-BB-API-Key": key}

    def _upstream_status(self, payload):
        return payload.get("statusCode", payload.get("status_code"))

    def _service_error(self, payload, native_file_delivery):
        return (payload.get("encoding") == "base64" and not native_file_delivery) or bool(payload.get("error"))

    @staticmethod
    def _delivered_file(payload):
        return 'content', payload.get('encoding') == 'base64'

    @staticmethod
    def _file_encoding_valid(payload):
        return payload.get('encoding', 'utf-8') in ('utf-8', 'base64')

    def _call_metadata(self, body):
        return {"proxies": self.config.get("proxies", True)}


class Crawl4aiCloudProvider(HostedFetchProvider):
    endpoint = "https://api.crawl4ai.com/scrape"
    route_fields = ('markdown',)
    content_field = 'markdown'
    forbidden_error = 'access_denied'

    def _option_errors(self):
        errors = []
        if self.config.get("engine", "browser") not in ("auto", "browser-light", "browser"):
            errors.append("unsupported engine")
        if self.config.get("proxy", "none") not in ("none", "isp", "residential"):
            errors.append("unsupported proxy")
        return errors

    def _request(self, case, key, output_format, body):
        body.update(format="md", engine=self.config.get("engine", "browser"),
                    proxy=self.config.get("proxy", "none"))
        return {"Authorization": f"Bearer {key}"}

    def _upstream_status(self, payload):
        return payload.get("origin_status")

    def _service_error(self, payload, native_file_delivery):
        return bool(payload.get("error")) or payload.get("success") is False or payload.get("ok") is False

    def _record_upstream_status(self, metadata, status):
        metadata["upstream_http_status_available"] = status is not None

    def _envelope_complete(self, status, title):
        return True

    def _call_metadata(self, body):
        return {"request_parameters": body, "native_browser_logs_available": False}


class _ReaderProvider(HostedFetchProvider):
    """Model-vendor fetch tools: no upstream status; the answer carries its fields."""

    title_field = 'title'

    def _record_upstream_status(self, metadata, status):
        metadata["upstream_http_status_available"] = False

    def _envelope_complete(self, status, title):
        return True


class DeepSeekFetchProvider(_ReaderProvider):
    endpoint = "https://api.deepseek.com/v1/tools/fetch"
    route_fields = ('content', 'title')
    forbidden_error = 'security_policy'
    retain_service_error = True

    def _envelope_complete(self, status, title):
        return isinstance(title, str)


class KimiFetchProvider(_ReaderProvider):
    endpoint = "https://api.moonshot.ai/v1/tools/fetch"
    route_fields = ('markdown', 'title', 'url')
    content_field = 'markdown'
    final_url_field = 'url'


class ZaiReaderProvider(_ReaderProvider):
    endpoint = "https://api.z.ai/api/paas/v4/reader"
    route_fields = ('reader_result.content', 'reader_result.title', 'reader_result.url')
    answer_prefix = ('reader_result',)
    final_url_field = 'url'

    def _request(self, case, key, output_format, body):
        body.update({field: self.config[field] for field in (
            'no_cache', 'return_format', 'retain_images', 'no_gfm',
            'keep_img_data_url', 'with_images_summary', 'with_links_summary')
            if field in self.config})
        if case.track in {'image', 'video'} and case.task_kind != 'file':
            body['keep_img_data_url'] = self.config.get(
                'embedded_media_keep_img_data_url', body.get('keep_img_data_url', False))
        return super()._request(case, key, output_format, body)
