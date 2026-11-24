"""Score saved runs with the benchmark scorer and summarize each arm. Makes no provider or model calls."""
from __future__ import annotations

import json
import math
import pathlib
import re
import statistics
from collections import Counter

from lexbench_webfetch.evaluation import evaluate_response

from harness.web_fetch.agent_backend.arms import fetch_dispatch
from harness.web_fetch.native_projection import native_answer_projection, project_native_answer

WRAPPER = re.compile(r"<fetched-web-content>\n?(.*)</fetched-web-content>", re.S)
PAIRS = (("builtin", "lexmount"), ("builtin", "moli"))
ATTEMPT = re.compile(r"^(?P<base>.+)__a(?P<n>\d+)$")


def scoring_input(directory: pathlib.Path, arm: str, case: dict) -> dict | None:
    """What the benchmark scores for one run, read from the run's saved evidence; None when nothing was delivered.

    * lexmount: the saved API response projected exactly as the benchmark projects a Lexmount call (``main_text``
      declared plain text, plus the returned title, final URL, links and images).
    * moli: its standard output; original file bytes when the output is binary.
    * builtin: the verbatim markdown of the tool result (``markdown.txt``) and the original bytes the tool saved for
      binary content (``saved_files/``).
    """
    log = directory / "backend_calls.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()] if log.exists() else []
    good = [r for r in rows if r.get("ok")]
    outputs = directory / "backend_outputs"
    if arm == "lexmount":
        if not good or not good[-1].get("payload"):
            return None
        payload = json.loads((outputs / good[-1]["payload"]).read_text(encoding="utf-8"))
        content, fields = project_native_answer(payload, native_answer_projection("lexmount", case, payload, good[-1]["status"]))
        result = payload.get("result") or {}
        return {"response": content.encode("utf-8"), "files": [], "metadata": fields,
                "title": result.get("title"), "final_url": result.get("final_url") or result.get("url")}
    if arm == "moli" and good and good[-1].get("binary"):
        return {"response": b"", "files": [(outputs / good[-1]["binary"]).read_bytes()], "metadata": {}}
    text = (directory / "markdown.txt").read_text(encoding="utf-8") if (directory / "markdown.txt").exists() else ""
    # Original bytes the built-in tool saved for binary content are delivered files, like moli's binary output.
    files = [p.read_bytes() for p in sorted((directory / "saved_files").glob("*"))] if arm == "builtin" else []
    return {"response": text.encode("utf-8"), "files": files, "metadata": {}} if text or files else None


def verdict(case: dict, delivered: dict | None, unscorable: set) -> bool:
    """The benchmark's own verdict through its public scoring boundary. Nothing delivered is a failure."""
    if not delivered or not (delivered["response"] or delivered["files"]):
        return False
    try:
        return bool(evaluate_response(case, delivered["response"], files=delivered["files"], ok=True,
                                      metadata=delivered["metadata"], title=delivered.get("title"),
                                      final_url=delivered.get("final_url"))["passed"])
    except ValueError as error:  # the scorer found nothing it can score in this output
        unscorable.add((case["id"], str(error)))
        return False


def controls(cases: dict, case_ids: list[str]) -> dict:
    """Empty output, sent through the scorer itself, must fail every selected task.

    A task's own reference text is not a valid response: many tasks have none, and the acceptance rules (stable
    anchors, comment counts, page structure, resources, files) are defined on the whole page or file, which the
    reference does not contain. It is therefore not used as a control.
    """
    failed = 0
    for cid in case_ids:
        try:
            failed += not evaluate_response(cases[cid], b"", ok=True)["passed"]
        except ValueError:
            failed += 1
    return {"cases": len(case_ids), "empty_fails": failed}


def _percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _stats(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "mean": statistics.fmean(values) if values else None,
            "p50": _percentile(values, .5), "p95": _percentile(values, .95)}


def _load(directory: pathlib.Path, attempt: int) -> dict:
    record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
    debug = directory / "debug.log"
    if debug.exists():  # derive from saved evidence so every record uses the same measurement point
        record["fetch_tool_outcome"], record["fetch_tool_ms"] = fetch_dispatch(debug.read_text(errors="replace", encoding="utf-8"))
    record["attempt"] = attempt
    return record


def score_run(root: pathlib.Path, cases: dict, *, price_per_1000_usd: float = 0.75) -> dict:
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    unscorable: set = set()
    control = controls(cases, plan["cases"])
    groups: dict[str, list] = {}
    for directory in sorted((root / "runs").glob("*")):
        match = ATTEMPT.match(directory.name)
        if match and (directory / "record.json").exists():
            groups.setdefault(match["base"], []).append((int(match["n"]), directory))
    rows, earlier = [], []
    for items in groups.values():
        items.sort()
        loaded = [_load(directory, n) for n, directory in items]
        record, previous = loaded[-1], loaded[:-1]  # the last attempt is the only result; earlier ones are evidence
        directory = items[-1][1]
        case = cases[record["case_id"]]
        record["retried"] = bool(previous)
        record["retry_reasons"] = [x for p in previous for x in (p["invalid_reasons"] or ["fetch_failed"])]
        # The benchmark defines one verdict per task: does what the fetch tool delivered pass the acceptance rules.
        # The agent's final answer is rewritten text and has no annotation, so it is stored but never scored.
        # An invalid final run has no trustworthy result and counts as not passed; its task stays in every arm's count.
        record["passed"] = record["valid"] and verdict(case, scoring_input(directory, record["arm"], case), unscorable)
        rows.append(record)
        earlier.extend(previous)
    # Every task counts for every arm. Usage and timing statistics use the valid runs only, because an invalid run has
    # no usage report; their count is reported with each statistic.
    by_key: dict = {}
    for r in rows:
        by_key.setdefault((r["case_id"], r["rep"]), {})[r["arm"]] = r
    arm_names = sorted({r["arm"] for r in rows})
    included = {k for k, v in by_key.items() if set(v) == set(arm_names)}
    invalid_runs = [{"case_id": k[0], "rep": k[1], "reasons": {a: x["invalid_reasons"] for a, x in v.items() if x["invalid_reasons"]}}
                    for k, v in sorted(by_key.items()) if any(x["invalid_reasons"] for x in v.values())]
    unit = price_per_1000_usd / 1000
    tokens = lambda r: sum((r.get(k) or 0) for k in
                           ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"))
    summary = {"plan": {"args": plan["args"], "versions": plan["versions"], "dataset": plan["dataset"],
                        "ineligible_for_all_arms": plan.get("ineligible_for_all_arms", {})},
               "controls": control, "unscorable": sorted(unscorable), "tasks": len(included),
               "invalid_runs": invalid_runs, "arms": {}}
    for arm in arm_names:
        mine = [r for r in rows if r["arm"] == arm]
        counted = [r for r in mine if (r["case_id"], r["rep"]) in included]
        used = [r for r in counted if r["valid"]]  # statistics need a usage report
        before = [r for r in earlier if r["arm"] == arm]
        priced = lambda r, arm=arm: r["backend_calls"] * unit if arm == "lexmount" else 0.0  # moli and builtin have no call price
        summary["arms"][arm] = {
            "runs": len(mine), "invalid_runs": sum(not r["valid"] for r in mine),
            "invalid_reasons": dict(Counter(x for r in mine for x in r["invalid_reasons"])),
            "retried_runs": sum(r["retried"] for r in mine), "retry_reasons": dict(Counter(x for r in mine for x in r["retry_reasons"])),
            "failed_fetch_after_retry": sum(r["fetch_failed"] for r in mine),
            "tasks": len(counted), "valid_runs": len(used), "routes": dict(Counter(r["route"] for r in mine)),
            "passed": sum(r["passed"] for r in counted),
            "fetch_failed_runs": sum(bool(r["fetch_failed"]) for r in mine),
            "summarized_tail_runs": sum(bool(r.get("summarized_tail")) for r in mine),
            "replaced_by_claude_code_runs": sum(bool(r.get("replaced_by_claude_code")) for r in mine),
            "markdown_chars": _stats([r.get("markdown_chars") for r in used]),
            # Metrics describe final attempts only. Earlier attempts are listed under ``retry_cost``, never averaged in.
            "subagent_tokens": _stats([r["subagent_tokens"] for r in used]),
            # The fetched page is the subagent's tool result, so its tokens grow with the delivered text. A failed
            # fetch costs only the fixed overhead; this view leaves those runs out.
            "subagent_tokens_fetched": _stats([r["subagent_tokens"] for r in used if not r["fetch_failed"]]),
            "subagent_duration_ms": _stats([r["subagent_duration_ms"] for r in used]),
            "fetch_tool_ms": _stats([r["fetch_tool_ms"] for r in used]),
            "tool_uses": dict(Counter(r["subagent_tool_uses"] for r in used)),
            "haiku_model_cost_usd": {"sum": sum(r.get("haiku_model_cost_usd") or 0 for r in mine)},
            "main_session_context": {"tokens": _stats([tokens(r) for r in used]),
                                     "llm_cost_usd": {"sum": sum(r["llm_cost_usd"] or 0 for r in mine)}},
            "fetch_calls": sum(r["backend_calls"] for r in mine), "fetch_api_cost_usd": sum(priced(r) for r in mine),
            "subagent_cost_usd_upper_bound": sum(r.get("haiku_model_cost_usd") or 0 for r in mine) + sum(priced(r) for r in mine),
            "retry_cost": {"attempts": len(before), "fetch_calls": sum(r["backend_calls"] for r in before),
                           "fetch_api_cost_usd": sum(priced(r) for r in before),
                           "subagent_tokens": sum(r["subagent_tokens"] or 0 for r in before),
                           "haiku_model_cost_usd": sum(r.get("haiku_model_cost_usd") or 0 for r in before)},
        }
    # Diagnostic like-for-like view: tasks that every arm fetched, so each arm read the same pages. It is chosen
    # after the outcome and never ranks the arms; the all-run and per-arm fetched figures stay the primary ones.
    fetched_by_all = [v for k, v in sorted(by_key.items()) if k in included
                      and all(x["valid"] and not x["fetch_failed"] for x in v.values())]
    summary["fetched_by_every_arm"] = {"tasks": len(fetched_by_all), "arms": {arm: {
        "subagent_tokens": _stats([v[arm]["subagent_tokens"] for v in fetched_by_all]),
        "markdown_chars": _stats([v[arm].get("markdown_chars") for v in fetched_by_all]),
        "subagent_duration_ms": _stats([v[arm]["subagent_duration_ms"] for v in fetched_by_all]),
        "fetch_tool_ms": _stats([v[arm]["fetch_tool_ms"] for v in fetched_by_all])} for arm in arm_names}}
    summary["by_suite"] = {}
    for arm in arm_names:  # tasks and passes per task category, the first part of the task id
        table: dict = {}
        for r in rows:
            if r["arm"] == arm and (r["case_id"], r["rep"]) in included:
                cell = table.setdefault(r["case_id"].split("--")[0], {"tasks": 0, "passed": 0, "fetch_failed": 0, "invalid": 0})
                cell["tasks"] += 1
                cell["invalid"] += not r["valid"]
                cell["passed"] += bool(r["passed"])
                cell["fetch_failed"] += bool(r["fetch_failed"])
        summary["by_suite"][arm] = table
    summary["paired"] = {}
    for x, y in PAIRS:
        both = [by_key[k] for k in sorted(included) if x in by_key[k] and y in by_key[k]]
        if both:
            summary["paired"][f"{x}|{y}"] = {
                "pairs": len(both), f"only_{x}": sum(v[x]["passed"] and not v[y]["passed"] for v in both),
                f"only_{y}": sum(v[y]["passed"] and not v[x]["passed"] for v in both),
                "both_pass": sum(v[x]["passed"] and v[y]["passed"] for v in both),
                "both_fail": sum(not v[x]["passed"] and not v[y]["passed"] for v in both)}
    (root / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    (root / "scored_runs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def render_markdown(summary: dict) -> str:
    c = summary["controls"]
    lines = ["# Agent fetch backend experiment", "",
             f"Dataset `{summary['plan']['dataset']['id']}` sha256 `{summary['plan']['dataset']['sha256'][:12]}`; "
             f"Claude Code `{summary['plan']['versions']['claude_code']}`.", "",
             f"Control: empty text fails {c['empty_fails']}/{c['cases']}.", "",
             f"{summary['tasks']} tasks per arm; invalid final runs (counted as not passed): "
             + (", ".join(f"{p['case_id']} r{p['rep']} {p['reasons']}" for p in summary["invalid_runs"]) or "none") + ".", "",
             "| Arm | Runs | Invalid | Tasks | Pass | Subagent tokens mean | Subagent s P50 | Fetch tool s P50 | Fetch API USD | Subagent USD (upper bound) |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm, s in summary["arms"].items():
        lines.append(f"| {arm} | {s['runs']} | {s['invalid_runs']} | {s['tasks']} | {s['passed']}/{s['tasks']} | "
                     f"{s['subagent_tokens']['mean'] or 0:.0f} | "
                     f"{(s['subagent_duration_ms']['p50'] or 0) / 1000:.1f} | {(s['fetch_tool_ms']['p50'] or 0) / 1000:.1f} | "
                     f"{s['fetch_api_cost_usd']:.4f} | {s['subagent_cost_usd_upper_bound']:.4f} |")
    return "\n".join(lines) + "\n"
