"""The base report template fixes page roles and slot order; tracks fill it with data."""
import pytest
from bs4 import BeautifulSoup

from harness.reporting.templates import (
    PAGE_ROLES,
    Page,
    SectionHook,
    TrackTemplate,
    Variant,
    check_page,
    compose,
    recorded_variant,
)


def _variant(**overrides):
    pages = tuple(Page(key=role, role=role, route=role + ".html", title=role.title()) for role in PAGE_ROLES)
    return Variant(**{"name": "default", "version": 1, "pages": pages, **overrides})


def _document(role, body):
    return BeautifulSoup(f'<main data-page-role="{role}"><h1>Title</h1>{body}</main>', "html.parser")


def test_a_variant_fills_or_declares_absent_every_role():
    _variant()
    pages = tuple(page for page in _variant().pages if page.role != "review")
    with pytest.raises(ValueError, match="every page role"):
        _variant(pages=pages)
    assert _variant(pages=pages, absent=frozenset({"review"})).absent == {"review"}
    with pytest.raises(ValueError, match="undeclared page role"):
        _variant(pages=(*pages, Page(key="extra", role="appendix", route="extra.html", title="Extra")))
    with pytest.raises(ValueError, match="repeats a page route"):
        _variant(pages=(*_variant().pages, Page(key="other", role="results", route="results.html", title="Other")))


def test_compose_orders_slots_and_refuses_new_structure():
    body = compose("home", {"summary": "<p>b</p>", "lead": "<p>a</p>", "coverage": ""})
    assert [n["data-slot"] for n in BeautifulSoup(body, "html.parser").select("[data-slot]")] == ["lead", "summary"]
    with pytest.raises(ValueError, match="no slot appendix"):
        compose("home", {"appendix": "<p>x</p>"})
    with pytest.raises(ValueError, match="undeclared slot"):
        SectionHook("view", "home", "appendix", lambda: "")


def test_page_check_rejects_wrong_role_order_and_misplaced_content():
    page = Page(key="home", role="home", route="index.html", title="Home")
    check_page(_document("home", compose("home", {"lead": '<p data-authored="0">a</p>', "findings": "<p>c</p>"})), page)
    with pytest.raises(ValueError, match="role"):
        check_page(_document("results", ""), page)
    with pytest.raises(ValueError, match="template order"):
        check_page(_document("home", '<div data-slot="findings"></div><div data-slot="lead"></div>'), page)
    with pytest.raises(ValueError, match="template order"):
        check_page(_document("home", '<div data-slot="appendix"></div>'), page)
    with pytest.raises(ValueError, match="content slot"):
        check_page(_document("home", '<div data-slot="core-comparison"><p data-authored="0">a</p></div>'), page)
    with pytest.raises(ValueError, match="content slot"):
        check_page(_document("home", '<p data-authored="0">a</p>'), page)


def test_manifest_records_and_binds_the_template_versions():
    template = TrackTemplate(track="synthetic", version=2, variants=(_variant(),))
    record = template.record("default")
    assert record == {"base": "webstone-report", "base_version": 1, "track_template": "synthetic",
                      "track_version": 2, "variant": "default", "variant_version": 1}
    assert recorded_variant({**record, "renderer": "kept"}, template).name == "default"
    for damage in ({"track_version": 1}, {"base_version": 0}, {"variant_version": 2}):
        with pytest.raises(ValueError, match="different template version"):
            recorded_variant({**record, **damage}, template)
    with pytest.raises(ValueError, match="Unregistered"):
        recorded_variant({**record, "variant": "other"}, template)
    with pytest.raises(ValueError, match="does not record its template"):
        recorded_variant("synthetic/1", template)
