"""Combine validated saved comparisons without changing their measured records."""
from copy import deepcopy
import json
import shutil
import re
from collections import Counter
from pathlib import Path

from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.record_io import file_sha256 as _hash


def _frozen_report(root, binding):
    """Verify a published tree without reinterpreting its historical verdicts."""
    audit = root / 'saved-matrix-audit.json'
    if _hash(audit) != binding['sha256']:
        raise ValueError('Frozen report audit changed')
    files = _read(audit)['files']
    inventory = list(root.rglob('*'))
    if root.is_symlink() or any(p.is_symlink() for p in inventory):
        raise ValueError('Frozen reports reject symlinks')
    actual = {p.relative_to(root).as_posix() for p in inventory if p.is_file()}
    if actual != set(files) | {'saved-matrix-audit.json'}:
        raise ValueError('Frozen report file inventory changed')
    for name, digest in files.items():
        if _hash(root / name) != digest:
            raise ValueError('Frozen report file changed: ' + name)
    return _read(root / 'matrix.json')


def _namespace_extension_evidence(text):
    """Qualify lazy sources only in views rendered beneath the outer report root."""
    if re.search(r'\bdata-record=', text):
        raise ValueError('Nested report requires explicit evidence-file bindings')
    return re.sub(r'\bdata-file="([^"]+)"', r'data-file="additional/\1"', text)


def _restore_extension_evidence(text):
    """Recover only the namespace projection used by frozen older extensions."""
    return re.sub(r'\bdata-file="additional/([^"]+)"', r'data-file="\1"', text)


def _extended_home(base, addition, base_model, added_model, overview, note, *, ranked=False, presentation=None):
    """Use saved comparison cells; never recompute scores across scorer editions."""
    from bs4 import BeautifulSoup
    from html import escape
    old = BeautifulSoup(base, 'html.parser')
    new = BeautifulSoup(addition, 'html.parser')
    family_order = [f['id'] for f in base_model['report_families']]
    added_order = [f['id'] for f in added_model['report_families']]
    if set(family_order) != set(added_order):
        raise ValueError('Report extensions require the same task families')
    for identifier in ['ranking-overall', *('ranking-' + f for f in family_order)]:
        target, source = old.find('table', id=identifier), new.find('table', id=identifier)
        if target is None or source is None:
            raise ValueError('Missing comparison table: ' + identifier)
        if ranked:
            continue
        # An unranked source has no index column. Add one before merging so
        # the same schema preserves provider links and all saved metric cells.
        for table in (target, source):
            if table.get('data-row-index') == 'false':
                table.thead.tr.insert(0, old.new_tag('th'))
                if table.has_attr('data-col'):
                    table['data-col'] = str(int(table['data-col']) + 1)
                for row in table.tbody.find_all('tr', recursive=False):
                    row.insert(0, old.new_tag('td'))
        if identifier == 'ranking-overall':
            # Sortable headers may wrap their labels in a button.
            unknown_headers = [next((h for h in table.select('thead th')
                                    if re.search('无法判定|Undecidable', h.get_text())), None)
                               for table in (target, source)]
            if any(h is not None for h in unknown_headers):
                header = next(h for h in unknown_headers if h is not None)
                for table, present in zip((target, source), unknown_headers, strict=True):
                    if present is None:
                        table.thead.tr.insert(4, deepcopy(header))
                        for row in table.tbody.find_all('tr', recursive=False):
                            cell = old.new_tag('td', attrs={'data-sort': '0'})
                            cell.string = '0'
                            row.insert(4, cell)
        old_rows, new_rows = target.tbody.find_all('tr', recursive=False), source.tbody.find_all('tr', recursive=False)
        if len(old_rows) != len(base_model['providers']) or len(new_rows) != len(added_model['providers']):
            raise ValueError('Comparison table provider population differs')
        target['data-row-index'] = 'false'
        target.thead.tr.find(['th', 'td']).string = '序号'
        extra_review = False
        if identifier == 'ranking-overall':
            target_headers = target.thead.tr.find_all('th', recursive=False)
            source_headers = source.thead.tr.find_all('th', recursive=False)
            if len(source_headers) == len(target_headers) + 1 and source_headers[-1].get_text().startswith('审查结果'):
                target.thead.tr.append(source_headers[-1])
                extra_review = True
            elif len(source_headers) != len(target_headers):
                raise ValueError('Incompatible overall comparison metrics')
        for index, row in enumerate(old_rows, 1):
            row.find('td').string = str(index)
            if extra_review:
                cell = old.new_tag('td')
                link = old.new_tag('a', href='review/index.html')
                link.string = '核验记录'
                cell.append(link)
                row.append(cell)
        for index, row in enumerate(new_rows, len(old_rows) + 1):
            cells = row.find_all('td', recursive=False)
            if identifier == 'ranking-overall':
                if len(cells) != len(source_headers):
                    raise ValueError('Unexpected overall metric columns')
                offset = 4 + int(any(h is not None for h in unknown_headers))
                reordered = cells[:offset] + [cells[offset + added_order.index(f)] for f in family_order] + cells[offset + len(added_order):]
                row.clear()
                for cell in reordered:
                    row.append(cell)
            row.find('td').string = str(index)
            for link in row.find_all('a', href=True):
                link['href'] = 'additional/' + link['href']
            target.tbody.append(row)
        # Values remain sortable, but mixed scorer editions have no shared best.
        for cell in target.find_all(True):
            if 'class' in cell.attrs:
                cell['class'] = [c for c in cell['class'] if c not in {'good', 'bad'} and not re.search(r'best|worst|winner', c)]
        for emphasis in target.find_all('strong'):
            emphasis.unwrap()
        for index, header in enumerate(target.select('thead th')):
            button = header.find('button', class_='sort-button')
            if button:
                button['onclick'] = f"sortTable('{identifier}',{index})"
    if not ranked:
        explanation = old.new_tag('p', attrs={'class': 'source-note', 'data-composition-scope': 'scoring'})
        explanation.string = note
        old.find('table', id='ranking-overall').parent.insert_before(explanation)
    methods = old.find('table', id='method-overview')
    if methods is None:
        raise ValueError('Published base has no method overview')
    for method in overview:
        row = old.new_tag('tr', attrs={'data-added-method': method['id']})
        settings = '<p>' + escape(method['configuration']) + '</p>'
        if method.get('configurations'):
            settings = '<dl class="method-settings">' + ''.join(
                '<div><dt>' + escape(group['category']) + '</dt><dd><ul class="method-parameters">'
                + ''.join('<li><span>' + escape(parameter['label']) + '</span><code>'
                    + escape(parameter['name'] + '=' + json.dumps(parameter['value'], ensure_ascii=False, separators=(',', ':')))
                    + '</code></li>' for parameter in group['parameters'])
                + '</ul>' + ('<p>' + escape(group['note']) + '</p>' if group.get('note') else '')
                + '</dd></div>' for group in method['configurations']) + '</dl>'
        sources = ' · '.join('<a href="' + escape(s['url'], quote=True) + '">' + escape(s['label']) + '</a>' for s in method['sources'])
        fragment = BeautifulSoup('<td><a href="additional/providers/' + escape(method['id']) + '.html">' + escape(method['label']) + '</a><small class="method-execution">' + escape(method['execution']) + '</small></td><td>' + settings + '</td><td>' + escape(method['price']) + '<p>' + escape(method['billing']) + '</p>' + sources + '</td>', 'html.parser')
        for cell in list(fragment.contents):
            row.append(cell)
        methods.tbody.append(row)
    heading = old.find(id='overall-ranking')
    if heading and heading.find('h2'):
        heading.find('h2').string = '各方法已保存结果'
    before_count = len(base_model['providers'])
    after_count = before_count + len(added_model['providers'])
    for node in old.find_all(string=True):
        if node.parent.get('data-composition-scope'):
            continue
        text = str(node)
        updated = re.sub(r'\b' + str(before_count) + r' 种(已测)?方法',
                         lambda match: str(after_count) + ' 种' + (match[1] or '') + '方法', text)
        updated = updated.replace('按本轮任务通过率排序。', '保留各轮已保存结果；评分口径见下方说明。')
        if updated != text:
            node.replace_with(updated)
    entry = old.new_tag('p', attrs={'class': 'source-note', 'data-added-report': 'true'})
    entry.append(BeautifulSoup('<a href="additional/index.html">查看方法详情与逐例证据 →</a>', 'html.parser'))
    methods.parent.insert_after(entry)
    if ranked:
        _unified_home(old, base_model, added_model, presentation)
    return str(old)


def _rank_extension_table(table, score_column, *, minimize=(), maximize=(), ascending=False):
    """Use the report owner's ranking and extrema implementation on saved cells."""
    from bs4 import BeautifulSoup
    from harness.web_fetch.report import comparative_rows, shared_ranks, RenderContext, _RENDER_CONTEXT
    for emphasis in table.select('strong'):
        emphasis.unwrap()
    rows = table.tbody.find_all('tr', recursive=False)
    rows.sort(key=lambda row: (1 if ascending else -1) * float(row.find_all('td')[score_column]['data-sort']))
    scores = [float(row.find_all('td')[score_column]['data-sort']) * (-1 if ascending else 1) for row in rows]
    directions = {c: ('saved_metric_' + str(c), 'min') for c in minimize}
    directions.update({c: ('saved_metric_' + str(c), 'max') for c in maximize})
    token = _RENDER_CONTEXT.set(RenderContext(descriptive_ranks=True))
    try:
        highlighted = comparative_rows([('', ''.join(str(c) for c in row.find_all('td', recursive=False))) for row in rows], directions)
    finally:
        _RENDER_CONTEXT.reset(token)
    table.tbody.clear()
    for (_, cells), rank in zip(highlighted, shared_ranks(scores), strict=True):
        row = BeautifulSoup('<tr>' + cells + '</tr>', 'html.parser').tr
        row.find('td').string = str(rank)
        table.tbody.append(row)
    table['data-row-index'] = 'false'
    table.thead.tr.find('th').string = '排名'


def _model_fetch_badges(page):
    labels = {'DeepSeek Fetch API', 'Claude Web Fetch', 'Claude Web Fetch · Haiku 5.5', 'Z.AI Web Reader', 'Kimi Fetch API'}
    for link in page.select('a[href], span[data-report-en]'):
        if link.name == 'a' and not re.search(r'/providers/(?:deepseek-fetch-api|claude-web-fetch|zai-reader|kimi-fetch)\.html$', '/' + link['href']):
            continue
        if link.name == 'span' and (link.get('data-report-en') not in labels or link.find_parent('a')):
            continue
        if link.select_one('.model-fetch-badge'):
            continue
        badge = page.new_tag('sup', attrs={'class': 'model-fetch-badge',
            'title': '大模型提供的网页抓取工具或 API',
            'style': 'font-size:10px;margin-left:4px;padding:2px 4px;border-radius:4px;background:#e8efff;color:#1746a2;white-space:nowrap'})
        badge.string = '模型 Fetch'
        link.append(badge)


def _extension_case_routes(cases, added):
    """Match every task once, retaining duplicate URLs with distinct case IDs."""
    remaining = dict(cases)
    pending = {case['id']: case for case in added}
    routes = {}
    def key(case):
        return case['url'], case.get('name', '')
    def contract(case):
        return {k: v for k, v in case.items() if k != 'id'}
    def bind(source, target):
        routes[source] = target
        pending.pop(source)
        remaining.pop(target)
    # Stable task IDs take precedence even when the frozen editions differ.
    for identifier, case in list(pending.items()):
        if identifier in remaining and key(case) == key(remaining[identifier]):
            bind(identifier, identifier)
    # Renamed IDs require a uniquely matching frozen definition. Never guess
    # how indistinguishable repeated tasks correspond across editions.
    for identifier, case in list(pending.items()):
        matches = sorted(i for i, candidate in remaining.items()
                         if key(candidate) == key(case) and contract(candidate) == contract(case))
        if len(matches) == 1:
            bind(identifier, matches[0])
    for identifier, case in list(pending.items()):
        matches = [i for i, candidate in remaining.items() if key(candidate) == key(case)]
        if len(matches) != 1:
            raise ValueError('Report extension has ambiguous or unmatched task identities')
        bind(identifier, matches[0])
    if remaining:
        raise ValueError('Report extension omitted task identities')
    return routes


def _joined_presentation(output):
    """Join frozen renderer inputs through a one-to-one task identity mapping."""
    from harness.record_io import load_jsonl
    base = _read(output / 'dataset.json')
    cases = {c['id']: c for c in base['cases']}
    records = load_jsonl(output / 'results.jsonl')
    extra = _read(output / 'additional/dataset.json')
    routes = _extension_case_routes(cases, extra['cases'])
    if any(c.get('task_kind', 'body') != cases[routes[c['id']]].get('task_kind', 'body') for c in extra['cases']):
        raise ValueError('Joined presentation requires the same task kinds')
    audit = _read(output / 'additional/saved-matrix-audit.json')
    paths = {name: 'additional/' + name for name in audit['files']}
    for record in _project(load_jsonl(output / 'additional/results.jsonl'), paths):
        record['case_id'] = routes[record['case_id']]
        records.append(record)
    base_model, added_model = _read(output/'matrix.json'), _read(output/'additional/matrix.json')
    providers = base_model['providers'] + added_model['providers']
    from harness.web_fetch.report import succeeded, measured
    expected = {}
    for model, mapping in ((base_model, {key: key for key in cases}), (added_model, routes)):
        for row in model['rows']:
            key = row['provider'], mapping[row['suite'] + '--' + row['case_id']]
            if key in expected:
                raise ValueError('Joined presentation repeats a saved task result')
            expected[key] = row
    if len(records) != len(expected):
        raise ValueError('Joined presentation result population differs')
    for record in records:
        row = expected[record['provider'], record['case_id']]
        verdict = succeeded(record, cases[record['case_id']]) if measured(record, cases[record['case_id']]) else None
        # Adapter rejection retains its saved failure value without claiming a
        # product call or entering the measurable-result denominator.
        if row.get('status') == 'adapter_rejected':
            verdict = False
        if (verdict != row['passed']
                or record['elapsed_ms'] != row['elapsed_ms']):
            raise ValueError('Joined presentation changed saved scores or latency')
    return cases, records, providers, base.get('report_suites', [])


def _shared_presentations(output):
    """Render one participant matrix through the existing shared components."""
    from harness.web_fetch import report as renderer
    cases, records, providers, suites = _joined_presentation(output)
    labels = {p['id']: p.get('label', p['id']) for p in providers}
    tokens = [(renderer._REPORT_PROVIDER_LABELS, renderer._REPORT_PROVIDER_LABELS.set(labels)),
              (renderer._REPORT_SUITES, renderer._REPORT_SUITES.set(tuple(suites))),
              (renderer._RENDER_CONTEXT, renderer._RENDER_CONTEXT.set(renderer.RenderContext(descriptive_ranks=True)))]
    try:
        return {'overall': renderer.overall_ranking(records, cases),
                'families': {f['id']: renderer.saved_suite_ranking(records, cases, f['id']) for f in suites},
                'timing': renderer.overview_matched_latency(records, cases),
                'cards': ''.join(renderer.overview_card(records, cases, f['id'], f['label'], list(labels)) for f in suites),
                'analysis': renderer.analysis_body(records, cases),
                'guide': renderer.render_explainer(output, cases, records, authored=False)}
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def _unified_home(page, base_model, added_model, presentation):
    """Install shared-renderer views without grouping contestants by source."""
    from bs4 import BeautifulSoup
    for note in page.select('[data-composition-scope], [data-added-report]'):
        note.decompose()
    method_section = page.find(id='method-overview-section')
    if method_section and method_section.find('p'):
        method_section.find('p').string = '价格单位：美元。'
    page.select_one('#overall-ranking h2').string = '任务通过率排名'
    for identifier, content in [('ranking-overall', presentation['overall']),
                                *[('ranking-' + key, value) for key, value in presentation['families'].items()]]:
        rendered = BeautifulSoup(content, 'html.parser')
        fragment = rendered.find('table', id=identifier)
        page.find('table', id=identifier).replace_with(fragment)
        if fragment.get('data-row-index') != 'false':
            fragment.thead.tr.find('th').string = '排名'
        else:
            for note in rendered.select('p.callout'):
                fragment.parent.insert_before(note)
            if identifier == 'ranking-overall':
                page.select_one('#overall-ranking h2').string = '各方法已保存结果'
        fragment['data-row-index'] = 'false'
    timing = page.select_one('#overall-matched-latency .wrap')
    if timing:
        timing.clear()
        timing.append(BeautifulSoup('<h2>全部方法共同成功任务的耗时</h2>' + presentation['timing'], 'html.parser'))
        for note in timing.select('p.source-note'):
            note.decompose()
        table = timing.find('table')
        if table:
            _rank_extension_table(table, 2, minimize=[2, 3, 4], ascending=True)
        if timing.find('details'):
            timing.find('details')['open'] = ''
    cards = page.select_one('#overview .grid')
    if cards:
        cards.clear()
        cards.append(BeautifulSoup(presentation['cards'], 'html.parser'))
    for node in list(page.find_all(string=True)):
        text = str(node)
        text = text.replace('保留各轮已保存结果；评分口径见下方说明。', '按任务通过率排序。')
        if text != str(node):
            node.replace_with(text)
    added_ids = {provider['id'] for provider in added_model['providers']}
    for link in page.select('a[href^="providers/"]'):
        if Path(link['href']).stem in added_ids:
            link['href'] = 'additional/' + link['href']
    _model_fetch_badges(page)


_EXTENSION_ROUTES = ('analysis/index.html', 'methodology/index.html',
                     'adoption/index.html', 'reproduce/index.html', 'review/index.html',
                     'guide/index.html', 'accounts/index.html')


def _extension_page(base, addition, route, base_model, added_model, overview, note):
    """Merge public comparisons and method inventories, preserving source evidence."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlsplit
    import posixpath
    old, new = BeautifulSoup(base, 'html.parser'), BeautifulSoup(_namespace_extension_evidence(addition), 'html.parser')
    if route == 'accounts/index.html':
        for paragraph in old.find_all('p'):
            if paragraph.get_text().strip().startswith('需要供应商账号的方法：'):
                paragraph.append('、' + '、'.join(p.get('label', p['id']) for p in added_model['providers']))
    if route == 'adoption/index.html':
        from html import escape
        prices = old.select_one('#method-guide-pricing .wrap')
        if prices:
            fragment = '<div class="table-wrap"><table id="model-fetch-pricing"><thead><tr><th>方法</th><th>价格</th><th>计费方式</th><th>来源</th></tr></thead><tbody>'
            for method in overview:
                fragment += '<tr><td><a href="../additional/providers/' + method['id'] + '.html">' + escape(method['label']) + '</a></td><td>' + escape(method['price']) + '</td><td>' + escape(method['billing']) + '</td><td>' + ' · '.join('<a href="' + escape(s['url'], quote=True) + '">' + escape(s['label']) + '</a>' for s in method['sources']) + '</td></tr>'
            prices.append(BeautifulSoup(fragment + '</tbody></table></div>', 'html.parser'))
        incoming = new.find(id='method-guide-pricing')
        if incoming:
            incoming.decompose()
    # Relative links in copied fragments still target the added frozen report.
    for tag in new.select('[href], [src]'):
        for attribute in ('href', 'src'):
            value = tag.get(attribute)
            if not value or value.startswith('#') or urlsplit(value).scheme or value.startswith('//'):
                continue
            resolved = urlsplit(urljoin('https://report.invalid/additional/' + route, value))
            tag[attribute] = posixpath.relpath(resolved.path.lstrip('/'), posixpath.dirname(route))
            if resolved.query:
                tag[attribute] += '?' + resolved.query
            if resolved.fragment:
                tag[attribute] += '#' + resolved.fragment
    if route == 'adoption/index.html':
        interfaces = old.find(id='method-guide-interfaces')
        if interfaces:
            container = interfaces.select_one('.wrap') or interfaces
            for provider in added_model['providers']:
                panel = new.find(id='interface-' + provider['id'])
                if panel:
                    existing = interfaces.find(id=panel['id'])
                    if existing:
                        existing.replace_with(panel)
                    else:
                        container.append(panel)
    def headers(table):
        return tuple(c.get_text(' ', strip=True) for c in table.select('thead th'))
    tables = list(old.select('table'))
    unmatched = []
    for source_table in list(new.select('table')):
        candidates = [t for t in tables if headers(t) == headers(source_table)]
        if len(candidates) == 1:
            target_table = candidates[0]
            if len(headers(source_table)) > 1 and headers(source_table)[1] in {'任务类别', '核对点', '参数'}:
                continue
            for row in list(source_table.tbody.find_all('tr', recursive=False)):
                target_table.tbody.append(row)
            for index, row in enumerate(target_table.tbody.find_all('tr', recursive=False), 1):
                row.find('td').string = str(index)
        elif source_table.tbody and source_table.tbody.find('tr'):
            unmatched.append(source_table)
    # Include interface/settings/review sections that have no corresponding base section.
    selected = []
    for section in new.select('section'):
        heading = section.find(['h1', 'h2'])
        if not heading or heading.name == 'h1':
            continue
        identifier = section.get('id')
        if identifier and not old.find(id=identifier):
            selected.append(section)
        elif any(table in section.descendants for table in unmatched):
            selected.append(section)
    container = old.find('main') or old.body
    for section in selected:
        container.append(section)
    if route == 'methodology/index.html':
        for table in old.select('table'):
            if '外层超时' not in headers(table):
                continue
            represented = {Path(link['href']).stem for link in table.select('tbody a[href]')}
            for row in table.select('tbody tr'):
                cells = row.find_all('td')
                if len(cells) > 1:
                    represented.add(cells[1].get_text(strip=True))
            for provider in added_model['providers']:
                if provider['id'] in represented or provider.get('label', provider['id']) in represented:
                    continue
                settings = [s for group in added_model.get('execution_settings', [])
                            if group['provider'] == provider['id'] for s in group['settings']]
                def observed(key, settings=settings):
                    values = sorted({str(s[key]) for s in settings if key in s})
                    return ' / '.join(values) if values else '未记录'
                row = old.new_tag('tr')
                values = [str(len(table.select('tbody tr')) + 1), provider.get('label', provider['id']),
                          '—', observed('timeout_seconds'), '未记录', '未记录', '未记录', observed('max_failure_retries')]
                for value in values:
                    cell = old.new_tag('td')
                    cell.string = value
                    row.append(cell)
                table.tbody.append(row)
        details = old.new_tag('details')
        summary = old.new_tag('summary')
        summary.string = '评分来源与版本'
        details.append(summary)
        paragraph = old.new_tag('p')
        paragraph.string = note
        details.append(paragraph)
        container.append(details)
    # Only the population summary owns these counts; never rewrite evidence,
    # prices, dates, elapsed times or identifiers elsewhere in the document.
    before_methods = len(base_model['providers'])
    after_methods = before_methods + len(added_model['providers'])
    before_results = len(base_model['rows'])
    after_results = before_results + len(added_model['rows'])
    for summary in old.select('.hero .lead'):
        for node in list(summary.find_all(string=True)):
            text = re.sub(r'\b' + str(before_methods) + r' 种(已测)?方法',
                          lambda match: str(after_methods) + ' 种' + (match[1] or '') + '方法', str(node))
            text = re.sub(r'(?<!\d)' + str(before_results) + r'(?= 个逻辑结果)',
                          str(after_results), text)
            if text != str(node):
                node.replace_with(text)
    _model_fetch_badges(old)
    return str(old)


def _extension_views(output, base_model, added_model, overview, note):
    """Return deterministic non-home views; source pages remain hash-bound copies."""
    result = {}
    for route in _EXTENSION_ROUTES:
        retained = output / 'extension-base-pages' / route
        source = retained if retained.exists() else output / route
        addition = output / 'additional' / route
        if source.is_file() and addition.is_file():
            if route in {'analysis/index.html', 'guide/index.html'}:
                result[route] = source.read_text(encoding='utf-8')
                continue
            result[route] = _extension_page(source.read_text(encoding='utf-8'),
                addition.read_text(encoding='utf-8'), route, base_model, added_model, overview, note)
    from bs4 import BeautifulSoup
    presentation = _shared_presentations(output)
    added_providers = {p['id'] for p in added_model['providers']}
    from harness.record_io import load_jsonl
    added_attempts = {r['attempt_id'] for r in load_jsonl(output / 'additional/results.jsonl')}
    for route, key in [('analysis/index.html', 'analysis'), ('guide/index.html', 'guide')]:
        if route not in result:
            continue
        page = BeautifulSoup(result[route], 'html.parser')
        container = page.find('main') or page.body
        container.clear()
        container.append(BeautifulSoup(presentation[key], 'html.parser'))
        for link in page.select('a[href]'):
            match = re.search(r'(providers|attempts)/([^/]+)\.html', link['href'])
            if match and ('/additional/' not in '/' + link['href']):
                ids = added_providers if match[1] == 'providers' else added_attempts
                if match[2] in ids:
                    link['href'] = link['href'][:match.start()] + 'additional/' + link['href'][match.start():]
        _model_fetch_badges(page)
        result[route] = str(page)

    return result


def build_published_extension(spec_path, output):
    """Extend a frozen publication while retaining each scoring edition verbatim."""
    from harness.web_fetch.report_saved_matrix import _path, _write
    spec = _read(spec_path)
    base_root = _path(spec_path.parent, spec['published_base']['root'])
    added_root = _path(spec_path.parent, spec['additional_report']['root'])
    base = _frozen_report(base_root, spec['published_base']['audit'])
    added = _frozen_report(added_root, spec['additional_report']['audit'])
    if {p['id'] for p in base['providers']} & {p['id'] for p in added['providers']}:
        raise ValueError('Report extension has duplicate providers')
    population = None
    for model in (base, added):
        for provider in model['providers']:
            rows = [r for r in model['rows'] if r['provider'] == provider['id']]
            urls = Counter((r['url'], r.get('name', '')) for r in rows)
            if not rows or (population is not None and urls != population):
                raise ValueError('Report extension task URL populations differ')
            population = urls
    overview_path = _path(spec_path.parent, spec['method_overview']['path'])
    if _hash(overview_path) != spec['method_overview']['sha256']:
        raise ValueError('Method configuration and pricing binding changed')
    overview = _read(overview_path)
    if {p['id'] for p in overview} != {p['id'] for p in added['providers']}:
        raise ValueError('Method overview must cover every added provider')
    shutil.copytree(base_root, output)
    shutil.copytree(added_root, output / 'additional')
    shutil.copyfile(base_root / 'index.html', output / 'extension-base-home.txt')
    ranked = spec.get('presentation') == 'ranked'
    (output / 'index.html').write_text(_extended_home(
        (base_root / 'index.html').read_text(encoding='utf-8'), (added_root / 'index.html').read_text(encoding='utf-8'),
        base, added, overview, spec['comparison_note'], ranked=ranked, presentation=_shared_presentations(output) if ranked else None), encoding='utf-8')
    pages = _extension_views(output, base, added, overview, spec['comparison_note']) if ranked else {}
    for route, content in pages.items():
        retained = output / 'extension-base-pages' / route
        retained.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output / route, retained)
        (output / route).write_text(content, encoding='utf-8')
    _write(output / 'extension-methods.json', overview)
    _write(output / 'saved-extension.json', {
        'schema_version': 1, 'spec_sha256': _hash(spec_path),
        'base_audit_sha256': _hash(base_root / 'saved-matrix-audit.json'),
        'additional_audit_sha256': _hash(added_root / 'saved-matrix-audit.json'),
        'comparison_note': spec['comparison_note'], 'provider_calls_added': 0,
        'presentation': spec.get('presentation', 'audit'), 'presentation_pages': list(pages),
        'providers': base['providers'] + added['providers'],
        'files': {p.relative_to(output).as_posix(): _hash(p) for p in output.rglob('*') if p.is_file()}})
    check_published_extension(output)
    return output / 'index.html'


def check_published_extension(output):
    from bs4 import BeautifulSoup
    proof = _read(output / 'saved-extension.json')
    inventory = list(output.rglob('*'))
    if output.is_symlink() or any(p.is_symlink() for p in inventory):
        raise ValueError('Published extensions reject symlinks')
    actual = {p.relative_to(output).as_posix() for p in inventory if p.is_file()}
    if actual != set(proof['files']) | {'saved-extension.json'}:
        raise ValueError('Published extension inventory changed')
    for name, digest in proof['files'].items():
        if _hash(output / name) != digest:
            raise ValueError('Published extension bytes changed: ' + name)
    baseline = _read(output / 'saved-matrix-audit.json')
    if (_hash(output / 'saved-matrix-audit.json') != proof['base_audit_sha256']
            or _hash(output / 'additional/saved-matrix-audit.json') != proof['additional_audit_sha256']
            or _hash(output / 'extension-base-home.txt') != baseline['files']['index.html']):
        raise ValueError('Published extension source binding changed')
    for name, digest in baseline['files'].items():
        retained = ('extension-base-home.txt' if name == 'index.html' else
                    'extension-base-pages/' + name if name in proof.get('presentation_pages', []) else name)
        if proof['files'].get(retained) != digest:
            raise ValueError('Published extension changed original evidence: ' + name)
    additional = _read(output / 'additional/saved-matrix-audit.json')
    for name, digest in additional['files'].items():
        if proof['files'].get('additional/' + name) != digest:
            import hashlib
            path = output/'additional'/name
            if (not name.endswith('.html') or hashlib.sha256(_restore_extension_evidence(
                    path.read_text(encoding='utf-8')).encode()).hexdigest() != digest):
                raise ValueError('Published extension changed added evidence: ' + name)
    expected = _extended_home((output / 'extension-base-home.txt').read_text(encoding='utf-8'),
        (output / 'additional/index.html').read_text(encoding='utf-8'), _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), _read(output / 'extension-methods.json'),
        proof['comparison_note'], ranked=proof.get('presentation') == 'ranked',
        presentation=_shared_presentations(output) if proof.get('presentation') == 'ranked' else None)
    if (output / 'index.html').read_text(encoding='utf-8') != expected:
        raise ValueError('Published extension comparison differs from source metrics')
    if proof.get('presentation') == 'ranked':
        views = _extension_views(output, _read(output / 'matrix.json'),
            _read(output / 'additional/matrix.json'), _read(output / 'extension-methods.json'), proof['comparison_note'])
        if set(views) != set(proof.get('presentation_pages', [])):
            raise ValueError('Published extension omitted comparison pages')
        for route, content in views.items():
            if (output / route).read_text(encoding='utf-8') != content:
                raise ValueError('Published comparison page differs from its frozen sources: ' + route)
        if 'analysis/index.html' in views:
            analysis = BeautifulSoup(views['analysis/index.html'], 'html.parser')
            provider_count = len(proof['providers'])
            for selector in ('#compare-a option', '[data-compare-method]'):
                if len(analysis.select(selector)) != provider_count:
                    raise ValueError('Unified method comparison omitted selectable methods')
            expected_rows = len(_read(output / 'matrix.json')['rows']) + len(_read(output / 'additional/matrix.json')['rows'])
            if len(analysis.select('#filtered-table tbody tr')) != expected_rows:
                raise ValueError('Unified result inventory omitted saved results')
    home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    count = len(proof['providers'])
    for table in home.select('table[id^="ranking-"]'):
        if len(table.tbody.find_all('tr', recursive=False)) != count or table.get('data-row-index') != 'false':
            raise ValueError('Published extension comparison population differs')
    if len(home.select('[data-added-method]')) != len(_read(output / 'extension-methods.json')):
        raise ValueError('Published extension omitted method configurations')
    from urllib.parse import urljoin, urlsplit, unquote
    for route in ['index.html', *proof.get('presentation_pages', [])]:
        page = home if route == 'index.html' else BeautifulSoup((output / route).read_text(encoding='utf-8'), 'html.parser')
        for link in page.select('a[href]'):
            url = urlsplit(urljoin('https://extension.invalid/' + route, link['href']))
            if url.netloc == 'extension.invalid' and url.path != '/':
                if not contained_path(output, unquote(url.path.lstrip('/')), reject_symlinks=True).is_file():
                    raise ValueError('Published extension has a broken page link: ' + route + ': ' + link['href'])
    return {'valid': True, 'files_verified': len(proof['files']), 'providers': count, 'provider_calls_added': 0}


def refresh_published_extension(output, *, spec_path=None):
    """Validate a complete replacement before installing it; restore on failure."""
    import os
    import tempfile
    from harness.web_fetch.safety import evidence_tree_identity, unlinked_path
    output = unlinked_path(output)
    before = evidence_tree_identity(output)
    with tempfile.TemporaryDirectory(prefix='.' + output.name + '-refresh-', dir=output.parent) as work:
        staging = Path(work)/'report'
        # All changes below use atomic replacement; unchanged evidence can share
        # inodes instead of copying potentially large immutable provider files.
        shutil.copytree(output, staging, copy_function=os.link)
        result = _refresh_published_extension(staging, spec_path=spec_path)
        if evidence_tree_identity(output) != before:
            raise ValueError('Published extension changed during refresh')
        previous = output.parent/(Path(work).name + '-previous')
        output.rename(previous)
        try:
            staging.rename(output)
        except BaseException:
            try:
                previous.rename(output)
            except OSError as error:
                raise RuntimeError('Refresh rollback failed; prior report retained at ' + str(previous)) from error
            raise
        shutil.rmtree(previous)
    return result


def _refresh_published_extension(output, *, spec_path=None):
    """Rebuild the home view from the same hash-bound, immutable source trees."""
    from harness.reporting.publication import atomic_write
    from harness.record_io import json_bytes
    import hashlib
    proof = _read(output / 'saved-extension.json')
    overview = _read(output / 'extension-methods.json')
    if spec_path is not None:
        spec = _read(spec_path)
        if (spec['published_base']['audit']['sha256'] != proof['base_audit_sha256']
                or spec['additional_report']['audit']['sha256'] != proof['additional_audit_sha256']):
            raise ValueError('Refresh cannot replace frozen report sources')
        from harness.web_fetch.report_saved_matrix import _path
        path = _path(spec_path.parent, spec['method_overview']['path'])
        if _hash(path) != spec['method_overview']['sha256']:
            raise ValueError('Refresh method overview binding changed')
        overview = _read(path)
        if {p['id'] for p in overview} != {p['id'] for p in _read(output / 'extension-methods.json')}:
            raise ValueError('Refresh cannot replace methods')
    ranked = (spec.get('presentation') if spec_path is not None else proof.get('presentation')) == 'ranked'
    note = spec['comparison_note'] if spec_path is not None else proof['comparison_note']
    home = _extended_home((output / 'extension-base-home.txt').read_text(encoding='utf-8'),
        (output / 'additional/index.html').read_text(encoding='utf-8'), _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), overview,
        note, ranked=ranked, presentation=_shared_presentations(output) if ranked else None)
    for name, digest in proof['files'].items():
        actual = _hash(contained_path(output, name, reject_symlinks=True))
        if actual != digest and not (name == 'index.html' and actual == hashlib.sha256(home.encode()).hexdigest()):
            raise ValueError('Cannot refresh changed extension evidence: ' + name)
    baseline = _read(output / 'saved-matrix-audit.json')
    if _hash(output / 'extension-base-home.txt') != baseline['files']['index.html']:
        raise ValueError('Cannot refresh changed base home')
    atomic_write(output / 'index.html', home.encode())
    pages = _extension_views(output, _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), overview, note) if ranked else {}
    for route, content in pages.items():
        retained = output / 'extension-base-pages' / route
        if not retained.exists():
            retained.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output / route, retained)
        atomic_write(output / route, content.encode())
        proof['files'][route] = _hash(output / route)
        proof['files']['extension-base-pages/' + route] = _hash(retained)
    if not ranked:
        for route in proof.get('presentation_pages', []):
            retained = output/'extension-base-pages'/route
            atomic_write(output/route, retained.read_bytes())
            proof['files'][route] = _hash(output/route)
    proof['presentation_pages'] = list(pages)
    proof['presentation'] = 'ranked' if ranked else 'audit'
    proof['comparison_note'] = note
    if spec_path is not None:
        atomic_write(output / 'extension-methods.json', json_bytes(overview))
        proof['files']['extension-methods.json'] = _hash(output / 'extension-methods.json')
        proof['spec_sha256'] = _hash(spec_path)
    proof['files']['index.html'] = _hash(output / 'index.html')
    atomic_write(output / 'saved-extension.json', json_bytes(proof))
    return check_published_extension(output)


def _read(path):
    return json.loads(path.read_bytes())


def _project(value, paths):
    if isinstance(value, dict):
        return {k: _project(v, paths) for k, v in value.items()}
    if isinstance(value, list):
        return [_project(v, paths) for v in value]
    return paths.get(value, value) if isinstance(value, str) else value


def _public_spec(spec):
    result = {k: deepcopy(spec[k]) for k in ('title', 'limitations', 'presentation', 'combined_ranking') if k in spec}
    if spec.get('native_policy_registry'):
        result['native_policy_registry'] = True
    return result


def composition_model(output, spec, snapshots):
    from harness.web_fetch.report_saved_matrix import _case_identity, _totals, check_saved_matrix
    providers, rows, cases, suites, families = [], [], {}, None, None
    expected_population = None
    for snapshot in snapshots:
        if snapshot['path'] != 'evidence/saved-reports/' + safe_identifier(snapshot['id']):
            raise ValueError('Invalid saved composition snapshot path')
        root = contained_path(output, snapshot['path'], reject_symlinks=True)
        if _hash(root / 'saved-matrix-audit.json') != snapshot['sha256']:
            raise ValueError('Saved composition source audit changed')
        check_saved_matrix(root)
        audit, model = _read(root / 'saved-matrix-audit.json'), _read(root / 'matrix.json')
        if 'providers' in snapshot:
            selected = snapshot['providers']
            available = {p['id'] for p in model['providers']}
            if (not isinstance(selected, list) or not selected
                    or any(not isinstance(p, str) for p in selected)
                    or len(selected) != len(set(selected)) or not set(selected) <= available):
                raise ValueError('Invalid saved report provider selection')
            model['providers'] = [p for p in model['providers'] if p['id'] in selected]
            model['rows'] = [r for r in model['rows'] if r['provider'] in selected]
        if suites is None:
            suites, families = model['suites'], model.get('report_families')
        if suites != model['suites'] or families != model.get('report_families'):
            raise ValueError('Saved comparisons have different task groups')
        if {p['id'] for p in providers} & {p['id'] for p in model['providers']}:
            raise ValueError('Saved comparisons have duplicate methods')
        providers.extend(model['providers'])
        paths = {name: snapshot['path'] + '/' + name for name in audit['files']}
        population = set()
        for row in model['rows']:
            key = row['suite'], row['case_id']
            case = _case_identity(_read(root / row['case_path']))
            if key in cases and cases[key] != case:
                raise ValueError('Saved comparisons have different frozen tasks')
            cases[key] = case
            population.add(key)
        if expected_population is not None and population != expected_population:
            raise ValueError('Saved comparisons have different case populations')
        expected_population = population
        rows.extend(_project(model['rows'], paths))
    if not rows:
        raise ValueError('Saved composition requires reports')
    result = dict(title=spec['title'], providers=providers, suites=suites, rows=rows,
                  totals=_totals(rows), limitations=spec.get('limitations', []),
                  presentation=spec.get('presentation', 'audit'),
                  combined_ranking=spec.get('combined_ranking', True),
                  source_bindings=[{'name': s['id'], 'sha256': s['sha256']} for s in snapshots],
                  scores_changed=any(row.get('score_revision') for row in rows))
    if families is not None:
        result['report_families'] = families
    if spec.get('native_policy_registry'):
        result['native_policy_registry'] = {'path': 'native-policy-registry.json',
                                           'sha256': _hash(output / 'native-policy-registry.json')}
    return result


def build_composition(spec_path, output):
    from harness.web_fetch.report import generate_saved_matrix
    from harness.web_fetch.report_saved_matrix import _path, _write, check_saved_matrix
    spec = _read(spec_path)
    if spec.get('schema_version') != 1 or not isinstance(spec.get('saved_reports'), list):
        raise ValueError('Unsupported saved composition specification')
    snapshots, identifiers = [], set()
    output.mkdir()
    for item in spec['saved_reports']:
        if item.get('role', 'main') != 'main':
            raise ValueError('Saved composition supports main methods only')
        identifier = safe_identifier(item['id'])
        if identifier in identifiers:
            raise ValueError('Duplicate saved report identifier')
        identifiers.add(identifier)
        root = _path(spec_path.parent, item['root'])
        audit_path = root / 'saved-matrix-audit.json'
        if _hash(audit_path) != item['audit']['sha256']:
            raise ValueError('Saved report audit differs from binding')
        check_saved_matrix(root)
        relative = 'evidence/saved-reports/' + identifier
        shutil.copytree(root, output / relative)
        snapshot = {'id': identifier, 'path': relative, 'sha256': _hash(audit_path)}
        if 'providers' in item:
            snapshot['providers'] = item['providers']
        snapshots.append(snapshot)
        # Revision receipts use report-root review attachment links. Preserve
        # those exact bytes, rejecting ambiguous collisions across sources.
        model = _read(root / 'matrix.json')
        receipt_paths = {row['score_revision']['receipt_path'] for row in model['rows']
                         if row.get('score_revision')}
        for receipt_path in sorted(receipt_paths):
            receipt = _read(root / receipt_path)
            for name in receipt.get('review_files', {}).values():
                target = output / name
                if target.exists() and target.read_bytes() != (root / name).read_bytes():
                    raise ValueError('Conflicting saved review attachments')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
    registry = spec.get('native_policy_registry')
    if registry:
        source = _path(spec_path.parent, registry['path'])
        if _hash(source) != registry['sha256']:
            raise ValueError('Native policy registry changed')
        shutil.copyfile(source, output / 'native-policy-registry.json')
    model = composition_model(output, spec, snapshots)
    _write(output / 'matrix.json', model)
    _write(output / 'saved-composition.json', {'spec': _public_spec(spec), 'snapshots': snapshots})
    entry = generate_saved_matrix(output, model)
    files = {p.relative_to(output).as_posix(): _hash(p) for p in output.rglob('*') if p.is_file()}
    _write(output / 'saved-matrix-audit.json', dict(schema_version=1, spec_sha256=_hash(spec_path),
        sources=model['source_bindings'], files=files, totals=model['totals'],
        provider_calls_added=0, scores_changed=model['scores_changed']))
    check_saved_matrix(output)
    return entry


def check_composition(output, spec_path=None):
    stored = _read(output / 'saved-composition.json')
    if spec_path is not None and _public_spec(_read(Path(spec_path))) != stored['spec']:
        raise ValueError('Saved composition specification changed')
    if composition_model(output, stored['spec'], stored['snapshots']) != _read(output / 'matrix.json'):
        raise ValueError('Saved composition differs from its immutable source reports')
