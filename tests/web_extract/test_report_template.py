"""Every Extract report shape is a variant of one track template, checked page by page."""
import pytest
from bs4 import BeautifulSoup

from harness.reporting.templates import PAGE_ROLES
from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import _check_rendered_report, build, check
from harness.web_extract.template import MODE_VARIANTS, TEMPLATE


def rebind(output, route, transform):
    path = output / route
    path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"][route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)


def test_every_mode_records_a_registered_variant_that_covers_the_base_roles():
    from harness.reporting.templates import resolve_template

    assert set(MODE_VARIANTS.values()) == {variant.name for variant in TEMPLATE.variants}
    assert resolve_template("web-extract")["variants"] == [item.name for item in TEMPLATE.variants]
    for variant in TEMPLATE.variants:
        assert {page.role for page in variant.pages} | variant.absent == set(PAGE_ROLES)


@pytest.mark.parametrize("mode", ["compatibility", "field-extraction-cohort"])
def test_report_pages_follow_their_recorded_template(tmp_path, mode):
    output = tmp_path / "report"
    create(output, mode=mode)
    manifest = read_json(output / "report-manifest.json")
    assert manifest["template"]["variant"] == MODE_VARIANTS[mode]
    _check_rendered_report(output)

    def reorder(raw):
        # An authored lead after the generated comparison breaks the home slot order.
        return raw.replace("</main>", '<div data-slot="lead"><p>Late lead</p></div></main>')

    original = (output / "zh/index.html").read_text(encoding="utf-8")
    rebind(output, "zh/index.html", reorder)
    with pytest.raises(ValueError, match="template order"):
        _check_rendered_report(output)
    rebind(output, "zh/index.html", lambda raw: original.replace(
        'data-page-role="home"', 'data-page-role="results"'))
    with pytest.raises(ValueError, match="template role"):
        _check_rendered_report(output)
    rebind(output, "zh/index.html", lambda raw: original)
    manifest["template"]["track_version"] = 0
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="different template version"):
        _check_rendered_report(output)


def test_product_report_builds_from_the_run_alone(tmp_path):
    home = create(tmp_path / "authored")
    output = tmp_path / "data-only"
    run = home / "runs" / read_json(home / "content.json")["evidence_runs"][0]["run"]
    build(output, None, home=home, run=run, profile=home / "profile.json")
    assert check(output, profile=home / "profile.json", home=home)
    manifest = read_json(output / "report-manifest.json")
    assert manifest["content_sha256"] is None and manifest["prices"] == {}
    accounts = BeautifulSoup((output / "zh/accounts.html").read_text(encoding="utf-8"),
                             "html.parser")
    assert "未绑定价格说明" in accounts.select_one("[data-price-table]").get_text()
    with pytest.raises(ValueError, match="declared price contract"):
        check(output, profile=home / "profile.json", home=home, release=True,
              visual_receipt=tmp_path / "unused.json")


def test_review_intro_leads_the_review_page_and_empty_core_slots_fail_placement(tmp_path):
    from harness.web_extract.report import intro_is_correctly_placed, page

    rendered = BeautifulSoup(page("zh", "review", "Review", "<p>Generated</p>",
                                  languages=("zh",), intro="<p>Authored</p>"), "html.parser")
    intro = rendered.select_one("[data-page-intro]")
    assert [n["data-slot"] for n in rendered.select("main > [data-slot]")] == ["summary", "notes"]
    assert intro_is_correctly_placed(rendered, intro, "review.html", "compatibility")

    document = BeautifulSoup(
        '<main data-page-role="home"><div data-slot="core-comparison"></div>'
        '<div data-slot="summary"><section data-page-intro></section></div></main>', "html.parser")
    assert not intro_is_correctly_placed(
        document, document.select_one("[data-page-intro]"), "index.html", "field-extraction-cohort")


def test_titles_name_pages_and_cases_and_unreturned_calls_never_match(tmp_path):
    output = tmp_path / "report"
    create(output)
    case = "zh/case-" + "1" * 64 + ".html"
    page = BeautifulSoup((output / case).read_text(encoding="utf-8"), "html.parser")
    assert page.h1.get_text() == "同题方法比较 · example.org/product/1"
    index = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    assert index.h1.get_text() == "合成示例 · 总览"
    blocked = page.select_one('[data-report-detail="failed"] [data-field-diagnostics]')
    assert {line.select("td")[3].get_text() for line in blocked.select("tbody tr")} == {"未返回"}
    results = (output / "zh/results.html").read_text(encoding="utf-8")
    assert 'field-not_returned' in results

    original_case = (output / case).read_text(encoding="utf-8")
    original_index = (output / "zh/index.html").read_text(encoding="utf-8")
    for route, raw in [
        (case, original_case.replace("example.org/product/1", "1" * 12)),
        ("zh/index.html", original_index.replace("合成示例 · 总览", "合成示例 · index")),
    ]:
        rebind(output, route, lambda _, raw=raw: raw)
        with pytest.raises(ValueError, match="hash or route name"):
            _check_rendered_report(output)
    # A numeric product path in a page address is a reader label, not an internal hash.
    rebind(output, case, lambda _: original_case.replace(
        "example.org/product/1", "example.org/product/123456789012"))
    rebind(output, "zh/index.html", lambda _: original_index)
    _check_rendered_report(output)
    rebind(output, case, lambda _: original_case)

    rebind(output, case, lambda raw: raw.replace("<td>未返回</td>", "<td>符合标注</td>", 1))
    with pytest.raises(ValueError, match="other than not returned"):
        _check_rendered_report(output)
    rebind(output, case, lambda _: original_case)
    rebind(output, "zh/results.html", lambda raw: raw.replace(
        'field-button field-not_returned', 'field-button field-correct', 1))
    with pytest.raises(ValueError, match="other than not returned"):
        _check_rendered_report(output)


def test_comparisons_rank_by_primary_metric_with_ties_by_name():
    from harness.web_extract.report import check_ranked_comparisons, ranked, record_comparison

    labels = {"a": "Zeta", "b": "Alpha", "c": "Beta", "d": "Gamma"}
    values = {"a": 0.8, "b": None, "c": 0.8, "d": 0.9}
    order = ranked(list(labels), values, labels)
    assert order == ["d", "c", "a", "b"]

    def view(methods):
        rows = [[f"<span data-method-label>{labels[m]}</span>", str(values[m])] for m in methods]
        html = record_comparison(["Method", "F1"], rows,
                                 {1: ("f1", "max", [values[m] for m in methods])}, rank_column=1)
        return BeautifulSoup(html, "html.parser")

    check_ranked_comparisons(view(order))
    for wrong in (["c", "d", "a", "b"], ["d", "a", "c", "b"], ["b", "d", "c", "a"]):
        with pytest.raises(ValueError, match="primary metric"):
            check_ranked_comparisons(view(wrong))


@pytest.mark.parametrize("mode", ["compatibility", "field-extraction-cohort"])
def test_header_speaks_the_page_language_and_theme_toggle_exposes_state(tmp_path, mode):
    output = tmp_path / "report"
    create(output, mode=mode)
    route = "zh/index.html"
    page = BeautifulSoup((output / route).read_text(encoding="utf-8"), "html.parser")
    assert page.select_one(".page-head .eyebrow").get_text() == "Web Extract · 结构化字段提取"
    toggle = page.select_one("header.shell-top [data-shell-theme]")
    assert toggle["aria-pressed"] == "false" and toggle["aria-label"] == "深色主题"
    original = (output / route).read_text(encoding="utf-8")
    for broken in (
        original.replace("Web Extract · 结构化字段提取", "WEB EXTRACT / STRUCTURED EXTRACTION"),
        original.replace(' aria-pressed="false"', ""),
    ):
        rebind(output, route, lambda _, broken=broken: broken)
        with pytest.raises(ValueError, match="eyebrow must be localized|theme control"):
            _check_rendered_report(output)


def test_client_script_carries_no_product_field_list():
    import json

    from harness.web_extract.product_fields import REPORTED
    from harness.web_extract.report_ui import ASSETS, JS

    source = (ASSETS / "report.js").read_text(encoding="utf-8")
    assert not [field for field in REPORTED if field.lower() in source.lower()]
    assert "const REPORT_FIELDS = " + json.dumps(list(REPORTED)) + ";" in JS
