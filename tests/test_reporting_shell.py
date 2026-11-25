"""The shared report shell: one header, theme bootstrap, table cues, labels and unit formats."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting import shell
from harness.reporting.locales import format_message, plural
from harness.reporting.presentation import check_labels, check_presentation, milliseconds, percent, unit_number, with_unit

LINKS = [(f"p{n}.html", f"Page {n}") for n in range(8)]


def page(body="", *, lang="en", links=LINKS, switch=("../zh/index.html", "zh")):
    header = shell.header(language=lang, brand="Track", home="index.html", links=links, current="p7.html",
                          switch=switch)
    return shell.page(lang=lang, base="./", title="T", heading="Heading", style="", script="", header=header,
                      eyebrow="", notice="", role="home", body=body, dialog="")


def test_header_keeps_six_inline_pages_and_moves_the_rest_under_more():
    soup = BeautifulSoup(page(), "html.parser")
    assert [a["href"] for a in soup.select(".shell-links > a")] == [f"p{n}.html" for n in range(6)]
    more = soup.select_one("details.shell-more")
    assert more.has_attr("data-current") and more.summary.get_text() == "More"
    assert more.select_one('a[aria-current="page"]')["href"] == "p7.html"
    assert soup.select_one('.shell-controls a[hreflang="zh-CN"]').get_text() == "中文"
    assert soup.select_one("button[data-shell-theme]")["aria-label"]
    check_presentation(soup)


def test_header_follows_declared_primary_pages_and_single_language_reports():
    links = [("a.html", "A", True), ("b.html", "B", False)]
    soup = BeautifulSoup(page(lang="zh-CN", links=links, switch=None), "html.parser")
    assert [a.get_text() for a in soup.select(".shell-links > a")] == ["A"]
    assert soup.select_one(".shell-more summary").get_text() == "更多"
    assert soup.select_one(".shell-controls a") is None
    assert soup.select_one("[data-table-hint]").get_text() == "左右滑动查看完整表格"


def test_page_carries_one_theme_bootstrap_and_token_set():
    html = page()
    assert html.count("localStorage.getItem('report-theme')") == 1
    assert "prefers-color-scheme: dark" in html
    assert ':root[data-theme="dark"]' in html
    linked = shell.page(lang="en", base=None, title="T", heading="H", style="", script="", header="",
                        eyebrow="", notice="", role="home", body="", dialog="", shell_css=False)
    assert ':root[data-theme="dark"]' not in linked and "report-theme" in linked


@pytest.mark.parametrize("body, message", [
    ("<button></button>", "accessible name"),
    ("<h2> </h2>", "Empty h2"),
    ("<details><summary></summary></details>", "Empty summary"),
    ("<table><tr><th>&lt;span data-report-en=&quot;x&quot;&gt;y&lt;/span&gt;</th></tr></table>", "Escaped markup"),
])
def test_shell_pages_reject_unnamed_controls_empty_headings_and_escaped_markup(body, message):
    check_presentation(page("<p>ok</p>"))
    with pytest.raises(ValueError, match=message):
        check_presentation(page(body))


@pytest.mark.parametrize("body, message", [
    ("<p>1 tasks passed</p>", "Plural noun"),
    ("<p>Overall basis:Every method</p>", "run together"),
])
def test_label_check_rejects_plural_and_separator_bugs(body, message):
    check_labels(page("<p>1 task passed; basis: every method</p>"))
    with pytest.raises(ValueError, match=message):
        check_labels(page(body))


def test_label_rules_skip_evidence_and_keep_chinese_counts_apart():
    check_presentation(page("<dialog><h2></h2></dialog>"))
    check_labels(page("<pre>1 tasks</pre><p>1/1 queries · 2 tasks</p>"))
    with pytest.raises(ValueError, match="Untranslated English count"):
        check_labels(page("<p>3 calls</p>", lang="zh-CN"))
    check_labels(page("<p>3 次调用</p>", lang="zh-CN"))


def test_header_shape_is_checked():
    html = page()
    with pytest.raises(ValueError, match="shared header"):
        check_presentation(html.replace("</header>", "</header><header class=\"shell-top\"></header>", 1))
    with pytest.raises(ValueError, match="theme control"):
        check_presentation(html.replace(" data-shell-theme", "", 1))


def test_empty_table_renders_one_empty_state_and_action_columns_stay_unsortable():
    assert shell.table(["A", "B"], [], empty="Nothing yet.") == shell.empty_state("Nothing yet.")
    assert "<table" in shell.table(["A", "B"], [])
    html = shell.table(["Name", "Score", "Open"], [shell.cells(["x", "1", "<a href=#>go</a>"])],
                       action_columns=(2,))
    check_presentation(html)
    with pytest.raises(ValueError, match="Action column"):
        check_presentation(html.replace("<th data-unsortable>Open", '<th data-unsortable><button data-sort-column="2">Open</button>'))


def test_a_highlight_needs_a_contest():
    assert shell.numeric_winners([{"v": 3}, {"v": None}], {"v": "max"}) == {}
    assert shell.numeric_winners([{"v": 3}, {"v": 1}], {"v": "max"}) == {"v": 3}
    single = shell.numeric_cell("3", 3, "score", "max", winner=True)
    with pytest.raises(ValueError, match="highlight"):
        check_presentation("<table><tr>" + single + "</tr></table>")
    check_presentation("<table><tr>" + shell.numeric_cell("3", 3, "score", "max") + "</tr></table>")


def test_plural_forms_and_unit_formats():
    assert format_message("{n} {n|task|tasks}", {"n": 1}) == "1 task"
    assert format_message("{n:,} {n|call|calls}", {"n": 1200}) == "1,200 calls"
    assert plural(0, "query", "queries") == "queries"
    assert milliseconds(200) == "200 ms" and milliseconds(1234.5) == "1,234 ms"
    assert percent(1) == "100.0%" and percent(None) == "—"
    assert unit_number(1.0, "s") == "1.00" and with_unit(2, "×") == "2.00×"


def test_quoted_source_text_is_not_held_to_report_label_rules():
    quoted = '<p data-verbatim>Task: open &lt;span&gt;3 pages&lt;/span&gt; and 1 tasks</p>'
    check_presentation(page(quoted, lang="zh-CN"))
    check_labels(page(quoted, lang="zh-CN"))
    check_labels(page('<p lang="en">Open the 3 pages</p>', lang="zh-CN"))
    check_presentation(page("<p>Reviewed reason: &lt;a href=x&gt;link&lt;/a&gt;</p>"))
    with pytest.raises(ValueError, match="Escaped markup"):
        check_presentation(page("<table><tr><th>&lt;span&gt;x&lt;/span&gt;</th></tr></table>"))


def test_accessible_names_resolve_labelledby_and_comments_are_not_labels():
    check_presentation(page('<span id="name">Open</span><button aria-labelledby="name"></button>'))
    for broken in ('<button aria-labelledby="missing"></button>',
                   '<span id="empty"></span><button aria-labelledby="empty"></button>'):
        with pytest.raises(ValueError, match="accessible name"):
            check_presentation(page(broken))
    check_labels(page("<!-- 1 tasks --><p>1 task</p>"))


@pytest.mark.parametrize("text", ["1 task", "1 call", "1 result", "2 queries", "1 family ranking"])
def test_chinese_pages_reject_english_counts_in_both_forms(text):
    with pytest.raises(ValueError, match="Untranslated English count"):
        check_labels(page(f"<p>{text}</p>", lang="zh-CN"))


def test_web_fetch_extension_controls_serve_pre_shell_pages(tmp_path):
    from harness.web_fetch.report_package import _extension_controls
    from harness.web_fetch.report_i18n import localize_html
    from pathlib import PurePosixPath

    script = _extension_controls().decode("utf-8")
    assert "function theme()" in script and "data-shell-css" in script and shell.SCRIPT in script
    english = localize_html(page(lang="zh-CN").replace(
        '<header class="shell-top"', '<header class="shell-top" data-x', 1), PurePosixPath("datasets/index.html"))
    current = BeautifulSoup(english, "html.parser").select_one('.shell-top a[aria-current="page"]')
    assert current is not None and current.get_text() == "Dataset"
