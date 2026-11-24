"""Export a scored Web Fetch run as a LexBench Web Fetch result submission."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness.record_schema import read_records
from harness.results.export import provider, rounded, submission
from harness.web_fetch.models import SCORED_RESULT, report_family
from lexbench_webfetch.metrics import summarize, task_passed

BENCHMARK, DEFINITION = "lexbench-webfetch", "split-media"
# Report family -> the definition's category.
CATEGORIES = {"web": "static", "dynamic": "dynamic", "challenge": "popular",
              "risk_control": "antibot", "document": "document",
              "web_media": "embeddedMedia", "media_file": "directMedia"}


def export(run_dir: Path | str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    run = Path(run_dir)
    records = read_records(run / "results.jsonl", SCORED_RESULT)
    dataset = json.loads((run / "dataset.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in dataset["cases"]}
    if not records:
        raise ValueError("The run has no scored results")
    calls = Counter((record["provider"], record["case_id"]) for record in records)
    if max(calls.values()) > 1:
        raise ValueError("Export needs one scored result per method and case; this run repeats cases")
    case_ids = {record["case_id"] for record in records}
    family = {case_id: CATEGORIES[report_family(cases[case_id])] for case_id in case_ids}
    totals = {category: sum(value == category for value in family.values())
              for category in CATEGORIES.values()}
    summary = summarize(records)["providers"]
    providers = []
    for name, item in summary.items():
        rows = [record for record in records if record["provider"] == name]
        passed = [record for record in rows if task_passed(record["ok"], record["metrics"])]
        rate = item["task_pass_rate"]
        providers.append(provider(
            name,
            {"passRate": None if rate is None else round(100 * rate, 2),
             "requestRate": round(100 * item["transport_success_rate"], 2),
             "totalMean": rounded(item["mean_all_attempts_ms"], 2),
             "totalP50": rounded(item["all_attempts_p50_ms"], 2),
             "totalP95": rounded(item["all_attempts_p95_ms"], 2)},
            mode=f"Web Fetch provider type {item['provider_type']}",
            description=f"{name} as configured in this run",
            passed=len(passed),
            categoryPassed={category: sum(family[record["case_id"]] == category
                                          for record in passed)
                            for category in CATEGORIES.values()},
        ))
    unmeasured = sum(item["task_unmeasured_attempts"] for item in summary.values())
    notes = [f"Task success excludes {unmeasured} results without a task measurement."] \
        if unmeasured else []
    return submission(
        benchmark=BENCHMARK, definition=DEFINITION, task_count=len(case_ids),
        providers=providers, receipt=receipt, track="Web Fetch", notes=notes,
        categoryTotals=totals,
        scope=(f"Evaluates {len(case_ids)} of the {len(cases)} cases in dataset {dataset['id']}. "
               "Latency covers every call; request success counts calls that returned a response."),
    )
