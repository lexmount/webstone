"""Reader-facing names and one-language pages: positive cases and negative controls."""

import pytest

from harness.reporting.names import check_display_names, check_interface_language, display_name
from harness.reporting.presentation import check_one_language, check_presentation, drop_other_edition

LABELS = {"moli-pr643": "Moli PR 643", "chromium": {"zh": "Chromium", "en": "Chromium"}, "exa": "exa"}


def page(body, lang="zh-CN"):
    return f'<html lang="{lang}"><body><main>{body}</main></body></html>'


def test_display_name_reads_the_registry_and_keeps_unregistered_names():
    assert display_name(LABELS, "moli-pr643") == "Moli PR 643"
    assert display_name(LABELS, "chromium", "zh") == "Chromium"
    assert display_name(LABELS, "unregistered") == "unregistered"


def test_body_cells_show_display_names():
    check_display_names(page("<table><tr><td>Moli PR 643</td><td>exa</td></tr></table>"), LABELS)
    # Ids stay in links, attributes and verbatim evidence.
    check_display_names(page('<table><tr><td><a href="providers/moli-pr643.html" data-id="chromium">Moli PR 643</a>'
                             '</td><td><code>moli-pr643</code></td></tr></table>'), LABELS)
    check_display_names(page("<table><tr><td><!--moli-pr643-->Moli PR 643</td></tr></table>"), LABELS)
    for cell in ("moli-pr643", "Model A · chromium", "<a href='x'>chromium</a>", "<span>chrom</span>ium"):
        with pytest.raises(ValueError, match="raw id"):
            check_display_names(page(f"<table><tr><td>{cell}</td></tr></table>"), LABELS)


def test_display_names_resolve_in_the_page_language():
    labels = {"foo": {"zh": "中文名称", "en": "foo"}, "bar": {"zh": "bar", "en": "Bar"}}
    with pytest.raises(ValueError, match="raw id 'foo'"):
        check_display_names(page("<table><tr><td>foo</td></tr></table>"), labels)
    check_display_names(page("<table><tr><td>foo</td></tr></table>", lang="en"), labels)
    check_display_names(page("<table><tr><td>bar</td></tr></table>"), labels)
    with pytest.raises(ValueError, match="raw id 'bar'"):
        check_display_names(page("<table><tr><td>bar</td></tr></table>", lang="en"), labels)


def test_chinese_pages_name_their_interface_in_chinese():
    check_interface_language(page("<table><tr><th>均值</th><th>P50</th><th>F1</th></tr></table>"))
    check_interface_language(page("<h1>Moli 1.1.3 · Chromium</h1>"))
    check_interface_language(page('<h1><span data-verbatim>Task static-title</span></h1>'))
    check_interface_language(page('<h2 lang="en">Mean</h2>'))
    check_interface_language(page("<th>Mean</th>", lang="en"))
    check_interface_language(page('<button translate="no">Next</button>'))
    check_interface_language(page('<h2><span lang="en">Results</span></h2>'))
    check_interface_language(page('<h2><span translate="no">Results</span> 汇总</h2>'))
    check_interface_language(page("<h1>Chromium<!-- Status --></h1>"))
    for chrome in ("<h1>Results<!-- 中文 --></h1>", "<th>Mean</th>", "<h1>GPT · Task #44</h1>", "<option>Passed</option>", "<button>Back</button>"):
        with pytest.raises(ValueError, match="Untranslated interface"):
            check_interface_language(page(chrome))


def test_presentation_gate_rejects_untranslated_interface_under_the_shared_header():
    header = ('<header class="shell-top"><nav aria-label="报告页面"><a class="shell-brand" href="#">R</a>'
              '<div class="shell-links"></div><button data-shell-theme aria-label="深色主题" aria-pressed="false">◐</button>'
              '</nav></header>')
    check_presentation(page(header + "<table><tr><th>均值</th></tr></table>"))
    with pytest.raises(ValueError, match="Untranslated interface"):
        check_presentation(page(header + "<table><tr><th>Mean</th></tr></table>"))


def test_one_language_per_page():
    bilingual = page('<p><span data-report-en="Pass">通过</span></p>')
    with pytest.raises(ValueError, match="other language"):
        check_one_language(bilingual)
    single = drop_other_edition(bilingual)
    assert single == page("<p><span>通过</span></p>")
    check_one_language(single)
