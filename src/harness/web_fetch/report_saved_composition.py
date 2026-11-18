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


def _extended_home(base, addition, base_model, added_model, overview, note, *, ranked=False):
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
        old_rows, new_rows = target.tbody.find_all('tr', recursive=False), source.tbody.find_all('tr', recursive=False)
        if len(old_rows) != len(base_model['providers']) or len(new_rows) != len(added_model['providers']):
            raise ValueError('Comparison table provider population differs')
        target['data-row-index'] = 'false'
        target.thead.tr.find(['th', 'td']).string = '来源'
        extra_review = False
        if identifier == 'ranking-overall':
            target_headers = target.thead.tr.find_all('th', recursive=False)
            source_headers = source.thead.tr.find_all('th', recursive=False)
            if len(source_headers) == len(target_headers) + 1 and source_headers[-1].get_text().startswith('审查结果'):
                target.thead.tr.append(source_headers[-1])
                extra_review = True
            elif len(source_headers) != len(target_headers):
                raise ValueError('Incompatible overall comparison metrics')
        for row in old_rows:
            row.find('td').string = '原轮'
            if extra_review:
                cell = old.new_tag('td')
                link = old.new_tag('a', href='review/index.html')
                link.string = '原轮核验记录'
                cell.append(link)
                row.append(cell)
        for row in new_rows:
            cells = row.find_all('td', recursive=False)
            if identifier == 'ranking-overall':
                if len(cells) != len(source_headers):
                    raise ValueError('Unexpected overall metric columns')
                reordered = cells[:4] + [cells[4 + added_order.index(f)] for f in family_order] + cells[4 + len(added_order):]
                row.clear()
                for cell in reordered:
                    row.append(cell)
            row.find('td').string = '新增'
            for link in row.find_all('a', href=True):
                link['href'] = 'additional/' + link['href']
            target.tbody.append(row)
        # Values remain sortable, but mixed scorer editions have no shared best.
        for cell in target.find_all(True):
            if 'class' in cell.attrs:
                cell['class'] = [c for c in cell['class'] if c not in {'good', 'bad'} and not re.search(r'best|worst|winner', c)]
        for emphasis in target.find_all('strong'):
            emphasis.unwrap()
        explanation = old.new_tag('p', attrs={'class': 'source-note', 'data-composition-scope': identifier})
        explanation.string = note
        target.parent.insert_before(explanation)
    methods = old.find('table', id='method-overview')
    if methods is None:
        raise ValueError('Published base has no method overview')
    for method in overview:
        row = old.new_tag('tr', attrs={'data-added-method': method['id']})
        settings = '<p>' + escape(method['configuration']) + '</p>'
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
        updated = updated.replace('本类最高：', '原轮最高：')
        updated = updated.replace('全部方法共同成功任务的耗时', '原轮方法共同成功任务的耗时')
        updated = updated.replace('价格为美元，核验于', '原轮价格为美元，核验于')
        if updated != text:
            node.replace_with(updated)
    entry = old.new_tag('p', attrs={'class': 'source-note', 'data-added-report': 'true'})
    entry.append(BeautifulSoup('<a href="additional/index.html">新增方法的配置、分类结果、逐例证据与核验记录 →</a>', 'html.parser'))
    methods.parent.insert_after(entry)
    if ranked:
        _unified_home(old, base_model, added_model)
    return str(old)


def _rank_extension_table(table, score_column, *, minimize=(), maximize=(), ascending=False):
    """Rank saved numeric values and mark every tied optimum in the shown scope."""
    def number(cell):
        raw = cell.get('data-sort')
        if raw is not None:
            return float(raw)
        text = cell.get_text(strip=True).replace(',', '')
        if '/' in text:
            left, right = text.split('/', 1)
            return float(left) / float(right.split()[0])
        return float(text.rstrip('%'))
    rows = table.tbody.find_all('tr', recursive=False)
    for emphasis in table.select('strong'):
        emphasis.unwrap()
    for cell in table.select('.good, .bad'):
        cell['class'] = [c for c in cell.get('class', []) if c not in {'good', 'bad'}]
    rows.sort(key=lambda row: ((1 if ascending else -1) * number(row.find_all('td', recursive=False)[score_column]), row.get_text()))
    previous = None
    for index, row in enumerate(rows):
        score = number(row.find_all('td', recursive=False)[score_column])
        if score != previous:
            rank = index + 1
        row.find('td').string = str(rank)
        previous = score
        table.tbody.append(row)
    table['data-row-index'] = 'false'
    table.thead.tr.find('th').string = '排名'
    for column, direction in [(c, min) for c in minimize] + [(c, max) for c in maximize]:
        cells = [row.find_all('td', recursive=False)[column] for row in rows]
        values = [number(cell) for cell in cells]
        best = direction(values)
        for cell, value in zip(cells, values, strict=True):
            if value == best:
                from bs4 import BeautifulSoup
                emphasis = BeautifulSoup('', 'html.parser').new_tag('strong')
                emphasis['class'] = 'metric-best'
                for child in list(cell.contents):
                    emphasis.append(child)
                cell.append(emphasis)


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


def _unified_home(page, base_model, added_model):
    """One comparison population, including recomputed shared-task timing."""
    from bs4 import BeautifulSoup
    from html import escape
    from statistics import mean
    from harness.web_fetch.report import percentile
    providers = base_model['providers'] + added_model['providers']
    for note in page.select('[data-composition-scope], [data-added-report]'):
        note.decompose()
    method_section = page.find(id='method-overview-section')
    if method_section and method_section.find('p'):
        method_section.find('p').string = '价格单位：美元。'
    page.select_one('#overall-ranking h2').string = '任务通过率排名'
    for table in page.select('table[id^="ranking-"]'):
        width = len(table.tbody.tr.find_all('td', recursive=False))
        if table['id'] == 'ranking-overall':
            families = len(base_model['report_families'])
            _rank_extension_table(table, 3, maximize=range(2, 4 + families), minimize=range(4 + families, 7 + families))
        else:
            _rank_extension_table(table, width - 1, maximize=[width - 1])
    models = [(base_model, ''), (added_model, 'additional/')]
    groups = [(p, prefix, [r for r in model['rows'] if r['provider'] == p['id']])
              for model, prefix in models for p in model['providers']]
    shared = set.intersection(*({(r['url'], r.get('name', '')) for r in rows if r['passed'] is True}
                               for _, _, rows in groups))
    timing = page.select_one('#overall-matched-latency')
    if timing:
        population = len(groups[0][2]) if groups else 0
        content = f'<h2>全部方法共同成功任务的耗时</h2><p>{len(providers)} 种方法共同通过 {len(shared)}/{population} 个任务。各方法使用同一组任务，按平均耗时从低到高排名；最低值加粗。</p>'
        if shared:
            content += '<div class="table-wrap"><table id="overall-matched-latency-table"><thead><tr><th>排名</th><th>方法</th><th>同题均值</th><th>同题 P50</th><th>同题 P95</th></tr></thead><tbody>'
            for provider, prefix, rows in groups:
                times = [r['elapsed_ms'] for r in rows if (r['url'], r.get('name', '')) in shared]
                values = [mean(times), percentile(times, .5), percentile(times, .95)]
                content += '<tr><td></td><td><a href="' + prefix + 'providers/' + provider['id'] + '.html">' + escape(provider.get('label', provider['id'])) + '</a></td>'
                content += ''.join(f'<td data-sort="{value}">{value:,.0f} ms</td>' for value in values) + '</tr>'
            content += '</tbody></table></div><details><summary>共同任务</summary><ul>' + ''.join('<li>' + escape(name) + '</li>' for _, name in sorted(shared)) + '</ul></details>'
        else:
            content += '<p>没有全部方法共同通过的任务，暂不计算同题耗时。</p>'
        wrap = timing.select_one('.wrap') or timing
        wrap.clear()
        wrap.append(BeautifulSoup(content, 'html.parser'))
        table = timing.find('table')
        if table:
            _rank_extension_table(table, 2, minimize=[2, 3, 4], ascending=True)
    cards = page.select('#overview .card')
    for card, family in zip(cards, base_model['report_families'], strict=False):
        table = page.find('table', id='ranking-' + family['id'])
        if table:
            winners = [r for r in table.tbody.find_all('tr', recursive=False) if r.find('td').get_text() == '1']
            paragraphs = card.find_all('p')
            if len(paragraphs) >= 2 and winners:
                paragraphs[0].string = '本类最高：' + winners[0].find_all('td')[-1].get_text().split('·')[-1].strip()
                paragraphs[1].string = '、'.join(r.find_all('td')[1].get_text(strip=True) for r in winners)
    for node in list(page.find_all(string=True)):
        text = str(node).replace('原轮最高：', '本类最高：').replace('原轮价格为美元', '价格为美元').replace('原轮核验记录', '核验记录')
        text = text.replace('保留各轮已保存结果；评分口径见下方说明。', '按任务通过率排序。')
        if text != str(node):
            node.replace_with(text)
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
    # Task identities differ between saved reports; match the actual URL/name pair.
    base_tasks = {(r['url'], r.get('name', '')): r['suite'] + '--' + r['case_id']
                  for r in base_model['rows']}
    task_ids = {r['suite'] + '--' + r['case_id']: base_tasks[(r['url'], r.get('name', ''))]
                for r in added_model['rows']}
    def task_key(row, *, added=False):
        anchor = row.select_one('a[href*="datasets/"]')
        if not anchor:
            return None
        value = posixpath.basename(urlsplit(anchor['href']).path).removesuffix('.html')
        return task_ids.get(value, value) if added else value
    for identifier in ('compare-a',):
        target, source = old.find(id=identifier), new.find(id=identifier)
        if target and source:
            for option in list(source.select('option')):
                option.attrs.pop('selected', None)
                target.append(option)
    for selector in ('.method-checks', 'select[data-filter="provider"]',
                     'select[data-filter="failureCategory"]'):
        target, source = old.select_one(selector), new.select_one(selector)
        if target and source:
            seen = {node.get('value') for node in target.select('[value]')}
            for child in list(source.children):
                if not getattr(child, 'name', None):
                    continue
                value_node = child if child.has_attr('value') else child.find(attrs={'value': True})
                if value_node and value_node.get('value') not in seen:
                    target.append(child)
                    seen.add(value_node.get('value'))
    # Combine the same-task outcome pills in place, rather than duplicating tasks.
    target, source = old.find(id='compare-rows'), new.find(id='compare-rows')
    handled = set()
    if target and source:
        by_task = {task_key(row): row for row in target.find_all('tr', recursive=False)}
        for row in source.find_all('tr', recursive=False):
            key = task_key(row, added=True)
            if key not in by_task:
                raise ValueError('Added comparison has an unmatched task')
            dest = by_task[key]
            for attr in ('data-pass', 'data-fail'):
                dest[attr] = ' '.join(filter(None, [dest.get(attr), row.get(attr)]))
            for dest_cell, source_cell in zip(dest.find_all('td')[2:], row.find_all('td')[2:], strict=True):
                incoming = source_cell.select('a')
                if incoming:
                    if not dest_cell.select('a'):
                        dest_cell.clear()
                    for anchor in incoming:
                        dest_cell.append(anchor)
        handled.add(id(source.find_parent('table')))
    def headers(table):
        return tuple(c.get_text(' ', strip=True) for c in table.select('thead th'))
    tables = list(old.select('table'))
    unmatched = []
    for source_table in list(new.select('table')):
        if id(source_table) in handled:
            continue
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
            for provider in added_model['providers']:
                settings = [s for group in added_model.get('execution_settings', [])
                            if group['provider'] == provider['id'] for s in group['settings']]
                def observed(key, settings=settings):
                    values = sorted({str(s[key]) for s in settings if key in s})
                    return ' / '.join(values) if values else '未记录'
                row = old.new_tag('tr')
                values = [str(len(table.select('tbody tr')) + 1), provider.get('label', provider['id']),
                          '—', observed('timeout_seconds'), '未记录', '未记录', '1', observed('max_failure_retries')]
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
            result[route] = _extension_page(source.read_text(encoding='utf-8'),
                addition.read_text(encoding='utf-8'), route, base_model, added_model, overview, note)
    if 'guide/index.html' in result and 'analysis/index.html' in result:
        from bs4 import BeautifulSoup
        from html import escape
        source = output / 'extension-base-pages/guide/index.html'
        if not source.exists():
            source = output / 'guide/index.html'
        guide = BeautifulSoup(source.read_text(encoding='utf-8'), 'html.parser')
        analysis = BeautifulSoup(result['analysis/index.html'], 'html.parser')
        models = [base_model, added_model]
        rows = base_model['rows'] + added_model['rows']
        total, passes = len(rows), sum(r['passed'] is True for r in rows)
        for node in list(guide.find_all(string=True)):
            text = re.sub(r'(\d+) 个逻辑结果，任务通过 \d+/\1',
                          f'{total} 个逻辑结果，任务通过 {passes}/{total}', str(node))
            if text != str(node):
                node.replace_with(text)
        content = '<section class="section" id="method-failures"><div class="wrap"><h2>各方法未通过的原因</h2><p>按已保存结果中的失败分类统计；点击示例查看实际返回与评分证据。</p>'
        for model in models:
            for provider in model['providers']:
                failed = analysis.select('#filtered-table tr[data-provider="' + provider['id'] + '"][data-result="fail"]')
                distribution = Counter(r.get('data-failure-category') or '未分类' for r in failed)
                population = [r for r in model['rows'] if r['provider'] == provider['id']]
                passed = sum(r['passed'] is True for r in population)
                prefix = '../additional/' if model is added_model else '../'
                content += '<article class="card"><h3><a href="' + prefix + 'providers/' + provider['id'] + '.html">' + escape(provider.get('label', provider['id'])) + '</a></h3>'
                content += f'<p>共 {len(population)} 题，通过 {passed} 题，未通过 {len(failed)} 题。</p><div class="table-wrap"><table><thead><tr><th>失败分类</th><th>题数</th><th>示例证据</th></tr></thead><tbody>'
                for category, count in distribution.most_common():
                    row = next(r for r in failed if (r.get('data-failure-category') or '未分类') == category)
                    link = row.find_all('td')[2].find('a')['href']
                    content += '<tr><td>' + escape(category) + f'</td><td>{count}</td><td><a href="' + escape(link, quote=True) + '">查看实际返回与评分</a></td></tr>'
                content += '</tbody></table></div></article>'
        content += '</div></section>'
        (guide.find('main') or guide.body).append(BeautifulSoup(content, 'html.parser'))
        _model_fetch_badges(guide)
        result['guide/index.html'] = str(guide)
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
        base, added, overview, spec['comparison_note'], ranked=ranked), encoding='utf-8')
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
            raise ValueError('Published extension changed added evidence: ' + name)
    expected = _extended_home((output / 'extension-base-home.txt').read_text(encoding='utf-8'),
        (output / 'additional/index.html').read_text(encoding='utf-8'), _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), _read(output / 'extension-methods.json'),
        proof['comparison_note'], ranked=proof.get('presentation') == 'ranked')
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
        note, ranked=ranked)
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
