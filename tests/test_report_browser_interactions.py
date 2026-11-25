"""Real browser checks of maintained report controls on small local fixtures.

Small component fixtures run at narrow/dark (theme toggle and narrow layout); width-dependent
layout checks also run at desktop/light. No provider calls or published reports.
"""
import http.server
import json
import os
import threading
import time
from contextlib import contextmanager

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

from harness.reporting.visual import browser_executable, local_report_server, local_page
from harness.web_fetch.report_model import RenderContext


DESKTOP_LIGHT = {"viewport": {"width": 1280, "height": 900}, "color_scheme": "light"}
NARROW_DARK = {"viewport": {"width": 390, "height": 844}, "color_scheme": "dark"}


@pytest.fixture
def display(request):
    return getattr(request, "param", NARROW_DARK)


@pytest.fixture(scope="module")
def browser():
    binary = os.environ.get("WEBSTONE_TEST_BROWSER_BINARY")
    if not binary:
        try:
            binary = browser_executable()
        except ValueError:
            pytest.skip("Install Chromium or set WEBSTONE_TEST_BROWSER_BINARY")
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=binary, headless=True)
        yield instance
        instance.close()


def test_browser_use_sort_filter_restore_and_keyboard_dialog(tmp_path, browser, display):
    from harness.browser_use.report import REPORT_JS

    (tmp_path / "app.js").write_text(REPORT_JS, encoding='utf-8')
    (tmp_path / "index.html").write_text('''<!doctype html><html><head><meta charset="utf-8">
    <script defer src="app.js"></script></head><body>
    <input data-search aria-label="Search"><select data-filter="result">
    <option value="">All</option><option value="fail">Fail</option></select>
    <span data-visible-count></span><table id="tasks"><thead><tr>
    <th><button onclick="sortTable('tasks',0)">Time</button></th></tr></thead><tbody>
    <tr data-tier="easy" data-result="pass"><td data-sort="20">20 ms</td></tr>
    <tr data-tier="hard" data-result="fail"><td data-sort="3">3 ms error</td></tr>
    <tr data-tier="easy" data-result="pass"><td>—</td></tr>
    <tr data-tier="easy" data-result="pass"><td data-sort="0">0 ms</td></tr>
    <tr data-tier="easy" data-result="pass"><td>N/A</td></tr>
    </tbody></table><button id="open" onclick="openDiagnosis('detail')">Evidence</button>
    <dialog id="detail" aria-label="Evidence"><button onclick="closeDiagnosis('detail')">Close</button>
    <pre>Exact evaluator error</pre></dialog></body></html>''', encoding='utf-8')
    with local_report_server(tmp_path) as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin)
            page.locator('th button').click()
            assert page.locator('tbody td').all_text_contents() == ['0 ms', '3 ms error', '20 ms', '—', 'N/A']
            page.locator('th button').click()
            assert page.locator('tbody td').all_text_contents() == ['20 ms', '3 ms error', '0 ms', '—', 'N/A']
            assert page.locator('th').get_attribute('aria-sort') == 'descending'
            page.locator('[data-filter]').select_option('fail')
            assert page.locator('tbody tr:visible').count() == 1
            page.reload()
            assert page.locator('[data-filter]').input_value() == 'fail'
            assert page.locator('tbody tr:visible').count() == 1
            page.locator('#open').click()
            assert page.locator('dialog').is_visible()
            assert 'Exact evaluator error' in page.locator('dialog').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('dialog').is_visible()
            assert page.locator('#open').evaluate('(e)=>e===document.activeElement')
        finally:
            page.close()


@contextmanager
def hosted_preview(root):
    from harness.trajectory_replay.webarena_report_visual import _QuietHandler

    def handler(*args, **kwargs):
        return _QuietHandler(*args, directory=str(root), **kwargs)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.preview_errors = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/'
        assert server.preview_errors == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_replay_real_assets_evidence_dialog_and_private_nested_navigation(tmp_path, browser, display):
    from harness.trajectory_replay.webarena_report import _assets, _shell

    _assets(tmp_path)
    body = '''<input data-filter="q"><span data-result-count></span>
    <div data-results><table><tbody><tr data-key="fixture" data-task="1">
    <td><button data-open-diagnosis>Diagnose</button></td></tr></tbody></table></div>
    <dialog id="diagnosis-dialog"><div class="dialog-head"><button>Close</button></div>
    <strong data-dialog-title></strong><span data-dialog-stage></span><span data-dialog-step></span>
    <span data-dialog-category></span><pre data-dialog-error></pre><p data-dialog-cause></p>
    <p data-dialog-remediation></p><a data-dialog-link>Detail</a><a data-dialog-step-link>Step</a></dialog>'''
    (tmp_path / 'index.html').write_text(_shell('zh', 'index', 'Home', {'lead': '<p>Home</p>'}, heading='Home'), encoding='utf-8')
    (tmp_path / 'results.html').write_text(_shell('zh', 'results', 'Results', {'inventory': body}, heading='Results'), encoding='utf-8')
    (tmp_path / 'result').mkdir()
    (tmp_path / 'result/fixture.html').write_text(_shell('zh', 'results', 'Detail', {'inventory': '<p>Detail</p>'}, heading='Detail', depth=1, current_route='result/fixture.html'), encoding='utf-8')
    (tmp_path / 'data/evidence').mkdir(parents=True)
    (tmp_path / 'data/results.json').write_text(json.dumps([{'key':'fixture','browser':'Synthetic','task_id':1,'failure_stage':'official_evaluation'}]), encoding='utf-8')
    (tmp_path / 'data/evidence/fixture.json').write_text(json.dumps({'official_evaluation':{'error':'Expected mutation was absent'}}), encoding='utf-8')
    with hosted_preview(tmp_path) as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin + 'results.html')
            page.wait_for_selector('[data-results][data-results-ready=true]')
            page.locator('[data-open-diagnosis]').click()
            page.wait_for_selector('dialog[data-loaded=true]')
            assert 'Expected mutation was absent' in page.locator('[data-dialog-error]').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('dialog').is_visible()
            assert page.locator('[data-open-diagnosis]').evaluate('(e)=>e===document.activeElement')
            page.goto(origin + 'result/fixture.html')
            assert page.locator('link[data-report-css]').evaluate('(e)=>Boolean(e.sheet)')
            page.locator('nav a[href="../index.html"]').first.click()
            assert page.url == origin + 'index.html'
            assert page.locator('h1').inner_text() == 'Home'
        finally:
            page.close()


def test_wcxb_generated_filter_and_diagnosis_keyboard_return(tmp_path, browser, display):
    from harness.web_fetch.report import render_report
    from tests.wcxb_fixture import create_report_inputs

    inputs = create_report_inputs(tmp_path)
    root = inputs[0]
    render_report(*inputs, variant='wcxb')
    with local_report_server(root) as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin + '/analysis/index.html')
            activate_and_check_layout(page, display)
            page.locator('#scope').select_option('all')
            rows = page.locator('tr[data-provider]:visible')
            assert rows.count() > 0
            full_height = page.locator('.scroll').filter(has=page.locator('tr[data-provider]')).first.evaluate('(e)=>e.clientHeight')
            page.locator('#q').fill('impossible-fixture-query')
            assert rows.count() == 0
            empty_height = page.locator('.scroll').filter(has=page.locator('tr[data-provider]')).first.evaluate('(e)=>e.clientHeight')
            assert empty_height < full_height
            page.locator('#q').fill('')
            assert rows.count() > 0
            trigger = page.locator('[data-diagnosis-open]:visible').first
            trigger.click()
            assert page.locator('#diagnosis-dialog').is_visible()
            assert page.locator('[data-diagnosis-error]').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('#diagnosis-dialog').is_visible()
            assert trigger.evaluate('(e)=>e===document.activeElement')
        finally:
            page.close()


def test_moli_actual_result_evidence_loader_and_keyboard_return(tmp_path, browser, display):
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / 'src/harness/lexbench/template'
    html = (source / 'index.html').read_text(encoding='utf-8').replace('{{lang}}', 'zh').replace('{{base}}', '')
    html = html.replace('{{messages}}', (source / 'messages.json').read_text(encoding='utf-8'))
    html = html.replace('<script defer src="mermaid.min.js"></script>', '')
    (tmp_path / 'style.css').write_text((source / 'style.css').read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'index.html').write_text(html, encoding='utf-8')
    (tmp_path / 'app.js').write_text((source / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'data/cases').mkdir(parents=True)
    model = {'cases': [{'id': 'fixture', 'title': 'Fixture result'}], 'functional_methods': [],
             'protocol': {'k_runs': 1}, 'runs': {key: {'run_id': key} for key in ('off_baseline', 'off_engine', 'on_baseline', 'on_engine')}}
    (tmp_path / 'data/model.json').write_text(json.dumps(model), encoding='utf-8')
    modes = {}
    for mode, status in [('off', 'pass'), ('on', 'fail')]:
        modes[mode] = {'passes': int(status == 'pass'), 'attempts': [{
            'ordinal': 1, 'status': status, 'profile_status': status, 'duration_ms': 2,
            'answer': 'Fixture answer', 'resource': {},
            'failure': {'class': 'fixture', 'detail': 'Expected mutation missing'} if status == 'fail' else None,
            'evidence': {'baseline': {'log': {'url': 'data/log.txt', 'sha256': ''}}},
        }]}
    (tmp_path / 'data/cases/fixture.json').write_text(json.dumps({'id': 'fixture', 'title': 'Fixture result', 'subset': 'Fixture', 'modes': modes}), encoding='utf-8')
    (tmp_path / 'data/log.txt').write_text('Exact fixture error\nExpected mutation missing', encoding='utf-8')
    with local_report_server(tmp_path) as origin:
        page = local_page(browser, origin, **display)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            for mode in ('off', 'on'):
                page.goto(origin + '/#page=result/fixture/' + mode)
                page.wait_for_selector('main[data-route^="result/"] [data-report-detail]')
                activate_and_check_layout(page, display)
                if mode == 'on':
                    assert 'Expected mutation missing' in page.locator('.notice.warn').inner_text()
                page.locator('[data-report-detail] summary').click()
                trigger = page.locator('[data-evidence="data/log.txt"]')
                trigger.click()
                page.wait_for_selector('#evidence-text')
                assert 'Expected mutation missing' in page.locator('#evidence-text').inner_text()
                page.locator('#evidence-query').fill('mutation')
                assert page.locator('mark.evidence-match').inner_text() == 'mutation'
                page.locator('#dialog-close').focus()
                page.keyboard.press('Escape')
                assert not page.locator('#evidence-dialog').is_visible()
                assert trigger.evaluate('(e)=>e===document.activeElement')
            assert errors == []
        finally:
            page.close()


def activate_and_check_layout(page, display):
    page.wait_for_load_state('load')
    theme = display["color_scheme"]
    dark = page.evaluate("document.documentElement.dataset.theme === 'dark' || document.body.classList.contains('dark')")
    before = page.evaluate("[document.body,document.documentElement].map(e=>getComputedStyle(e).backgroundColor).join('/')")
    if dark != (theme == "dark"):
        page.locator('#theme, .theme, .theme-toggle, button[onclick="theme()"]:visible').first.click()
        page.wait_for_function(
            """({theme, before}) => {
                const dark = document.documentElement.dataset.theme === 'dark'
                    || document.body.classList.contains('dark');
                const background = [document.body, document.documentElement]
                    .map(e => getComputedStyle(e).backgroundColor).join('/');
                return dark === (theme === 'dark') && background !== before;
            }""",
            arg={"theme": theme, "before": before},
        )
    assert page.evaluate("document.documentElement.dataset.theme === 'dark' || document.body.classList.contains('dark')") == (theme == "dark")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")


@pytest.mark.parametrize('behavior', ['delayed', 'missing-state', 'missing-color'])
def test_theme_check_waits_for_visual_change_and_rejects_broken_controls(browser, behavior):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(600)
    state = "document.documentElement.dataset.theme='dark';" if behavior != 'missing-state' else ''
    color = "setTimeout(()=>document.body.style.backgroundColor='black',150);" if behavior != 'missing-color' else ''
    try:
        page.set_content(f'''<html data-theme="light"><body style="background:white">
            <button id="theme">Theme</button><script>
            document.getElementById('theme').onclick=()=>{{{state}{color}}};
            </script></body></html>''')
        display = {"color_scheme": "dark"}
        if behavior == 'delayed':
            activate_and_check_layout(page, display)
        else:
            with pytest.raises(PlaywrightTimeoutError, match='Page.wait_for_function'):
                activate_and_check_layout(page, display)
    finally:
        page.close()


def test_same_task_explorer_follows_baseline_comparisons_and_relations(tmp_path, browser):
    from harness.reporting.explorer import SCRIPT
    from tests.test_report_explorer import render_fixture

    (tmp_path / "index.html").write_text(
        f'<!doctype html><html><body>{render_fixture()}<script>{SCRIPT}</script></body></html>', encoding="utf-8")
    with local_report_server(tmp_path) as origin:
        page = local_page(browser, origin, **DESKTOP_LIGHT)
        try:
            page.goto(origin)
            visible = lambda: [r.inner_text().split()[0] for r in page.locator("tr[data-explorer-row]:visible").all()]
            assert visible() == ["t1", "t2"] and page.locator("[data-explorer-count]").inner_text() == "2 / 4"
            page.locator("[data-explorer-relation]").select_option("baseline_passes")
            assert visible() == ["t1"]
            page.locator("[data-explorer-relation]").select_option("comparison_passes")
            assert visible() == ["t2"]
            page.locator('[data-explorer-compare] input[value="c"]').uncheck()
            assert visible() == ["t2"] and not page.locator('th[data-participant="c"]').is_visible()
            page.locator("[data-explorer-relation]").select_option("all_pass")
            assert visible() == ["t1", "t3"]
            page.locator("[data-explorer-baseline]").select_option("b")
            assert page.locator('[data-explorer-compare] input[value="b"]').is_disabled()
            assert visible() == ["t1", "t3"]
            page.locator("[data-explorer-relation]").select_option("differs")
            assert visible() == ["t2"]
            page.locator("[data-explorer-filter]").select_option("easy")
            assert visible() == []
        finally:
            page.close()


def test_generated_browser_use_success_failure_and_evidence_viewer(tmp_path, monkeypatch, browser, display):
    from tests.browser_use.test_report import build_browser_use_fixture
    from harness.reporting.visual import QuietHandler

    serve = QuietHandler.do_GET

    def delayed_evidence_and_script(handler):
        if handler.path.split('?', 1)[0].endswith(('execution-evidence.json', 'assets/report.js')):
            time.sleep(0.1)
        serve(handler)

    monkeypatch.setattr(QuietHandler, 'do_GET', delayed_evidence_and_script)
    output, _, _ = build_browser_use_fixture(tmp_path, monkeypatch, failed=True)
    with local_report_server(output) as origin:
        page = local_page(browser, origin, **display)
        try:
            for method in ("chromium", "moli"):
                page.goto(origin + '/results/gpt-5.6-sol-' + method + '-44.html?report-language=zh')
                activate_and_check_layout(page, display)
                link = page.locator('a[href*="evidence-viewer.html?src="]').first
                assert link.count() == 1
                link.click()
                page.wait_for_selector('#evidence-raw-link:not([hidden])')
                evidence = page.locator('#evidence-viewer-content')
                expect(evidence).to_contain_text('AgentResponseEvaluator')
                if method == 'moli':
                    expect(evidence).to_contain_text('Fixture expected mutation missing')
                activate_and_check_layout(page, display)
        finally:
            page.close()


def test_generated_replay_success_failure_steps_and_viewer(tmp_path, monkeypatch, browser, display):
    from tests.trajectory_replay.test_webarena_report import build_replay_fixture
    output, *_ = build_replay_fixture(tmp_path, monkeypatch, failed=True)
    with hosted_preview(output) as origin:
        page = local_page(browser, origin, **display)
        try:
            for method in ('chromium', 'moli-old'):
                page.goto(origin + 'result/' + method + '-29.html?report-language=zh')
                activate_and_check_layout(page, display)
                trigger = page.locator('[data-open-step]').first
                trigger.click()
                dialog = page.locator('#step-dialog')
                assert dialog.is_visible()
                box = dialog.bounding_box()
                assert box['x'] >= -1 and box['width'] <= display['viewport']['width'] + 2
                page.wait_for_function("document.querySelector('[data-step-planned]').textContent.includes('snapshot')")
                assert 'snapshot' in dialog.inner_text()
                page.keyboard.press('Escape')
                assert not dialog.is_visible()
                assert trigger.evaluate('(e)=>e===document.activeElement')
                page.locator('a[href*="evidence-viewer.html?file="]:visible').first.click()
                page.wait_for_function("document.querySelector('[data-evidence-viewer]').textContent.length > 10")
                page.locator('[data-evidence-tab="trajectory"]').click()
                assert 'Fixture action failed' in page.locator('main').inner_text() if method == 'moli-old' else 'Fixture snapshot' in page.locator('main').inner_text()
        finally:
            page.close()


def test_web_extract_same_case_panels_keep_native_evidence_and_keyboard_return(tmp_path, browser, display):
    language = 'zh'
    from harness.web_extract.example import create
    from harness.web_extract.pipeline import digest, read_json, save_json
    from harness.web_extract.report import build

    home = create(tmp_path / 'fixture', mode='full-cohort-progress', language='en')
    model = read_json(home / 'content.json')
    method = 'synthetic-success'
    evidence = home / 'cost-disclosure.json'
    save_json(evidence, {
        'schema': 'web-extract-cost-disclosure/1', 'method': method, 'case_count': 3,
        'total_charge_receipts': 0, 'source_plan_sha256': model['cohort_plan']['sha256'],
    })
    model['prices'][method]['cost_unavailable'] = {
        'population_cases': 3, 'missing_cases': 3,
        'reason': {'en': 'Synthetic missing total bill.', 'zh': '合成的完整账单缺失场景。'},
        'evidence': {'path': 'assets/cost-disclosure.json', 'sha256': digest(evidence.read_bytes())},
    }
    model.setdefault('assets', []).append({
        'source': str(evidence), 'path': 'assets/cost-disclosure.json',
        'sha256': digest(evidence.read_bytes()),
    })
    save_json(home / 'content.json', model)
    profile = read_json(home / 'profile.json')
    profile.setdefault('research_report', {}).setdefault('content', {})['sha256'] = digest(
        (home / 'content.json').read_bytes()
    )
    save_json(home / 'profile.json', profile)
    output = tmp_path / 'report'
    build(output, home / 'content.json', home=home, profile=home / 'profile.json', language='en')
    with hosted_preview(output) as origin:
        page = local_page(browser, origin, **display)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + language + '/results.html')
            for method in ('synthetic-success', 'synthetic-blocked'):
                if display['color_scheme'] == 'dark':
                    if not page.locator('html').evaluate('(e)=>e.classList.contains("dark")'):
                        page.locator('#theme').click()
                page.locator('[data-result-filter="status"]').select_option('all')
                row = page.locator(f'tr[data-method="{method}"]').first
                row.locator('[data-call-log-open]').first.click()
                diagnosis = page.locator('dialog:visible')
                expect(diagnosis).to_be_visible()
                link = diagnosis.locator('.case-links a').first
                assert link.get_attribute('href').endswith('#' + method)
                link.click()
                panel = page.locator(f'details[data-result-panel][id="{method}"]')
                expect(panel).to_have_attribute('open', '')
                assert page.url.endswith('#' + method)
                assert panel.locator('[data-result-summary]').count() == 1
                trigger = panel.locator('[data-call-log-open]')
                trigger.click()
                dialog = panel.locator('dialog')
                expect(dialog).to_be_visible()
                expect(dialog.locator('pre')).to_contain_text('case_id')
                expect(dialog.locator('pre')).to_contain_text(method)
                if method == 'synthetic-blocked':
                    expect(panel.locator('[data-result-logs]')).to_contain_text(
                        'Synthetic route rejects archived HTML'
                    )
                page.keyboard.press('Escape')
                expect(dialog).not_to_be_visible()
                assert trigger.evaluate('(e)=>e===document.activeElement')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                page.locator(f'nav a[href="{language}/results.html"]').click()
                assert page.url == origin + language + '/results.html'
            account_link = page.locator(f'nav a[href="{language}/accounts.html"]')
            if not account_link.is_visible():
                page.locator('nav details > summary').click()
            account_link.click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
            with page.expect_response(lambda r: r.url.endswith('/assets/cost-disclosure.json')) as receipt:
                page.locator('[data-cost-evidence]').click()
            assert receipt.value.json()['method'] == 'synthetic-success'
            assert page.url == origin + 'assets/cost-disclosure.json'
            page.go_back()
            expect(page.locator('[data-price-table]')).to_be_visible()
            assert errors == []
        finally:
            page.close()


@pytest.mark.parametrize('display', [DESKTOP_LIGHT, NARROW_DARK], indirect=True)
def test_webfetch_shared_ranks_survive_sorting(tmp_path, browser, display):
    from pathlib import Path
    from bs4 import BeautifulSoup
    from harness.web_fetch import report
    from harness.web_fetch.report_saved_composition import _home_method_summary
    from harness.web_fetch.report_style import RANKING_STICKY
    from tests.test_report_body_scope import case, record
    c = case('sample', kind='body', track='web')
    records = []
    for provider, passed, elapsed in [('a', True, 90), ('b', True, 30), ('c', False, 10)]:
        row = record(c, passed=passed, elapsed=elapsed)
        row['provider'] = provider
        records.append(row)
    markup = report.overall_ranking(records, {c['id']: c}, passing_latency=True, ctx=RenderContext())
    overview = '<table id="method-overview"><tbody>' + ''.join(
        f'<tr><td><a href="providers/{provider}.html">{provider}</a><small class="method-execution">API</small></td>'
        '<td>Default</td><td>$2 / 1,000 requests</td></tr>' for provider in ('a', 'b', 'c')) + '</tbody></table>'
    page_model = BeautifulSoup(markup + overview, 'html.parser')
    _home_method_summary(page_model, {'b': {'price': '$0.09 / hour'},
        'c': {'price': '$0.10–$0.50 / $0.50–$2.50 / million tokens'}})
    markup = str(page_model)
    js = Path(report.__file__).with_name('assets').joinpath('report.js').read_text(encoding='utf-8')
    (tmp_path/'app.js').write_text(js, encoding='utf-8')
    (tmp_path/'index.html').write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<style>'+report.STYLE+RANKING_STICKY+'#ranking-overall{min-width:1500px}</style>'
        '<script defer src="app.js"></script></head><body>'+markup+'</body></html>', encoding='utf-8')
    with local_report_server(tmp_path) as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin)
            ranks = page.locator('#ranking-overall td[data-rank]')
            assert ranks.all_text_contents() == ['1', '1', '3']
            page.locator('#ranking-overall th button').last.click()
            assert sorted(ranks.all_text_contents()) == ['1', '1', '3']
            for row in page.locator('#ranking-overall tbody tr').all():
                expected = '3' if 'providers/c.html' in row.inner_html() else '1'
                assert row.locator('td').first.inner_text() == expected
            table = page.locator('#ranking-overall')
            assert table.locator('.method-price-note').count() == 2
            for amount in table.locator('.method-price-amount').all():
                assert amount.bounding_box()['width'] >= 100
            if display['viewport']['width'] >= 640:
                for note in table.locator('.method-price-note').all():
                    assert note.bounding_box()['height'] < 20
            coordinates = '''table => {
                const cells = [...table.tBodies[0].rows[0].cells];
                return cells.slice(0, 3).map(cell => cell.getBoundingClientRect().left);
            }'''
            table.evaluate('table => { table.parentElement.scrollLeft = 0; }')
            start = table.evaluate(coordinates)
            table.evaluate('table => { table.parentElement.scrollLeft = 120; }')
            after = table.evaluate(coordinates)
            assert after[:2] == pytest.approx(start[:2], abs=1)
            assert after[2] < start[2] - 100
            if display['viewport']['width'] < 640:
                assert table.locator('tbody td:nth-child(2)').first.bounding_box()['width'] <= 129
        finally:
            page.close()


def test_webfetch_single_scope_report_keeps_contestants_and_evidence(tmp_path, browser, display):
    language = 'en'
    from tests.benchmarks.webfetch.test_content_scopes import annotated_case
    from tests.test_optional_reports import invoke
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    names = ['method-a', 'method-b', 'method-c', 'method-d']
    config = tmp_path/'providers.json'
    config.write_text(json.dumps({'providers': {name: {'type': 'fixture'} for name in names}}), encoding='utf-8')
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--provider-config', config, '--providers', ','.join(names),
                    '--delay', '0', '--max-failure-retries', '0', language=language)
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8').splitlines()[0])
    with local_report_server(run/'report') as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin)
            expect(page.locator('[data-content-scope-switch], a[data-content-scope]')).to_have_count(0)
            expect(page.locator('#ranking-web tbody tr')).to_have_count(4)
            labels = page.locator('#ranking-web a[href*="providers/"]').all_text_contents()
            assert len(set(labels)) == 4
            page.goto(origin.rstrip('/')+'/attempts/'+row['attempt_id']+'.html')
            expect(page.locator('#evidence-reference')).to_contain_text('Contact publisher')
            expect(page.locator('[data-content-scope-switch], a[data-content-scope]')).to_have_count(0)
        finally:
            page.close()
    assert len(list((run/'call-logs').rglob('*.json'))) == 4


def test_webfetch_home_opens_without_catalog_and_details_stay_lazy(tmp_path, browser, display):
    """The summary works with evidence requests blocked; opening evidence still works."""
    import zipfile
    from pathlib import Path
    from harness.web_fetch import report_package
    from tests.test_webfetch_report_package import report_tree

    source, archive = report_tree(tmp_path)
    assets = Path(report_package.__file__).parent/'assets'
    (source/'assets/report.js').write_bytes((assets/'report.js').read_bytes())
    (source/'index.html').write_text(
        '<html><head><style>h1{display:none}</style>'
        '<style data-report-page-style>h1{padding-left:17px}</style></head>'
        '<body><main><h1>Summary 80.8%</h1><img src="assets/chart.svg">'
        '<a href="detail.html">Details</a></main></body></html>', encoding='utf-8')
    (source/'assets/chart.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<rect width="20" height="20" fill="green"/></svg>', encoding='utf-8')
    output = 'Exact evidence\n'*30000
    (source/'large-answer.txt').write_text(output, encoding='utf-8')
    (source/'detail.html').write_text(
        '<html><body><main><h1>Detail</h1><a href="index.html">Back</a>'
        '<pre data-file="large-answer.txt" data-readable="1"></pre></main></body></html>',
        encoding='utf-8')
    report_package.build(source, archive)
    published = tmp_path/'published'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(published)
    with local_report_server(published) as origin:
        page = local_page(browser, origin, **display)
        requests = []
        page.on('request', lambda request:requests.append(request.url))
        try:
            page.route('**/manifest.json', lambda route:route.abort())
            page.route('**/data/**', lambda route:route.abort())
            page.goto(origin)
            expect(page.get_by_role('heading', name='Summary 80.8%')).to_be_visible()
            expect(page.get_by_role('heading', name='Summary 80.8%')).to_have_css('padding-left', '17px')
            page.wait_for_function('document.querySelector("img")?.naturalWidth>0')
            assert not any('/manifest.json' in url or '/data/' in url for url in requests)
            page.unroute('**/manifest.json')
            page.unroute('**/data/**')
            page.get_by_role('link', name='Details', exact=True).click()
            expect(page.get_by_role('heading', name='Detail', exact=True)).to_be_visible()
            expect(page.locator('head style[data-report-page-style]')).to_have_count(0)
            assert any('/manifest.json' in url for url in requests)
            expect(page.locator('pre')).to_have_attribute('role', 'button')
            page.locator('pre').click()
            expect(page.locator('textarea')).to_have_value(output)
            assert any('/data/' in url for url in requests)
            page.get_by_role('link', name='Back', exact=True).click()
            expect(page.get_by_role('heading', name='Summary 80.8%')).to_be_visible()
            expect(page.get_by_role('heading', name='Summary 80.8%')).to_have_css('padding-left', '17px')
        finally:
            page.close()


def test_webfetch_extension_standalone_evidence_dialog(tmp_path, browser, display):
    from tests.test_saved_matrix_composition import _published_extension
    output, _, _, _ = _published_extension(tmp_path)
    with local_report_server(output) as origin:
        page = local_page(browser, origin, **display)
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        try:
            page.goto(origin+'/additional/providers/extra.html')
            page.get_by_role('button', name='Evidence', exact=True).click()
            expect(page.get_by_role('dialog', name='Evidence')).to_be_visible()
            expect(page.locator('dialog pre')).to_have_text('unchanged answer: extra')
            assert any(url.endswith('/additional/answer.txt') for url in requests)
            assert not any('/additional/additional/' in url for url in requests)
        finally:
            page.close()


def test_webfetch_bilingual_extension_navigation(tmp_path, browser, display):
    from tests.test_saved_matrix_composition import _real_extension, write
    from harness.web_fetch.report_saved_composition import refresh_published_extension
    output, spec_path = _real_extension(tmp_path, source_languages=('en', 'en'))
    spec = json.loads(spec_path.read_bytes())
    spec['presentation'] = 'ranked'
    write(spec_path, spec)
    refresh_published_extension(output, spec_path=spec_path)
    with local_report_server(output) as origin:
        page = local_page(browser, origin, **display)
        try:
            page.goto(origin+'/index.html')
            expect(page).to_have_url(origin+'/en/index.html')
            expect(page.locator('#ranking-overall tbody tr')).to_have_count(2)
            page.locator('a[hreflang="zh-CN"]').click()
            expect(page).to_have_url(origin+'/index.html?report-language=zh')
            expect(page.locator('#ranking-overall tbody tr')).to_have_count(2)
            page.locator('a[hreflang="en"]').click()
            expect(page.locator('#ranking-overall tbody tr')).to_have_count(2)
            page.locator('header.top').get_by_role('link', name='Analysis', exact=True).click()
            expect(page).to_have_url(origin+'/en/analysis/index.html')
            page.locator('[data-filter="result"]').select_option('')
            expect(page.locator('#filtered-table tbody tr')).to_have_count(2)
            page.locator('#filtered-table tr[data-provider="method-1"] a[href*="attempts/"]').click()
            expect(page).to_have_url(origin+'/additional/en/attempts/first--run-1.html')
            assert page.locator('html').get_attribute('lang') == 'en'
        finally:
            page.close()
