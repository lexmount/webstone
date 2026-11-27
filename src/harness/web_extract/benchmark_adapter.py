"""The ``BenchmarkAdapter`` hooks of a registered Web Extract benchmark on a prepared home.

``dataset.py`` declares each ``ExtractBenchmark`` and ``pipeline.py`` owns collection.
The pipeline reads references and scores every method through these hooks, so a
benchmark's scorer (the pinned upstream scorer or a record adapter) is selected here,
not by the runner. An Extract benchmark scores a population, not one call: ``score``
takes the population's references and one method's predictions.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import InvalidOperation
from functools import cache, cached_property
from pathlib import Path
from typing import Any

from harness.web_extract import pipeline
from harness.web_extract.dataset import BENCHMARKS, ExtractBenchmark, benchmark_config, input_mode


@dataclass(frozen=True)
class ExtractBenchmarkAdapter:
    """One registered benchmark bound to the prepared home it reads."""

    benchmark: ExtractBenchmark
    home: Path

    @property
    def name(self) -> str:
        return self.benchmark.name

    def truth(self) -> dict[str, dict]:
        return pipeline.read_json(self.home / "dataset/dataset/ground-truth.json")

    def cases(self, profile: dict | None = None) -> list[dict[str, Any]]:
        """Frozen cases of the prepared home, in identifier order."""
        return [{"id": key, **value} for key, value in sorted(self.truth().items())]

    @contextmanager
    def inputs(self, cases: list[Any]) -> Iterator[dict[str, Any]]:
        """Frozen pages are served from the home's archive; live cases call their sources."""
        mode = input_mode(self.home)
        yield {"mode": mode, "root": self.home / "dataset/html" if mode == "snapshot" else None,
               "case_ids": [case["id"] for case in cases]}

    @cached_property
    def _upstream(self) -> dict[str, Any]:
        """The pinned upstream scorer, loaded once; prices that are not decimals match exactly."""
        scorer = pipeline.official_scorer(self.home)
        decimal = scorer["decimal_matching"]

        def price_match(gold, prediction):
            try:
                return decimal(gold, prediction)
            except InvalidOperation:
                return scorer["hard_matching"](gold, prediction)

        return {**scorer, "decimal_matching": price_match}

    def score(self, truth: dict[str, dict], predictions: dict[str, dict],
              **reference_policy: Any) -> dict[str, Any]:
        """Score one method's predictions over the given references."""
        module = benchmark_config(self.home)["adapter"]
        if module is not None:
            return module.score(truth, predictions, **reference_policy)
        return pipeline.evaluate_predictions(truth, predictions, self._upstream)

    def prediction(self, row, reference):
        """A typed-JSON adapter may validate native framing before projecting its answer."""
        if row.get("http_status") != 200:
            return None if self.benchmark.reference_format == "json" else {}
        if self.benchmark.reference_format == "json":
            module = benchmark_config(self.home)["adapter"]
            return module.scored_prediction(row, reference)
        return row.get("normalized_fields") or {}

    def populations(self) -> list[dict[str, Any]]:
        """The declared population: its denominator and the fields it scores."""
        truth = self.truth()
        if self.benchmark.reference_format == "json":
            return [{"id": self.name, "cases": len(truth),
                     "pages": len({case["task"]["page_id"] for case in truth.values()}),
                     "queries": sum(len(case["task"]["queries"]) for case in truth.values())}]
        if self.benchmark.fields:
            fields = (*self.benchmark.fields, *self.benchmark.derived_fields)
        else:
            fields = tuple(sorted({field for case in truth.values()
                                   for field in case.get("values", {})}))
        return [{"id": self.name, "cases": len(truth), "fields": list(fields)}]

    def views(self) -> list[Any]:
        return []


def adapter(home: Path | str, name: str | None = None) -> ExtractBenchmarkAdapter:
    """The adapter of the benchmark the prepared home records, or of ``name``."""
    item = BENCHMARKS.get(name) if name else benchmark_config(Path(home))["benchmark"]
    return ExtractBenchmarkAdapter(item, Path(home))


@cache
def _record_value_equivalence():
    """The one value equivalence a registered task adapter declares for record references."""
    (key,) = {module.record_value_key for _, item in BENCHMARKS.items() if item.module
              if hasattr(module := importlib.import_module(item.module), "record_value_key")}
    return key


def record_value_key(reference, field, value, text_matching="literal"):
    """Comparison key of one record value under its reference's declared value kind."""
    return _record_value_equivalence()(reference, field, value, text_matching)
