"""Export a scored Web Extract run as a result submission for its benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.record_schema import validate_records
from harness.results.export import (
    distribution,
    percent,
    provider,
    rounded,
    scored_result_schema,
    submission,
)
from harness.web_extract.analysis import field_results
from harness.web_extract.dataset import benchmark_config
from harness.web_extract.pipeline import digest, read_json, score_rows, validate_dataset
from harness.web_extract.report import completion_bundle

# Dataset benchmark -> (submission benchmark, its quality metrics: metric -> score path).
SUBMISSIONS = {
    "swde": ("lexbench-swde", {"pageMacroF1": ("page_hit_macro", "f1"),
                               "valueMicroF1": ("value_micro", "f1"),
                               "fieldSetExact": ("exact_field_sets", "rate")}),
    "zyte": ("lexbench-zyte-product-extraction", {"priceF1": ("price", "f1"),
                                                  "skuF1": ("sku", "f1"),
                                                  "stockF1": ("availability", "f1"),
                                                  "outOfStockF1": ("OutOfStock", "f1")}),
}
PERCENT_METRICS = {"fieldSetExact"}
# The call as the exporter reads it; ``passed`` is a delivered structured output and
# an untimed call keeps a null elapsed time.
SCORED_RESULT = scored_result_schema("webextract.scored_result", 1, nullable=("elapsed",),
                                     row=(dict,))


def _calls(run: Path) -> list[dict[str, Any]]:
    """Call rows named by the run manifest, or by the call journal of a checkpointed run.

    The journal holds every physical attempt; the latest one per method and case is the
    effective call, as the manifest of a finalized run records it.
    """
    manifest = run / "manifest.json"
    if manifest.is_file():
        entries = read_json(manifest)["calls"]
    else:
        journal = run / "calls.journal.jsonl"
        entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    rows = {}
    for entry in entries:
        relative = Path(entry["path"])
        path = run / relative
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
            raise ValueError("Unsafe call evidence path")
        if digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError(f"Call evidence changed: {relative}")
        row = read_json(path)
        rows[(row["method"], row["case_id"])] = {**row, "_evidence": relative.as_posix()}
    return list(rows.values())


def _scored(row: dict[str, Any]) -> dict[str, Any]:
    elapsed = row.get("elapsed_seconds")
    return {"schema": SCORED_RESULT.id, "method": row["method"], "case": row["case_id"],
            "elapsed_ms": None if elapsed is None else 1000 * elapsed,
            "evidence": row["_evidence"],
            "passed": row.get("http_status") == 200
            and row.get("compatibility") == "structured_output",
            "row": row}


def export(run_dir: Path | str, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    run = Path(run_dir).resolve()
    home = run.parents[1]
    contract = read_json(run / "contract.json")
    if contract.get("kind") == "input_compatibility_only":
        raise ValueError("An input compatibility run has no scored population to export")
    # Score against the dataset the calls were made on, as score_run and the report do.
    local = home / "profile.json"
    validate_dataset(home, local if local.is_file() and read_json(local).get("local_example")
                     else None)
    if contract["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("The prepared dataset differs from the one the run contract binds")
    name = benchmark_config(home)["benchmark"].name
    if name not in SUBMISSIONS:
        raise ValueError(f"Benchmark {name} has no submission definition")
    site_id, quality = SUBMISSIONS[name]
    records = name != "zyte"
    all_truth = read_json(home / "dataset/dataset/ground-truth.json")
    truth = {case_id: all_truth[case_id] for case_id in contract["case_ids"]}
    scored = validate_records([_scored(row) for row in _calls(run)], SCORED_RESULT,
                              source=run.name)
    rows = [record["row"] for record in scored]
    complete = [method for method in contract["methods"]
                if {row["case_id"] for row in rows if row["method"] == method} == set(truth)]
    scores = score_rows(home, truth, [row for row in rows if row["method"] in complete], complete)
    judged = [{**row, "field_results": field_results(home, truth[row["case_id"]], row)}
              for row in rows if row["method"] in complete]
    completion = completion_bundle(complete, judged, records=records,
                                   complete_methods=complete)["methods"]
    providers = []
    for method in contract["methods"]:
        own = [record for record in scored if record["method"] == method]
        timing = distribution(record["elapsed_ms"] / 1000 for record in own
                              if record["elapsed_ms"] is not None)
        values: dict[str, Any] = {}
        for metric, (group, key) in quality.items():
            value = scores[method][group][key] if method in complete else None
            values[metric] = rounded(100 * value if metric in PERCENT_METRICS and value is not None
                                     else value, 4)
        extra = completion.get(method) or {}
        delivered = sum(record["passed"] for record in own)
        values.update(
            executedPages=len(own),
            **({"pagesWithFields": delivered} if records else {"returnedObjects": delivered}),
            pageExact=percent((extra.get("page_exact") or {}).get("correct"),
                              (extra.get("page_exact") or {}).get("total")),
            nullAccuracy=percent((extra.get("empty_fields") or {}).get("correct"),
                                 (extra.get("empty_fields") or {}).get("total")),
            timedPages=timing["n"], untimedPages=len(own) - timing["n"],
            meanTime=rounded(timing["mean"]), p50Time=rounded(timing["p50"]),
            p95Time=rounded(timing["p95"]))
        providers.append(provider(method, values, mode="Structured extraction API",
                                  description=f"{method} as configured in this run"))
    notes = [f"{method} executed part of the population; its quality metrics are undisclosed."
             for method in contract["methods"] if method not in complete]
    return submission(
        benchmark=site_id, definition="standard", task_count=len(truth), providers=providers,
        receipt=receipt, track="Web Extract", notes=notes,
        scope=(f"Evaluates {len(truth)} archived {name} pages declared by the run contract; "
               "quality metrics cover methods that executed every page."),
    )
