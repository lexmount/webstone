"""Replay renderers keep English inline and read Chinese from the shared catalog by stable ID."""
import ast
import json
import re
import string

import pytest

from harness.reporting.source_policy import HAN
from harness.trajectory_replay.messages import MESSAGES, report_text

PACKAGE = MESSAGES.parent
RENDERERS = (
    "webarena_report.py", "failure_matrix.py", "report.py", "public_webarena/report.py",
    "core/report.py",
)
SEMANTIC_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


def _is_lookup(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "t"
    return isinstance(node.func, ast.Call) and getattr(node.func.func, "id", "") == "report_text"


def _fields(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_every_lookup_has_a_catalog_translation_with_matching_placeholders():
    catalog = json.loads(MESSAGES.read_text(encoding="utf-8"))
    sources = {}
    for name in RENDERERS:
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        for node in filter(_is_lookup, ast.walk(tree)):
            where = f"{name}:{node.lineno}"
            key, source = node.args
            assert isinstance(key, ast.Constant) and SEMANTIC_ID.fullmatch(key.value), where
            assert isinstance(source, ast.Constant) and isinstance(source.value, str), where
            assert not HAN.search(source.value), where
            assert sources.setdefault(key.value, source.value) == source.value, where
            values = {item.arg for item in node.keywords}
            assert None not in values and _fields(source.value) == values, where
            assert _fields(catalog["zh"][key.value]) <= values, where
    assert sources
    # Keys without a dot belong to the interpretation renderer's two-language catalog.
    assert {key for key in catalog["zh"] if key != "nav.other_edition" and "." in key} == set(
        sources
    )
    undotted = {key for key in catalog["zh"] if "." not in key}
    assert set(catalog["en"]) == undotted | {"nav.other_edition"}


def test_english_is_source_text_and_chinese_comes_from_the_catalog():
    english, chinese = report_text("en"), report_text("zh")
    assert english("unit.seconds", "{seconds:.1f} s", seconds=1.25) == "1.2 s"
    assert chinese("unit.seconds", "{seconds:.1f} s", seconds=1.25) == "1.2 秒"
    assert english("no.such.id", "Source only") == "Source only"
    with pytest.raises(ValueError, match="zh/no.such.id"):
        chinese("no.such.id", "Source only")
