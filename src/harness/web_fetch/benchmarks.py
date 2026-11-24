"""Web Fetch benchmark adapters and their registry.

A benchmark supplies the steps that differ between task populations: which
cases a run selects, how their inputs reach the measured method (live sites or
frozen pages served on loopback), how one output is scored and which
populations the report counts. The run stage obtains the adapter from
``BENCHMARKS`` by the selected name and never branches on that name.

See docs/spec.md, "Extension points" and "One pipeline per track".
"""
from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.scoring import score_attempt

from harness.adapters import Capabilities, Registry
from harness.distribution import repository_root
from harness.record_io import bytes_sha256
from harness.web_fetch.config import DEFAULT_CALL_TIMEOUT_SECONDS

ROOT = repository_root()
EXAMPLES = Path(__file__).parent / "data"

BENCHMARKS: Registry = Registry("Web Fetch benchmark")

# Capability: inputs are frozen pages served to the method by ``inputs()``;
# the run writes one shard per method and scoring runs as a separate stage.
FROZEN_INPUTS = "frozen_inputs"


@dataclass(frozen=True)
class LiveCases:
    """Cases of a live-site dataset with the dataset declaration they came from."""

    meta: dict
    cases: list[EvalCase]

    def __iter__(self):
        return iter(self.cases)

    def as_pair(self) -> tuple[dict, list[EvalCase]]:
        return self.meta, self.cases

    def __len__(self) -> int:
        return len(self.cases)


@dataclass(frozen=True)
class FrozenCases:
    """Frozen-page records selected for one run, with their source bindings."""

    records: list[dict]
    dataset_id: str
    source_sha256: str
    selection: dict | None = None
    scorer_commit: str | None = None
    expected_versions: dict[str, str] = field(default_factory=dict)
    extra_manifest: dict = field(default_factory=dict)

    def __iter__(self):
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class ScoredOutput:
    """One method output as a frozen-input scorer reads it."""

    content: str
    ok: bool
    scorer: Any = None


class LiveSuite:
    """The default suite: tasks on live sites, scored by ``score_attempt``."""

    name = "default"
    default_dataset = EXAMPLES / "fixture-dataset.json"
    default_provider_config = EXAMPLES / "fixture-providers.json"
    default_timeout = DEFAULT_CALL_TIMEOUT_SECONDS

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def cases(self, profile: dict) -> LiveCases:
        from harness.web_fetch.config import load_dataset

        meta, cases = load_dataset(
            profile["dataset"],
            content_scope=profile.get("content_scope"),
            scoring_scope=profile.get("scoring_scope"),
        )
        return LiveCases(meta, cases)

    def inputs(self, cases):
        """Live sites need no prepared environment."""
        return nullcontext(None)

    def score(self, case: EvalCase, scored_input: dict) -> dict:
        options = dict(scored_input)
        return score_attempt(case, options.pop("content"), **options)

    def populations(self) -> list[dict]:
        """Populations come from the dataset's own declaration."""
        return []

    def views(self) -> list:
        """The shared Web Fetch report sections cover this suite."""
        return []


class FrozenPageBenchmark:
    """Shared behaviour of benchmarks whose inputs are frozen HTML pages."""

    name: str
    run_id_prefix: str
    category: str
    source_name: str
    default_provider_config = ROOT / "configs/providers.headless.json"
    default_timeout = 45.0

    def capabilities(self) -> Capabilities:
        return Capabilities(frozenset({FROZEN_INPUTS}))

    def views(self) -> list:
        """The shared Web Fetch report sections cover frozen-page benchmarks."""
        return []

    def case(self, record: dict, url: str) -> EvalCase:
        """The call input for one frozen record served at ``url``."""
        return EvalCase(
            id=record["track_id"],
            name=record["track_id"],
            url=url,
            category=self.category,
            expected_any=[],
            track="static_html",
            source_benchmark={"name": self.source_name, "original_url": record["url"]},
        )

    def inputs(self, cases, *, preserve_original_origin: bool = False):
        from harness.web_fetch.frozen_inputs import fixture_server

        return fixture_server(list(cases), preserve_original_origin)

    def resolve(self, options: argparse.Namespace) -> None:
        """Fill the benchmark's locked default inputs into unset options."""
        raise NotImplementedError

    def certify(self, argv: list[str]) -> None:
        """Run and certify the profile's complete method matrix."""
        raise NotImplementedError

    def cases(self, profile: argparse.Namespace) -> FrozenCases:
        raise NotImplementedError


class WebMainBench(FrozenPageBenchmark):
    name = "webmainbench"
    run_id_prefix = "wmb-"
    category = "webmainbench"
    source_name = "WebMainBench-545"

    def resolve(self, options: argparse.Namespace) -> None:
        from harness.web_fetch.webmainbench_dataset import selected_input

        options.dataset, options.expected_dataset_sha256, options.official_scorer_commit = selected_input(
            options.dataset, options.expected_dataset_sha256, options.official_scorer_commit)

    def certify(self, argv: list[str]) -> None:
        from harness.web_fetch.webmainbench_certification import main

        main(argv)

    def cases(self, profile: argparse.Namespace) -> FrozenCases:
        from harness.benchmarks.data import binding, dependencies
        from harness.web_fetch.webmainbench_dataset import load_selected_ids, load_source

        selected = selection = None
        if profile.case_ids_file:
            selected, selection = load_selected_ids(profile.case_ids_file, profile.expected_dataset_sha256)
        records = load_source(profile.dataset, selected, profile.expected_dataset_sha256)
        expected_versions = {}
        if profile.expected_version:
            expected_versions[profile.provider] = profile.expected_version
        locked = binding("webmainbench")
        extra = ({"benchmark_datasets": dependencies("webmainbench")}
                 if profile.expected_dataset_sha256 == locked["datasets"][locked["default_dataset"]]["sha256"]
                 else {})
        return FrozenCases(
            records=records,
            dataset_id="webmainbench-545",
            source_sha256=profile.expected_dataset_sha256,
            selection=selection,
            scorer_commit=profile.official_scorer_commit,
            expected_versions=expected_versions,
            extra_manifest=extra,
        )

    def score(self, case: dict, scored_input: ScoredOutput) -> dict:
        """Official metrics; ``scored_input.scorer`` is the prepared pinned calculator."""
        from harness.web_fetch.webmainbench_scoring import official_metrics

        if not scored_input.ok:
            return {}
        return official_metrics(scored_input.scorer, scored_input.content, case["groundtruth_content"])

    def populations(self) -> list[dict]:
        from harness.web_fetch.webmainbench_dataset import CASE_COUNT
        from harness.web_fetch.webmainbench_scoring import CORE_METRICS

        return [{"name": "webmainbench-545", "case_count": CASE_COUNT,
                 "metrics": list(CORE_METRICS)}]


class WCXB(FrozenPageBenchmark):
    name = "wcxb"
    run_id_prefix = "wcxb-"
    category = "wcxb"
    source_name = "WCXB"

    def resolve(self, options: argparse.Namespace) -> None:
        from harness.web_fetch.wcxb_dataset import selected_wcxb_input

        options.dataset, options.case_ids_file = selected_wcxb_input(
            options.dataset, options.case_ids_file, profile=options.profile)

    def certify(self, argv: list[str]) -> None:
        from harness.web_fetch.wcxb_certification import main

        main(argv)

    def cases(self, profile: argparse.Namespace) -> FrozenCases:
        from harness.web_fetch.wcxb_dataset import load_wcxb_source, validate_manifest

        if not profile.case_ids_file:
            raise ValueError("WCXB requires a frozen stratified sample manifest")
        records, source_digest = load_wcxb_source(profile.dataset, manifest=profile.case_ids_file)
        sample_bytes = profile.case_ids_file.read_bytes()
        sample = json.loads(sample_bytes)
        expected, _ = validate_manifest(records, source_digest, sample)
        selected = {record["track_id"] for record in expected}
        records = [record for record in records if record["track_id"] in selected]
        selection = {"sha256": bytes_sha256(sample_bytes), "case_count": len(records),
                     "scope": sample.get("scope"), "seed": sample.get("seed")}
        if profile.profile is None:
            raise ValueError("WCXB execution requires an explicit --profile")
        declared = json.loads(profile.profile.read_text(encoding="utf-8"))
        expected_versions = declared.get("expected_versions")
        if (not isinstance(expected_versions, dict)
                or set(expected_versions) != set(declared["providers"])
                or profile.provider not in declared["providers"]):
            raise ValueError("The run profile must declare expected_versions for every participant")
        return FrozenCases(
            records=records,
            dataset_id=sample["dataset_id"],
            source_sha256=source_digest,
            selection=selection,
            expected_versions=expected_versions,
        )

    def score(self, case: dict, scored_input: ScoredOutput) -> dict:
        from harness.web_fetch.wcxb_scoring import case_metrics

        ground_truth = case["ground_truth"]
        return case_metrics(scored_input.content, {
            "main_content": case["groundtruth_content"],
            "with": ground_truth.get("with") or [],
            "without": ground_truth.get("without") or [],
        })

    def populations(self) -> list[dict]:
        return [{"name": "wcxb", "metrics": ["precision", "recall", "f1", "with_rate", "without_rate"]}]


BENCHMARKS.register(LiveSuite.name, LiveSuite())
BENCHMARKS.register(WebMainBench.name, WebMainBench())
BENCHMARKS.register(WCXB.name, WCXB())


def frozen_benchmark_names() -> tuple[str, ...]:
    return tuple(name for name, adapter in BENCHMARKS.items()
                 if adapter.capabilities().has(FROZEN_INPUTS))
