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
