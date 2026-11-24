"""Every registered Web Fetch benchmark adapter honours the shared contract."""
import argparse
from types import SimpleNamespace

import pytest

from harness.adapters import BenchmarkAdapter
from harness.web_fetch import certification, cli
from harness.web_fetch.benchmarks import (
    BENCHMARKS,
    FROZEN_INPUTS,
    ScoredOutput,
    frozen_benchmark_names,
)

RECORD = {
    "track_id": "case-one",
    "url": "https://example.test/source?x=1",
    "html": "<html><body><p>Hello world</p></body></html>",
    "groundtruth_content": "Hello world",
    "ground_truth": {"main_content": "Hello world", "with": ["Hello"], "without": ["Absent"]},
    "meta": {},
}


@pytest.mark.parametrize("name", BENCHMARKS.names())
def test_registered_adapter_implements_the_benchmark_contract(name):
    adapter = BENCHMARKS.get(name)
    assert isinstance(adapter, BenchmarkAdapter)
    assert adapter.name == name
    assert isinstance(adapter.populations(), list)
    assert adapter.default_provider_config.is_file()
    assert adapter.default_timeout > 0


@pytest.mark.parametrize("name", frozen_benchmark_names())
def test_frozen_adapter_builds_call_inputs_from_records(name):
    adapter = BENCHMARKS.get(name)
    assert adapter.capabilities().has(FROZEN_INPUTS)
    assert adapter.run_id_prefix
    case = adapter.case(RECORD, "https://localhost:1/case/case-one")
    assert (case.id, case.url) == ("case-one", "https://localhost:1/case/case-one")
    assert case.source_benchmark["original_url"] == RECORD["url"]
    for hook in ("resolve", "cases", "certify"):
        assert getattr(type(adapter), hook) is not getattr(type(adapter).__mro__[1], hook)


def test_benchmark_choices_come_from_the_registry():
    parser = argparse.ArgumentParser()
    cli.add_run_stage_arguments(parser)
    choices = next(action.choices for action in parser._actions if action.dest == "benchmark")
    assert tuple(choices) == BENCHMARKS.names()
    assert {"default", "webmainbench", "wcxb"} <= set(BENCHMARKS.names())


def test_wcxb_score_uses_the_bound_metric_module():
    scores = BENCHMARKS.get("wcxb").score(RECORD, ScoredOutput("Hello world", True))
    assert scores == {"precision": 1.0, "recall": 1.0, "f1": 1.0, "with_rate": 1.0, "without_rate": 0.0}


def test_webmainbench_score_skips_failed_calls_and_uses_the_prepared_scorer():
    class Calculator:
        def calculate_all(self, **values):
            assert values["groundtruth_content"] == "Hello world"
            return {"text_edit": SimpleNamespace(score=1.0, success=True, details={})}

    adapter = BENCHMARKS.get("webmainbench")
    assert adapter.score(RECORD, ScoredOutput("", False)) == {}
    assert adapter.score(RECORD, ScoredOutput("Hello world", True, Calculator())) == {
        "text_edit": {"score": 1.0, "success": True, "details": {}}}


@pytest.mark.parametrize("option", [["--case-ids", "x"], ["--runs", "2"], ["--fail-on-failure"]])
def test_frozen_run_rejects_options_it_would_ignore(option, tmp_path):
    with pytest.raises(SystemExit, match="does not apply"):
        cli.main(["run", "--benchmark", "wcxb", "--providers", "moli",
                  "--output", str(tmp_path / "out"), *option])


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_run_rejects_a_limit_that_would_not_limit(limit, tmp_path):
    with pytest.raises(SystemExit, match="positive"):
        cli.main(["run", "--benchmark", "webmainbench", "--providers", "moli",
                  "--output", str(tmp_path / "out"), "--limit", limit])


def test_default_run_rejects_frozen_only_options(tmp_path):
    with pytest.raises(SystemExit, match="does not apply"):
        cli.main(["run", "--profile", str(tmp_path / "p.json"), "--output", str(tmp_path / "out")])


def test_certified_flag_selects_the_benchmark_certifier(monkeypatch):
    seen = []
    monkeypatch.setattr(certification, "main", seen.append)
    cli.main(["run", "--certified", "--benchmark", "wcxb", "--profile", "p.json"])
    assert seen == [["--benchmark", "wcxb", "--profile", "p.json"]]
