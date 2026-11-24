"""The ``BenchmarkAdapter`` hooks of a registered Web Extract benchmark on a prepared home.

``dataset.py`` declares each ``ExtractBenchmark`` and ``pipeline.py`` owns preparation
and scoring, but both files' bytes are bound into frozen cohort plans and run contracts
(``dataset_registry_sha256``, ``pipeline_sha256``). The hooks therefore live here and
delegate to those owners, so adding them changes no binding. An Extract benchmark scores
a population, not one call: ``score`` takes the population's references and one
method's predictions, as the pinned upstream scorer and the record adapters do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.web_extract.dataset import BENCHMARKS, ExtractBenchmark, benchmark_config, input_mode
from harness.web_extract.pipeline import read_json, score_rows

# The method name under which ``score`` passes one method's predictions to the pipeline.
_SCORED = "scored"


@dataclass(frozen=True)
class ExtractBenchmarkAdapter:
    """One registered benchmark bound to the prepared home it reads."""

    benchmark: ExtractBenchmark
    home: Path

    @property
    def name(self) -> str:
        return self.benchmark.name

    def truth(self) -> dict[str, dict]:
        return read_json(self.home / "dataset/dataset/ground-truth.json")

    def cases(self, profile: dict | None = None) -> list[dict[str, Any]]:
        """Frozen cases of the prepared home, in identifier order."""
        return [{"id": key, **value} for key, value in sorted(self.truth().items())]

    @contextmanager
    def inputs(self, cases: list[Any]) -> Iterator[dict[str, Any]]:
        """Frozen pages are served from the home's archive; live cases call their sources."""
        mode = input_mode(self.home)
        yield {"mode": mode, "root": self.home / "dataset/html" if mode == "snapshot" else None,
               "case_ids": [case["id"] for case in cases]}

    def score(self, truth: dict[str, dict], predictions: dict[str, dict]) -> dict[str, Any]:
        """Score one method's predictions over the given references."""
        rows = [{"method": _SCORED, "case_id": case_id, "http_status": 200,
                 "normalized_fields": fields} for case_id, fields in predictions.items()]
        return score_rows(self.home, truth, rows, [_SCORED])[_SCORED]

    def populations(self) -> list[dict[str, Any]]:
        """The declared population: its denominator and the fields it scores."""
        truth = self.truth()
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
