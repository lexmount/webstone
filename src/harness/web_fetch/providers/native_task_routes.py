"""Native task-route contracts of the browser-engine adapters.

Each engine's route, extractor and child-document metadata is adapter
knowledge, so the report-publication check that enforces it lives with the
adapters rather than in the shared projections of ``task_outputs``.
"""

import hashlib
from types import SimpleNamespace

from harness.web_fetch.methods import native_browser_engine
from harness.web_fetch.providers.headless import output_mode_for_case
from harness.web_fetch.retrieval_hints import NATIVE_RETRIEVAL_HINTS, native_contract_for_case
from harness.web_fetch.task_outputs import (
    DOCUMENT_PAGE_EXPRESSION,
    MEDIA_METADATA_EXPRESSION,
    RESOURCE_PAGE_EXPRESSION,
    resource_detail_links,
    resource_expression,
)
from harness.web_fetch.report_model import MethodScope


def require_native_task_routes(cases, records):
    """Reject substitutions and stale task adapters before report publication."""

    inventory = {case['id']: SimpleNamespace(id=case['id'], url=case['url'], track=case.get('track','web'), task_kind=case.get('task_kind','body'), retrieval_hints=case.get('retrieval_hints',{}), source_benchmark=case.get('source_benchmark',{}), expected_resources=[{'kind':r.get('kind')} for r in case.get('expected_resources',[])]) for case in cases}
    expressions = {'resource':RESOURCE_PAGE_EXPRESSION, 'media_metadata':MEDIA_METADATA_EXPRESSION,
                   'document_page':DOCUMENT_PAGE_EXPRESSION}
    failures = []
    for row in records:
        provider = (
            'moli'
            if row.get('provider_type') == 'moli_webfetch'
            else native_browser_engine(row['provider'], scope=MethodScope())
        )
        if provider not in {'moli','lightpanda','obscura','chromium','kitesurf'}:
            failures.append(row['provider'] + '/' + row['case_id'])
            continue
        if row.get('error_type') == 'configuration':
            failures.append(row['provider'] + '/' + row['case_id'])
            continue
        case = inventory[row['case_id']]
        mode = output_mode_for_case(provider, 'headless_markdown', case)
        native_contract = native_contract_for_case(case)
        meta = row.get('provider_metadata') or {}
        native_dynamic_task = (
            provider == 'moli'
            and case.track == 'dynamic'
            and case.retrieval_hints == NATIVE_RETRIEVAL_HINTS
        )
        # The runner may expire before a worker returns metadata. Its retained
        # call and cohort receipts still bind the invocation and deadline.
        runner_fields = {
            'call_log', 'configured_min_interval_seconds', 'client_throttle_wait_ms',
            # A declared retry records the completed physical calls but cannot
            # add route metadata when every call expires in the runner.
            'retry',
        }
        if row.get('ok') is False and row.get('error_type') in {'timeout','network'} and meta.get('call_log') and not (set(meta) - runner_fields):
            continue
        valid = True
        if mode == 'markdown':
            # Page-body tasks have one formal deliverable: Markdown.  Every
            # non-Chromium method must obtain it from its browser's native
            # Markdown route; Chromium is the sole declared shared-converter
            # exception.  Older Moli evidence did not spell out the two
            # redundant metadata fields, so its native route/output pair is
            # also the backward-compatible proof of this contract.
            standard_body = case.track not in {'document', 'image', 'video'}
            if standard_body:
                declared_contract = meta.get('output_contract')
                if declared_contract not in {None, 'markdown'}:
                    valid = False
            if provider == 'moli':
                native = (meta.get('route') == 'ordinary_markdown'
                          and meta.get('output') == 'markdown')
                if meta.get('route') == 'dynamic_native_markdown':
                    native = (case.track == 'dynamic' and meta.get('output') == 'markdown'
                              and meta.get('dump') == 'markdown')
                valid = valid and native and meta.get('extractor') in {None, 'moli_native_markdown'}
                if native_dynamic_task:
                    wait = meta.get('wait') or {}
                    layout = wait.get('layout') or {}
                    valid = valid and (
                        meta.get('layout') is True
                        and meta.get('with_frames') is True
                        and meta.get('wait_until') == 'domstable'
                        and wait.get('wait_until') == 'domstable'
                        and wait.get('scope') == 'dynamic_task_family'
                        and wait.get('strategy') == 'native_task_family_completion'
                        and layout == {
                            'requested': True,
                            'support': 'native',
                            'interface': '--layout',
                            'scope': 'dynamic_task_family',
                        }
                        and not set(wait).intersection({
                            'selector', 'frame_selector', 'minimum_count',
                            'minimum_text_fragments', 'output_extraction',
                        })
                    )
            elif provider == 'chromium':
                valid = valid and (meta.get('extractor') == 'node-html-markdown'
                                   and (meta.get('extractor_version') == '2.0.0'
                                        or (row.get('ok') is False and meta.get('extractor_version') is None)))
            elif provider in {'lightpanda','obscura'}:
                valid = valid and meta.get('extractor') == provider+'_native_markdown'
            elif provider == 'kitesurf':
                routes = {'browser_rendering_markdown'}
                if case.track == 'dynamic':
                    routes.add('browser_rendering_markdown_dynamic')
                valid = valid and (meta.get('route') in routes
                                   and meta.get('extractor') == 'cloudflare_native_markdown'
                                   and meta.get('browser') == 'kitesurf')
        elif mode in {'document','file'}:
            if provider == 'moli':
                valid = (meta.get('route') == ('raw_document' if mode == 'document' else 'raw_file')
                         and meta.get('extractor') == 'moli_cdp_response' + ('+document_text' if mode == 'document' else ''))
            else:
                prefixes = {'lightpanda':'lightpanda_cdp_response', 'obscura':'obscura_original_response',
                            'chromium':'browser_native_response_or_download', 'kitesurf':'kitesurf_cdp_response'}
                valid = meta.get('extractor') == prefixes[provider] + ('+document_text' if mode == 'document' else '')
            valid = valid and meta.get('route') != 'workers_ai_tomarkdown'
        elif mode == 'resource' and provider == 'moli':
            valid = (
                meta.get('route') == 'resource_native_markdown'
                and meta.get('output') == 'native_markdown_resource_urls'
                and meta.get('output_contract') == 'native_markdown_resource_urls'
                and meta.get('dump') == 'markdown'
                and meta.get('extractor') == 'moli_native_markdown'
                and not meta.get('network_trace_requested')
            )
            valid = valid and not meta.get('output_projection_sha256')
        elif mode in expressions:
            expression = resource_expression(case.track, resource_detail_links(case)) if mode == 'resource' else expressions[mode]
            valid = meta.get('output_projection_sha256') == hashlib.sha256(expression.encode()).hexdigest()
            if provider == 'kitesurf':
                valid = (valid and meta.get('browser') == 'kitesurf'
                         and meta.get('endpoint_route') == '/browser-run/devtools/browser?browser=kitesurf')
        if case.track == 'document':
            valid = valid and meta.get('document_extractor') in {None, 'pdfjs_runtime', 'rendered_page_text'}
            valid = valid and '+document_text' not in str(meta.get('extractor', ''))
        frame_contract = (
            native_dynamic_task
            or (provider == 'moli' and case.task_kind == 'resource')
            or bool(native_contract and native_contract.include_subdocuments)
        )
        frame_metadata = (
            (meta.get('wait') or {}).get('subdocuments')
            if provider == 'moli'
            else meta.get('subdocuments')
        )
        # A family-wide provider option may request native child documents
        # without adding a case-specific completion/selector contract.
        optional_native_family_frames = (
            not frame_contract
            and provider == 'lightpanda' and case.track == 'dynamic'
            and mode == 'markdown' and frame_metadata is not None
        )
        if optional_native_family_frames:
            valid = valid and meta.get('output_contract') == 'markdown'
        if frame_contract or optional_native_family_frames:
            expected = {
                'moli': ('native', '--with-frames'),
                'lightpanda': (
                    'native_markdown_omits_child_document',
                    '--load-resources iframe + --with-frames',
                ),
            }.get(provider, ('unsupported', None))
            expected_frame_metadata = {
                'requested': True,
                'support': expected[0],
                'interface': expected[1],
                'harness_content_assembly': False,
            }
            if isinstance(frame_metadata, dict):
                extra = set(frame_metadata) - set(expected_frame_metadata)
                valid = valid and not extra - {'scope'}
                valid = valid and all(
                    frame_metadata.get(key) == value
                    for key, value in expected_frame_metadata.items()
                )
                if 'scope' in frame_metadata:
                    expected_scope = (
                        'dynamic_task_family'
                        if native_dynamic_task or optional_native_family_frames
                        else 'resource_task_family'
                        if case.task_kind == 'resource'
                        else 'declared_native_contract'
                    )
                    valid = valid and frame_metadata['scope'] == expected_scope
            else:
                valid = False
        elif frame_metadata is not None:
            valid = False
        if not valid:
            failures.append(row['provider'] + '/' + case.id)
    if failures:
        raise ValueError('Native task route contract violated: ' + ', '.join(failures))
