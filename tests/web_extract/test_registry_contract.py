"""Every registered Web Extract method, engine and benchmark declares its contract."""

import importlib

import pytest

from harness.adapters import MethodAdapter
from harness.web_extract.dataset import BENCHMARKS, fixed_field_benchmark, review_prompt
from harness.web_extract.pipeline import METHODS
from harness.web_extract.self_hosted import ENGINES


@pytest.mark.parametrize("name", METHODS.names())
def test_method_adapters_declare_request_projection_and_identity(name):
    method = METHODS.get(name)
    assert isinstance(method, MethodAdapter) and method.name == name
    assert method.input in ("html", "url")
    assert callable(method.request) and callable(method.project)
    assert method.capabilities().value("input") == method.input
    assert method.implementation_identity()["method"] == name
    assert not method.requires_custom_schema or method.custom_schema
    assert method.project({}, False) is None
    if method.self_hosted:
        assert method.engine in ENGINES


@pytest.mark.parametrize("name", ENGINES.names())
def test_engines_declare_extract_and_failure_functions(name):
    engine = ENGINES.get(name)
    assert callable(engine.extract) and callable(engine.version)
    assert engine.failure({}) == (None, [])


@pytest.mark.parametrize("name", BENCHMARKS.names())
def test_benchmarks_declare_task_shape_and_review_prompts(name):
    benchmark = BENCHMARKS.get(name)
    assert benchmark.name == name
    assert bool(benchmark.module) != bool(benchmark.fields)
    if benchmark.module:
        module = importlib.import_module(benchmark.module)
        for hook in ("prepare", "task_schema", "probe_cases", "score"):
            assert callable(getattr(module, hook))
    else:
        assert tuple(benchmark.schema["properties"]) == benchmark.fields
        assert benchmark.instructions and callable(benchmark.valid_value)
    for schema, prompt in benchmark.review_prompts.items():
        assert "{path}" in prompt and review_prompt(schema) == prompt


def test_one_single_schema_benchmark_owns_unlabelled_homes():
    assert fixed_field_benchmark().fields
    with pytest.raises(ValueError, match="No benchmark declares"):
        review_prompt("unknown-review/1")
