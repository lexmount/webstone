"""Deterministic native answer selection, bound before a cohort starts.

Each adapter declares its selection policy as ``native_answer_selection``;
branches depend only on response structure and transport outcome. Acquisition
and saved-evidence checks dispatch to the same hook through the provider
registry, so replay never accepts a later declaration of allowed fields.
"""

import base64
import binascii
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

from lexbench_webfetch.paths import validate_http_url
from lexbench_webfetch.resource_urls import srcset_urls


def _selector(provider_type):
    """The registered adapter's selection hook, or None for an unregistered type."""
    from harness.web_fetch.providers.base import PROVIDERS, provider_class
    if not isinstance(provider_type, str) or provider_type not in PROVIDERS:
        return None
    return provider_class(provider_type).native_answer_selection


def native_projection_source(provider_type):
    """Bytes that define one provider type's answer selection policy.

    The policy is this module plus the adapter module that defines the type's
    ``native_answer_selection`` hook. The bytes are frozen beside evidence as
    data and identified by their SHA-256; they are never executed.
    """
    paths = [Path(__file__)]
    selector = _selector(provider_type)
    if selector is not None:
        owner = Path(sys.modules[selector.__module__].__file__)
        if owner != paths[0]:
            paths.append(owner)
    root = Path(__file__).parent
    return b''.join(b'# native answer policy source: ' + path.relative_to(root).as_posix().encode() + b'\n'
                    + path.read_bytes() for path in paths)


def native_projection_regressions():
    """Executable source-boundary controls owned by this acquisition layer."""
    document = json.loads((Path(__file__).parent / 'data/native-projection-regressions.json').read_text())
    rows = document.get('cases', [])
    indexed = {row['id']: row for row in rows}
    if document.get('schema_version') != 1 or not rows or len(indexed) != len(rows):
        raise ValueError('Invalid native projection regression registry')
    return indexed


# Historical implementations are approved by a caller-selected, hash-bound
# evidence registry, never by a receipt's self-declared version.
_HISTORICAL_POLICIES = ContextVar('webfetch_approved_native_policies', default=None)


@contextmanager
def approved_native_policies(registry):
    """Scope an independently selected registry to one evidence-validation job.

    The caller must verify the registry's immutable path/hash binding first.
    Saved selector bytes are still verified separately and are never executed.
    """
    sources = {}
    if registry is not None:
        if (not isinstance(registry, dict) or set(registry) != {'schema_version', 'sources'}
                or registry['schema_version'] != 1 or not isinstance(registry['sources'], list)):
            raise ValueError('Invalid approved native policy registry')
        for row in registry['sources']:
            if (not isinstance(row, dict) or set(row) != {'source_sha256', 'version', 'reason'}
                    or not isinstance(row['source_sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', row['source_sha256'])
                    or type(row['version']) is not int or row['version'] not in {1, 2, 3, 4, 5, 6, 7}
                    or not isinstance(row['reason'], str) or not row['reason'].strip()
                    or row['source_sha256'] in sources):
                raise ValueError('Invalid or duplicate approved native policy source')
            sources[row['source_sha256']] = row['version']
    token = _HISTORICAL_POLICIES.set(sources)
    try:
        yield
    finally:
        _HISTORICAL_POLICIES.reset(token)


def native_projection_policy_version(policy, provider_type):
    if policy == native_projection_policy(provider_type):
        return 7
    if not isinstance(policy, dict):
        raise ValueError('Native answer selection policy must be an object')
    digest = policy.get('source_sha256')
    version = (_HISTORICAL_POLICIES.get() or {}).get(digest) if isinstance(digest, str) else None
    expected = {'kind': 'native-answer-selection', 'version': version,
                'provider_type': provider_type, 'source_sha256': digest,
                'source_path': 'native-answer-policies/'+str(digest)+'.py'}
    if version is None or policy != expected:
        raise ValueError('Native answer selection policy implementation differs from its pre-call freeze')
    return version


def native_projection_policy(provider_type):
    digest = hashlib.sha256(native_projection_source(provider_type)).hexdigest()
    return {'kind': 'native-answer-selection', 'version': 7,
            'provider_type': provider_type,
            'source_sha256': digest, 'source_path': 'native-answer-policies/'+digest+'.py'}


def json_object(value):
    """The value when it is a JSON object, otherwise an empty object."""
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class NativeSelection:
    """One response presented to an adapter's pre-call answer selection policy.

    Adapters declare ``native_answer_selection(selection)``; it returns the
    projection declaration or None when the response carries no answer.
    """

    provider_type: str
    case: Any
    payload: dict
    status_code: Any
    interface: Any
    policy_version: int

    def get(self, key, default=None):
        if isinstance(self.case, dict):
            return self.case.get(key, default)
        return getattr(self.case, key, default)

    @property
    def track(self):
        return self.get('track', 'web')

    @property
    def task_kind(self):
        return self.get('task_kind', 'body')

    @property
    def successful_http(self):
        return type(self.status_code) is int and 200 <= self.status_code < 300


def native_answer_projection(provider_type, case, payload, status_code=None, *, interface=None, policy_version=7):
    """Return the declaration selected by the adapter's pre-call structural policy."""
    if type(policy_version) is not int or policy_version not in {1, 2, 3, 4, 5, 6, 7}:
        raise ValueError('Unknown native answer selection policy version')
    selector = _selector(provider_type)
    if selector is None:
        raise ValueError('Unsupported native answer selection policy: ' + str(provider_type))
    return selector(NativeSelection(provider_type, case, json_object(payload), status_code,
                                    interface, policy_version))


def native_field_value(payload, pointer):
    if not isinstance(pointer, list) or not pointer:
        raise ValueError('Native answer projection requires a nonempty field pointer')
    value = payload
    try:
        for key in pointer:
            if isinstance(value, dict) and isinstance(key, str):
                value = value[key]
            elif isinstance(value, list) and type(key) is int and key >= 0:
                value = value[key]
            else:
                raise ValueError('Native answer pointer is not a structural field selection')
    except (KeyError, IndexError) as error:
        raise ValueError('Native answer field is missing') from error
    return value


SRCSET_PARSER = 'html-candidates-v1'


@lru_cache(maxsize=1)
def _historical_srcset_parsers():
    registry = json.loads((Path(__file__).parent / 'data/native-projection-history.json').read_text())
    if registry.get('schema_version') != 2:
        raise ValueError('Unsupported native projection history schema')
    parsers = {source: entry['parser'] for source, entry in registry['scoring_sources'].items()}
    if any(parser not in {SRCSET_PARSER, 'legacy-comma-split'} for parser in parsers.values()):
        raise ValueError('Unsupported historical srcset parser')
    return parsers


def _srcset_urls(value, parser, historical_scoring_source):
    # Source identities bind the historical projection, never the resulting answer.
    historical = _historical_srcset_parsers().get(historical_scoring_source)
    if parser is not None:
        if parser != SRCSET_PARSER or historical not in {None, parser}:
            raise ValueError('Native srcset parser differs from its bound semantics')
    else:
        parser = historical
    if parser == SRCSET_PARSER:
        return srcset_urls(value)
    if parser == 'legacy-comma-split':
        return [part.strip().split()[0] for part in value.split(',') if part.strip()]
    raise ValueError('Native srcset projection lacks a recognized parser binding')


def _attribute_record_urls(groups, base_url, *, parser=None, historical_scoring_source=None):
    """Project declared HTML URL attributes without classifying their targets."""
    urls = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        elements = group.get('results')
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            attributes = element.get('attributes')
            for attribute in attributes if isinstance(attributes, list) else []:
                if not isinstance(attribute, dict):
                    continue
                name, value = attribute.get('name'), attribute.get('value')
                if not isinstance(name, str) or not isinstance(value, str):
                    continue
                name = name.casefold()
                if name not in {'src', 'srcset', 'href', 'data-src', 'data-original'}:
                    continue
                values = (_srcset_urls(value, parser, historical_scoring_source)
                          if name == 'srcset' else [value])
                for raw in values:
                    try:
                        urls.append(urljoin(base_url, raw.strip()))
                    except ValueError:
                        # Retain malformed native values; the shared scorer,
                        # not a provider adapter, decides URL eligibility.
                        urls.append(raw)
    return sorted(set(urls))


SCORE_ANSWER_FIELDS = frozenset({'links', 'images', 'videos', 'media', 'extras', 'page_structure', 'records', 'page_information'})


def _selected(payload, spec):
    # Missing native fields may mean no answer, never a synthesized answer.
    _require_field_spec(spec, 'native field')
    if 'default' in spec and spec['default'] not in (None, '', [], {}):
        raise ValueError('Native answer defaults must be empty')
    try:
        return native_field_value(payload, spec['pointer'])
    except ValueError:
        if 'default' not in spec:
            raise
        return spec['default']


def _project_field(payload, spec, *, historical_scoring_source=None):
    value = _selected(payload, spec)
    kind = spec.get('format', 'identity')
    if kind == 'identity':
        return value
    if kind == 'text_or_none':
        return value if isinstance(value, str) else None
    if kind == 'text_or_text_list':
        return value if isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)) else None
    if kind == 'text_items':
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]
        return [item if isinstance(item, str) else item['text']
                for item in items if isinstance(item, str) or
                isinstance(item, dict) and isinstance(item.get('text'), str)]
    if kind == 'joined_text_items':
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return None
        return ' '.join(value)
    if kind == 'object_rows':
        return [{name: _project_field(row, child, historical_scoring_source=historical_scoring_source)
                 for name, child in spec['fields'].items()} for row in value if isinstance(row, dict)
                ] if isinstance(value, list) else []
    if kind == 'object_fields':
        return {name: _project_field(value, child, historical_scoring_source=historical_scoring_source)
                for name, child in spec['fields'].items()}
    if kind == 'html_attribute_urls':
        return _attribute_record_urls(value, spec['base_url'], parser=spec.get('srcset_parser'),
                                      historical_scoring_source=historical_scoring_source)
    if kind == 'named_links':
        return [{'text': item['anchor'], 'href': item['url']}
                for item in value if isinstance(item, dict)
                and isinstance(item.get('anchor'), str) and item['anchor'].strip()
                and isinstance(item.get('url'), str) and item['url'].strip()]
    if kind == 'video_values':
        values = [item for item in value if isinstance(item, (str, dict))] if isinstance(value, list) else []
        legacy = _selected(payload, spec['legacy'])
        return values + ([legacy] if isinstance(legacy, str) and legacy else [])
    raise ValueError('Unknown native answer field projection')


def require_native_projection_schema(declaration):
    """Keep historical answer scope separate from schema-2 result context."""
    if not isinstance(declaration, dict):
        raise ValueError('Native projection declaration must be an object')
    if type(declaration.get('schema_version')) is not int or declaration['schema_version'] not in {1, 2}:
        raise ValueError('Unsupported native answer projection')
    if declaration['schema_version'] == 1 and 'result_fields' in declaration:
        raise ValueError('Native result_fields require projection schema 2')
    for group, allowed in [('answer_fields', SCORE_ANSWER_FIELDS),
                           ('result_fields', {'title', 'final_url'})]:
        fields = declaration.get(group, {})
        if not isinstance(fields, dict):
            raise ValueError(group + ' must be an object')
        for name, spec in fields.items():
            field = group + '.' + str(name)
            if name not in allowed:
                raise ValueError('Unknown native projection field: ' + field)
            _require_field_spec(spec, field)
    if 'answer_format' in declaration and declaration['answer_format'] not in {'plain_text', 'markdown', 'html'}:
        raise ValueError('Invalid native answer representation')
    if declaration.get('files') is not None:
        _require_field_spec(declaration['files'], 'files')


def _require_field_spec(spec, field):
    """Reject malformed declarations with a field location, before reading data."""
    if not isinstance(spec, dict):
        raise ValueError(field + ' must be an object')
    if 'srcset_parser' in spec and (spec.get('format') != 'html_attribute_urls'
                                    or spec['srcset_parser'] != SRCSET_PARSER):
        raise ValueError(field + '.srcset_parser is unsupported')
    if 'format' in spec and not isinstance(spec['format'], str):
        raise ValueError(field + '.format must be text')
    if 'pointer' not in spec and spec.get('format', 'identity') not in {
            'request_url', 'fetch_log_final_url', 'transport_response_url', 'raw_bytes'}:
        raise ValueError(field + '.pointer is required')
    if 'pointer' in spec:
        pointer = spec['pointer']
        if (not isinstance(pointer, list) or not pointer
                or any(not isinstance(key, str) and not (type(key) is int and key >= 0)
                       for key in pointer)):
            raise ValueError(field + '.pointer must be a nonempty list of names or nonnegative indexes')
    if spec.get('format') in {'object_fields', 'object_rows'}:
        if not isinstance(spec.get('fields'), dict):
            raise ValueError(field + '.fields must be an object')
        for name, child in spec['fields'].items():
            _require_field_spec(child, field + '.fields.' + name)
    if 'legacy' in spec:
        _require_field_spec(spec['legacy'], field + '.legacy')


def project_native_answer(payload, declaration, *, historical_scoring_source=None):
    """Select declared native fields; never inspect references or URL meaning."""
    require_native_projection_schema(declaration)
    kind = declaration.get('content_format')
    if declaration.get('source_format') == 'bytes':
        if declaration.get('answer_fields'):
            raise ValueError('Native byte projection cannot declare structured answer fields')
        if not isinstance(payload, bytes):
            raise ValueError('Native byte projection requires bytes')
        if kind == 'utf8':
            try:
                return payload.decode('utf-8'), {}
            except UnicodeDecodeError:
                # Invalid text remains raw evidence, never repaired answer text.
                return '', {}
        if kind == 'empty':
            return '', {}
        raise ValueError('Unknown native byte projection')
    if kind == 'empty':
        content = ''
    else:
        value = _selected(payload, {'pointer': declaration.get('content_pointer'),
                                  **({'default': ''} if declaration.get('optional_content') else {})})
        if kind == 'text' and isinstance(value, str):
            content = value
        elif kind == 'json':
            content = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        else:
            raise ValueError('Native answer field differs from its declared type')
    fields = ({'answer_format': declaration['answer_format']} if 'answer_format' in declaration else {})
    for name, spec in declaration.get('answer_fields', {}).items():
        if name not in SCORE_ANSWER_FIELDS:
            raise ValueError('Unknown native answer metadata field')
        fields[name] = _project_field(payload, spec, historical_scoring_source=historical_scoring_source)
    return content, fields


def project_native_files(payload, declaration):
    """Decode native delivered bytes; filenames and logs cannot supply answers."""
    spec = declaration.get('files')
    if spec is None:
        return []
    if not isinstance(spec, dict):
        raise ValueError('Native file projection must be an object')
    kind = spec.get('format')
    if kind not in {'raw_bytes', 'base64_file', 'utf8_file', 'worker_files'}:
        raise ValueError('Unknown native file projection')
    if kind == 'raw_bytes':
        if not isinstance(payload, bytes):
            raise ValueError('Native raw file projection requires bytes')
        return [payload] if payload else []
    value = _selected(payload, spec)
    if kind == 'utf8_file':
        if not isinstance(value, str):
            raise ValueError('Native UTF-8 file projection requires text')
        return [value.encode('utf-8')] if value else []
    if kind == 'base64_file':
        values = [value] if value else []
        if not isinstance(value, str):
            raise ValueError('Native base64 file projection requires text')
    else:
        if not isinstance(value, list) or any(
            not isinstance(item, dict) or not isinstance(item.get('base64'), str)
            for item in value
        ):
            raise ValueError('Native worker files require objects with base64 text')
        values = [item['base64'] for item in value]
    try:
        return [base64.b64decode(item, validate=True) for item in values]
    except (binascii.Error, ValueError) as error:
        raise ValueError('Native file projection contains invalid base64') from error


def native_fetch_diagnostics(stderr: bytes, requested_url: str) -> tuple[int | None, bool]:
    """Read target HTTP events only through the captured document's lifetime.

    A pending-navigation snapshot can precede renderer shutdown and a late
    replacement commit. A unique page-turn/capture/close sequence binds that
    cutoff; without it, later commits keep legacy ownership ambiguous.
    """
    prefix = rb"(?:^|\n)(?:\d{4}-\d{2}-\d{2}T[^\s]+\s+)?"
    captures = list(re.finditer(prefix + rb"DEBUG capturing page state while a location navigation is pending\b[^\r\n]*", stderr))
    if len(captures) == 1:
        capture = captures[0]
        turns = list(re.finditer(prefix + rb"DEBUG completed one ordinary Page turn\b[^\r\n]*\bpage_id=(\d+)\b", stderr[:capture.start()]))
        if turns and len({row.group(1) for row in turns}) == 1:
            page = turns[-1].group(1)
            closes = list(re.finditer(prefix + rb"DEBUG closing renderer page handle page_id=" + re.escape(page) + rb" requested_execution_interrupt=true\b", stderr[capture.end():]))
            if len(closes) == 1:
                stderr = stderr[:capture.start()]
    rows = list(re.finditer(prefix + rb"DEBUG fetch request complete method=(?:GET|POST) url=(?P<url>\S+) final_url=\S+ status=(?P<status>\d{3})\b[^\r\n]*", stderr))
    matches = [row for row in rows if row.group('url') == requested_url.encode('utf-8')]
    status = None
    if len(matches) == 1:
        row = matches[0]
        value = int(row.group('status'))
        if not 300 <= value < 400 and not any(marker in stderr[row.end():] for marker in (
                b'committed main navigation before replacement realm bootstrap',
                b'detached committed main WindowProxy for replacement context')):
            status = value
    waits = re.finditer(prefix + rb"WARN fetch readiness wait timed out; returning best-effort page page_id=\d+ url=(?P<url>\S+) final_url=\S+ wait_until=(?:DomStable|NetworkIdle) timeout_ms=[1-9]\d* remaining_ms=\d+ error=[^\r\n]+", stderr)
    return status, any(row.group('url') == requested_url.encode('utf-8') for row in waits)


def main_document_url_from_fetch_log(stderr: str | bytes, requested_url: str) -> str | None:
    """Replay the effective main-document URL from the same call's debug log."""
    pattern = re.compile(
        rb"fetch request complete method=(?:GET|POST) url=(?P<url>\S+) "
        rb"final_url=(?P<final_url>\S+) status=(?P<status>\d{3})\b"
    )

    diagnostic = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
    navigation_pattern = re.compile(
        rb"following pending location navigation asynchronously[^\n]* url=(?P<url>\S+)"
    )

    def field(row: re.Match[bytes], name: str) -> str:
        return row.group(name).decode("utf-8", "replace")

    def identity(value: str) -> tuple | None:
        try:
            validate_http_url(value)
            parts = urlsplit(value)
            return (
                parts.scheme.casefold(),
                (parts.hostname or "").casefold(),
                parts.port if parts.port is not None else (443 if parts.scheme.casefold() == "https" else 80),
                re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(),
                       quote(parts.path, safe="/%:@!$&'()*+,;=")).rstrip("/") or "/",
                sorted(parse_qsl(parts.query, keep_blank_values=True)),
            )
        except ValueError:
            return None

    requested_identity = identity(requested_url)
    if requested_identity is None:
        return None
    rows = list(pattern.finditer(diagnostic))

    # Legacy completion records do not identify redirect hops or request owners.
    # Never infer the document from GET ordering or challenge query parameters.
    row = next((candidate for candidate in rows
                if identity(field(candidate, "url")) == requested_identity), None)
    if row is None or 300 <= int(field(row, "status")) < 400:
        return None
    navigations = list(navigation_pattern.finditer(diagnostic))
    for index, navigation in enumerate(navigations):
        boundary = navigations[index + 1].start() if index + 1 < len(navigations) else len(diagnostic)
        committed = diagnostic.find(
            b"committed main navigation before replacement realm bootstrap",
            navigation.end(), boundary,
        )
        if committed < 0:
            continue
        navigation_identity = identity(field(navigation, "url"))
        if navigation_identity is None:
            return None
        matches = [candidate for candidate in rows
                   if navigation.end() <= candidate.start() < boundary
                   and identity(field(candidate, "url")) == navigation_identity]
        if len(matches) != 1 or 300 <= int(field(matches[0], "status")) < 400:
            return None
        row = matches[0]
    candidate = field(row, "final_url")
    if identity(candidate) is None:
        return None
    try:
        validate_http_url(candidate)
    except ValueError:
        return None
    return candidate


def project_native_result_fields(payload, declaration, *, request_url=None,
                                 diagnostic=None, transport_receipt=None):
    """Replay scored title/address from native fields or same-call evidence."""
    require_native_projection_schema(declaration)
    fields = {'title': None, 'final_url': None}
    for name, spec in declaration.get('result_fields', {}).items():
        if name not in fields:
            raise ValueError('Unknown native result field')
        kind = spec.get('format', 'identity')
        if kind == 'request_url':
            if name != 'final_url' or request_url is None:
                raise ValueError('Native URL fallback requires its declared request URL')
            value = request_url
        elif kind == 'fetch_log_final_url':
            if name != 'final_url' or diagnostic is None or request_url is None:
                raise ValueError('Native final URL lacks its same-call diagnostic evidence')
            value = main_document_url_from_fetch_log(diagnostic, request_url)
        elif kind == 'transport_response_url':
            if name != 'final_url' or not transport_receipt:
                raise ValueError('Native final URL lacks its source transport receipt')
            value = transport_receipt['response_url']
        else:
            # Schema 2 historically treats result context as optional. Preserve
            # missing-field replay, but never overwrite an explicit default.
            value = _selected(payload, {'default': None, **spec})
            if kind == 'text_or_none':
                value = value if isinstance(value, str) else None
            elif kind != 'identity':
                raise ValueError('Unknown native result field projection')
            if spec.get('nonempty') and not value:
                value = None
            if not value and spec.get('fallback') == 'request_url':
                if name != 'final_url' or request_url is None:
                    raise ValueError('Native URL fallback requires its declared request URL')
                value = request_url
        fields[name] = value
    return fields


def require_native_answer_projection(content, metadata, raw_response, *, files=(), title=None, final_url=None,
                                     request_url=None, diagnostic=None, transport_receipt=None,
                                     historical_scoring_source=None):
    """Reject every undeclared score-bearing input, then check exact provenance."""
    declaration = metadata.get('native_answer_projection')
    present = {key for key in SCORE_ANSWER_FIELDS if metadata.get(key)}
    if declaration is None:
        if (present or files or title is not None
                or (final_url is not None and final_url != request_url)
                or (raw_response is not None and content)):
            raise ValueError('Scored native answer has undeclared answer fields')
        return
    require_native_projection_schema(declaration)
    if raw_response is None:
        raise ValueError('Declared native answer lacks its retained raw response')
    if present - set(declaration.get('answer_fields', {})):
        raise ValueError('Scored native answer has undeclared answer fields')
    allowed = metadata.get('native_route_receipt', {}).get('answer_fields')
    if allowed is not None:
        def check_source(spec, prefix=()):
            pointer = [*prefix, *spec.get('pointer', [])]
            if spec.get('format') in {'object_fields', 'object_rows'}:
                for child in spec['fields'].values():
                    check_source(child, pointer)
                return
            if pointer:
                name = '.'.join(str(key) for key in pointer if not isinstance(key, int))
                allowed_names = [field.replace('[]', '') for field in allowed]
                if not any(name == field or name.startswith(field+'.') for field in allowed_names):
                    raise ValueError('Native projection reads outside its frozen answer fields')
            if 'legacy' in spec:
                check_source(spec['legacy'], prefix)
        if declaration.get('source_format') == 'bytes':
            required = ('original_bytes' if (declaration.get('files') or
                        (declaration.get('content_format') == 'empty' and 'original_bytes' in allowed)) else
                        'response_body' if 'response_body' in allowed
                        else 'stdout')
            if required not in allowed:
                raise ValueError('Native byte projection differs from its frozen answer fields')
        else:
            if declaration.get('content_format') != 'empty':
                check_source({'pointer': declaration.get('content_pointer')})
            for spec in declaration.get('answer_fields', {}).values():
                check_source(spec)
            if declaration.get('files'):
                check_source(declaration['files'])
        for spec in declaration.get('result_fields', {}).values():
            check_source(spec)
            if spec.get('format') == 'fetch_log_final_url' and 'diagnostic.main_document_url' not in allowed:
                raise ValueError('Native final URL conversion is outside its frozen sources')
    payload = raw_response if declaration.get('source_format') == 'bytes' else json.loads(raw_response)
    projected_result = project_native_result_fields(payload, declaration, request_url=request_url,
        diagnostic=diagnostic, transport_receipt=transport_receipt)
    if projected_result != {'title': title, 'final_url': final_url}:
        raise ValueError('Scored title or final URL differs from its declared native projection')
    if declaration.get('schema_version') == 2:
        for spec in declaration.get('answer_fields', {}).values():
            if spec.get('format') == 'html_attribute_urls' and spec.get('base_url') != (final_url or request_url):
                raise ValueError('Native resource base URL differs from its verified result URL')
    expected, fields = project_native_answer(payload, declaration, historical_scoring_source=historical_scoring_source)
    if content != expected or any(metadata.get(key) != value for key, value in fields.items()):
        raise ValueError('Scored answer differs from its declared native field projection')
    if [item['data'] for item in files] != project_native_files(payload, declaration):
        raise ValueError('Scored native files differ from their declared byte projection')
