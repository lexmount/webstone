"""The Web Fetch variant registry selects every build, check and package rule."""

import json

import pytest

from harness.reporting.templates import resolve_template
from harness.web_fetch.report_variants import (
    VARIANTS,
    BuildRequest,
    VariantUsageError,
    select,
    variant_for_kind,
)


def test_template_registry_lists_the_registered_variants():
    assert resolve_template("webfetch")["variants"] == list(VARIANTS)


def test_profile_selects_by_declared_variant_then_comparison_kind(tmp_path):
    profile = tmp_path / "profile.json"
    for data, expected in [({}, "certified"), ({"variant": "diagnostic-cohorts"}, "diagnostic-cohorts"),
                           ({"comparison_kind": "wcxb_frozen_html_to_markdown"}, "wcxb")]:
        profile.write_text(json.dumps(data), encoding="utf-8")
        request = BuildRequest(profile=profile, evidence=tmp_path, output=tmp_path / "out")
        assert select(request).name == expected
    with pytest.raises(VariantUsageError, match="selects report variant wcxb, not certified"):
        select(BuildRequest(profile=profile, evidence=tmp_path), "certified")


def test_profile_with_an_unregistered_variant_is_a_usage_error(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"variant": "diagnostic-cohort"}), encoding="utf-8")
    with pytest.raises(VariantUsageError, match="Unregistered Web Fetch report variant"):
        select(BuildRequest(profile=profile, evidence=tmp_path, output=tmp_path / "out"))


def test_variant_check_rejects_options_it_would_ignore(tmp_path, capsys):
    from harness.web_fetch.report_check import main
    for option in ("--source-dataset", "--presentation-binding", "--visual-receipt", "--source-spec"):
        with pytest.raises(SystemExit):
            main([str(tmp_path), "--variant", "webmainbench-sample", option, str(tmp_path / "x")])
        assert "does not check " + option in capsys.readouterr().err


def test_variant_rejects_undeclared_and_missing_arguments(tmp_path):
    with pytest.raises(VariantUsageError, match="does not accept: --evidence"):
        select(BuildRequest(input=tmp_path, output=tmp_path, evidence=tmp_path), "saved-matrix")
    with pytest.raises(VariantUsageError, match="requires: --input"):
        select(BuildRequest(output=tmp_path), "refresh-review")


def test_each_comparison_kind_selects_one_variant():
    kinds = [kind for item in VARIANTS.values() for kind in item.comparison_kinds]
    assert len(kinds) == len(set(kinds))
    assert variant_for_kind(None).name == "certified"
    for item in VARIANTS.values():
        for kind in item.comparison_kinds:
            assert variant_for_kind(kind) is item


def test_every_build_renders_a_registered_template_variant():
    from harness.web_fetch.template import TEMPLATE
    names = {item.name for item in TEMPLATE.variants}
    assert {item.template_variant for item in VARIANTS.values()} == names


def _template_report(root, variant="certified"):
    from harness.web_fetch.template import TEMPLATE, report_template
    for page in TEMPLATE.variant(variant).pages:
        target = root / page.route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'<main data-page-role="{page.role}"><h1>{page.title}</h1></main>', encoding="utf-8")
    (root / "report-languages.json").write_text('{"languages": ["zh"], "primary_language": "zh"}',
                                                 encoding="utf-8")
    return {"template": report_template(variant)}


def test_release_check_rejects_pages_that_leave_their_template(tmp_path):
    from harness.web_fetch.template import check_template_pages
    manifest = _template_report(tmp_path)
    assert check_template_pages(tmp_path, manifest).name == "certified"
    (tmp_path / "guide/index.html").write_text('<main data-page-role="results"></main>', encoding="utf-8")
    with pytest.raises(ValueError, match="does not fill its template role interpretation"):
        check_template_pages(tmp_path, manifest)
    _template_report(tmp_path)
    (tmp_path / "index.html").write_text(
        '<main data-page-role="home"><div data-slot="summary"></div><div data-slot="lead"></div></main>',
        encoding="utf-8")
    with pytest.raises(ValueError, match="differ from the home template order"):
        check_template_pages(tmp_path, manifest)
    with pytest.raises(ValueError, match="different template version"):
        check_template_pages(tmp_path, {"template": {**manifest["template"], "variant_version": 0}})
    with pytest.raises(ValueError, match="no recorded variant"):
        check_template_pages(tmp_path, {"template": None})
    _template_report(tmp_path)
    (tmp_path / "review/index.html").unlink()
    with pytest.raises(ValueError, match="missing its review page"):
        check_template_pages(tmp_path, manifest)


def test_report_suites_must_match_case_report_families():
    from harness.web_fetch.report_model import report_model
    cases = [{"id": "a", "track": "web"}, {"id": "b", "track": "dynamic"}]
    report_model({"cases": cases}, [], suites=[{"id": "web", "label": "Web", "case_ids": ["a"]},
                                               {"id": "dynamic", "label": "Dynamic", "case_ids": ["b"]}])
    with pytest.raises(ValueError, match="report family"):
        report_model({"cases": cases}, [], suites=[{"id": "custom", "label": "All", "case_ids": ["a", "b"]}])


def test_sample_guide_check_reads_the_role_marked_main(tmp_path):
    from harness.web_fetch.webmainbench_report import _wmb_write
    from harness.web_fetch.webmainbench_report_check import bound_guide
    _wmb_write(tmp_path, "guide/index.html", "Guide", "<p>bound guide</p>")
    page = (tmp_path / "guide/index.html").read_text(encoding="utf-8")
    assert 'data-page-role="interpretation"' in page
    assert bound_guide(page) == "<p>bound guide</p>"


def test_benchmark_page_sets_are_views_on_the_one_render_entry(tmp_path):
    from harness.reporting.templates import PageSetHook
    from harness.web_fetch.benchmarks import BENCHMARKS
    from harness.web_fetch.template import TEMPLATE
    views = {hook.key for _, benchmark in BENCHMARKS.items() for hook in benchmark.views()
             if isinstance(hook, PageSetHook)}
    assert views == {"wcxb", "webmainbench-sample", "webmainbench-full"}
    assert views <= {variant.name for variant in TEMPLATE.variants}


def test_benchmark_page_set_records_and_fills_its_variant(tmp_path):
    from harness.web_fetch.report import render_report
    from harness.web_fetch.template import check_recorded_template
    from tests.wcxb_fixture import create_report_inputs
    inputs = create_report_inputs(tmp_path, english_source=True)
    render_report(*inputs, include_english=True, variant="wcxb")
    manifest = json.loads((inputs[0] / "report-manifest.json").read_text(encoding="utf-8"))
    assert manifest["template"]["variant"] == "wcxb"
    check_recorded_template(inputs[0])
