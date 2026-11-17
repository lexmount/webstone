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


def _extended_home(base, addition, base_model, added_model, overview, note):
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
                if len(cells) != len(family_order) + 7 + int(extra_review):
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
    return str(old)


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
    # Lazy evidence paths are relative to a report root, rather than the page.
    # Namespace them when a report is hosted beneath another report's loader.
    for page in (output / 'additional').rglob('*.html'):
        original = page.read_text(encoding='utf-8')
        if re.search(r'\bdata-record=', original):
            raise ValueError('Nested report requires explicit evidence-file bindings')
        rewritten = re.sub(r'\bdata-file="([^"]+)"', r'data-file="additional/\1"', original)
        if rewritten != original:
            page.write_text(rewritten, encoding='utf-8')
    shutil.copyfile(base_root / 'index.html', output / 'extension-base-home.txt')
    (output / 'index.html').write_text(_extended_home(
        (base_root / 'index.html').read_text(encoding='utf-8'), (added_root / 'index.html').read_text(encoding='utf-8'),
        base, added, overview, spec['comparison_note']), encoding='utf-8')
    _write(output / 'extension-methods.json', overview)
    _write(output / 'saved-extension.json', {
        'schema_version': 1, 'spec_sha256': _hash(spec_path),
        'base_audit_sha256': _hash(base_root / 'saved-matrix-audit.json'),
        'additional_audit_sha256': _hash(added_root / 'saved-matrix-audit.json'),
        'comparison_note': spec['comparison_note'], 'provider_calls_added': 0,
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
        retained = 'extension-base-home.txt' if name == 'index.html' else name
        if proof['files'].get(retained) != digest:
            raise ValueError('Published extension changed original evidence: ' + name)
    additional = _read(output / 'additional/saved-matrix-audit.json')
    for name, digest in additional['files'].items():
        retained = 'additional/' + name
        if proof['files'].get(retained) == digest:
            continue
        if not name.endswith('.html'):
            raise ValueError('Published extension changed added evidence: ' + name)
        import hashlib
        original = re.sub(r'\bdata-file="additional/([^"]+)"', r'data-file="\1"',
                          (output / retained).read_text(encoding='utf-8'))
        if hashlib.sha256(original.encode()).hexdigest() != digest:
            raise ValueError('Published extension changed added page beyond its evidence namespace: ' + name)
    expected = _extended_home((output / 'extension-base-home.txt').read_text(encoding='utf-8'),
        (output / 'additional/index.html').read_text(encoding='utf-8'), _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), _read(output / 'extension-methods.json'),
        proof['comparison_note'])
    if (output / 'index.html').read_text(encoding='utf-8') != expected:
        raise ValueError('Published extension comparison differs from source metrics')
    home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    count = len(proof['providers'])
    for table in home.select('table[id^="ranking-"]'):
        if len(table.tbody.find_all('tr', recursive=False)) != count or table.get('data-row-index') != 'false':
            raise ValueError('Published extension comparison population differs')
    if len(home.select('[data-added-method]')) != len(_read(output / 'extension-methods.json')):
        raise ValueError('Published extension omitted method configurations')
    from urllib.parse import urljoin, urlsplit, unquote
    for link in home.select('a[href]'):
        url = urlsplit(urljoin('https://extension.invalid/index.html', link['href']))
        if url.netloc == 'extension.invalid' and url.path != '/':
            if not contained_path(output, unquote(url.path.lstrip('/')), reject_symlinks=True).is_file():
                raise ValueError('Published extension has a broken home link: ' + link['href'])
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
    home = _extended_home((output / 'extension-base-home.txt').read_text(encoding='utf-8'),
        (output / 'additional/index.html').read_text(encoding='utf-8'), _read(output / 'matrix.json'),
        _read(output / 'additional/matrix.json'), overview,
        proof['comparison_note'])
    for name, digest in proof['files'].items():
        actual = _hash(contained_path(output, name, reject_symlinks=True))
        if actual != digest and not (name == 'index.html' and actual == hashlib.sha256(home.encode()).hexdigest()):
            raise ValueError('Cannot refresh changed extension evidence: ' + name)
    baseline = _read(output / 'saved-matrix-audit.json')
    if _hash(output / 'extension-base-home.txt') != baseline['files']['index.html']:
        raise ValueError('Cannot refresh changed base home')
    atomic_write(output / 'index.html', home.encode())
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
