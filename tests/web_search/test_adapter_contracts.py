"""Every registered Search adapter and benchmark satisfies the shared extension contracts."""
import pytest

from harness.adapters import BenchmarkAdapter, MethodAdapter
from harness.web_search.adapters import ADAPTERS
from harness.web_search.benchmarks import BENCHMARKS, benchmark_of, catalog_keys, ordered


@pytest.mark.parametrize("name", ADAPTERS.names())
def test_search_adapters_declare_the_method_contract(name):
    adapter = ADAPTERS.get(name)
    assert isinstance(adapter, MethodAdapter)
    assert adapter.name == name
    assert adapter.implementation_identity()["adapter"] == name
    assert adapter.validate() == []


@pytest.mark.parametrize("name", BENCHMARKS.names())
def test_search_benchmarks_declare_the_benchmark_contract(name):
    benchmark = BENCHMARKS.get(name)
    assert isinstance(benchmark, BenchmarkAdapter)
    assert benchmark.name == name
    population = benchmark.populations()[0]
    assert population["id"] == name and all(population["metrics"][level] for level in ("l1", "l2", "l3"))
    assert benchmark.text["column"] and set(benchmark.text["purpose"]) == {"l1", "l2", "l3"}


def test_each_case_belongs_to_exactly_one_benchmark_in_declared_order():
    cases = [{"id": "open", "query": "q"}, {"id": "fact", "query": "q", "reference_answer": "a"}]
    assert [benchmark_of(case).name for case in cases] == [item.name for item in ordered()]
    assert {item.label for item in ordered()} <= catalog_keys()
