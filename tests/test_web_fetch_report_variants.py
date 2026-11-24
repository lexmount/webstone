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
