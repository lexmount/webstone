"""Export a scored Web Search run as a result submission for one of its benchmarks."""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from harness.record_schema import validate_records
from harness.results.export import provider, rounded, scored_result_schema, submission
from harness.web_search.benchmarks import BENCHMARKS
from harness.web_search.core import latency
from harness.web_search.report import load_run, scoring_rows
from lexbench_websearch.scoring import task_acceptance

# Search benchmark -> (submission benchmark, definition, quality metrics read from the
# scored call: submission metric -> (row metric, scale)). Scenario relevance needs the
# separate relevance review, so its quality metrics are not part of a scored run.
SUBMISSIONS = {
    "scenario": ("lexbench-websearch", "title-snippet-v2",
                 {"ndcg5": None, "precision5": None, "success5": None}),
    "factual": ("simpleqa-verified", "source-match-v2",
                {"mrr5": ("labeled_source_rr_5", 1), "hit5": ("labeled_source_hit_5", 100),
                 "answerMatch5": ("answer_in_preview_5", 100)}),
}
BENCHMARK_IDS = tuple(item[0] for item in SUBMISSIONS.values())
# The scored call as the exporter reads it; ``passed`` is a delivered, non-empty result list.
SCORED_RESULT = scored_result_schema("websearch.scored_result", 1, metrics=(dict,),
                                     results=(list,))


def _scored(row: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCORED_RESULT.id, "method": row["method"], "case": row["case_id"],
            "elapsed_ms": row["elapsed_ms"], "evidence": row["evidence"]["path"],
            "passed": bool(task_acceptance({}, row, "availability")),
            "metrics": row["metrics"], "results": row["results"]}


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def export(run_dir: Path | str, receipt: dict[str, Any] | None = None,
           benchmark: str | None = None) -> dict[str, Any]:
    profile, rows, _ = load_run(Path(run_dir))
    present = [name for name in SUBMISSIONS
               if any(BENCHMARKS.get(name).member(case) for case in profile["cases"])]
    if benchmark is None:
        if len(present) != 1:
            raise ValueError("The run holds several benchmarks; select one with --benchmark")
        name = present[0]
    else:
        name = next((key for key, item in SUBMISSIONS.items() if item[0] == benchmark), None)
        if name is None:
            raise ValueError(f"Unknown benchmark {benchmark}; choose {', '.join(BENCHMARK_IDS)}")
        if name not in present:
            raise ValueError(f"The run has no {benchmark} cases")
    site_id, definition, quality = SUBMISSIONS[name]
    case_ids = {case["id"] for case in profile["cases"] if BENCHMARKS.get(name).member(case)}
    records = validate_records(
        [_scored(row) for row in scoring_rows(profile, rows) if row["case_id"] in case_ids],
        SCORED_RESULT, source="results.jsonl")
    providers = []
    for method in profile["methods"]:
        own = [record for record in records if record["method"] == method["id"]]
        timing = latency([record["elapsed_ms"] for record in own])
        scores: dict[str, Any] = {}
        for metric, source in quality.items():
            values = [record["metrics"][source[0]] for record in own] if source else []
            value = _mean([float(item) for item in values if item is not None])
            scores[metric] = rounded(None if value is None or not source else value * source[1], 4)
        characters = sum(len(item.get("title") or "") + len(item.get("snippet") or "")
                         for record in own for item in record["results"])
        price = method.get("price_per_1000") if method.get("currency") == "USD" else None
        scores.update(meanChars=rounded(characters / len(own), 1),
                      meanTime=rounded(timing["mean"], 1), p50Time=rounded(timing["p50"], 1),
                      p95Time=rounded(timing["p95"], 1), pricePerThousand=price)
        providers.append(provider(
            method["id"], scores, mode=f"Search API ({method['adapter']} adapter)",
            description=str(method.get("label") or method["id"])))
    notes = []
    if not all(quality.values()):
        notes.append("Relevance metrics come from the separate relevance review and are not "
                     "exported from a scored run.")
    if any(method.get("currency") != "USD" for method in profile["methods"]):
        notes.append("Prices not quoted in USD are left undisclosed.")
    return submission(
        benchmark=site_id, definition=definition, task_count=len(case_ids),
        providers=providers, receipt=receipt, track="Web Search", notes=notes,
        scope=(f"Evaluates {len(case_ids)} {site_id} queries from the run profile, scored "
               f"on round {profile['protocol']['score_round']} with the top "
               f"{profile['protocol']['top_k']} results; timing covers every scored call."),
    )
