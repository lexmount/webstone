"""Score saved runs with the benchmark scorer and summarize each arm. Makes no provider or model calls."""
from __future__ import annotations

import json
import math
import pathlib
import re
import statistics
from collections import Counter

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt

from harness.web_fetch.agent_backend.arms import ARMS, fetch_dispatch
from harness.web_fetch.native_projection import native_answer_projection

WRAPPER = re.compile(r"<fetched-web-content>\n?(.*)</fetched-web-content>", re.S)
PAIRS = (("builtin", "lexmount"), ("builtin", "moli"))
ATTEMPT = re.compile(r"^(?P<base>.+)__a(?P<n>\d+)$")


def declared_format(arm: str, case: dict) -> str | None:
    """The representation the benchmark's own provider declaration gives this arm's output, if any.

    The benchmark declares Lexmount's ``main_text`` as plain text. Its moli declaration (bytes in, UTF-8 out) and the
    built-in tool carry no representation, so the scorer's default applies. The value is read from the benchmark's
    declaration, not chosen here.
    """
    if ARMS[arm].get("provider_type") != "lexmount":
        return None
    return native_answer_projection("lexmount", case, {"result": {"main_text": "x"}}, 200).get("answer_format")


def verdict(case: dict, text: str, ok: bool, unscorable: set, answer_format: str | None = None) -> bool:
    if not ok or not text:
        return False
    try:
        metadata = {"answer_format": answer_format} if answer_format else {}
        metrics = score_attempt(case, text.encode("utf-8"), ok=True, metadata=metadata)
        return bool(task_passed(True, metrics, case.get("task_kind", "body")))
    except ValueError as error:  # the scorer found no scorable body in this text
        unscorable.add((case["id"], str(error)))
        return False


def controls(cases: dict, case_ids: list[str]) -> dict:
    """Empty text must fail every selected task.

    A task's own reference text is not a valid response: roughly half of the tasks have none, and the acceptance
    rules (stable anchors, comment counts, page structure) are defined on the whole page, which the reference does
    not contain. It is therefore not used as a control.
    """
    unscorable: set = set()
    empty_fails = sum(not verdict(cases[cid], "", True, unscorable) for cid in case_ids)
    return {"cases": len(case_ids), "empty_fails": empty_fails}


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
    record = json.loads((directory / "record.json").read_text())
    debug = directory / "debug.log"
    if debug.exists():  # derive from saved evidence so every record uses the same measurement point
        record["fetch_tool_outcome"], record["fetch_tool_ms"] = fetch_dispatch(debug.read_text(errors="replace"))
    record["attempt"] = attempt
    return record


def score_run(root: pathlib.Path, cases: dict, *, price_per_1000_usd: float = 0.75) -> dict:
    plan = json.loads((root / "plan.json").read_text())
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
        markdown = (directory / "markdown.txt").read_text(encoding="utf-8")  # the backend's own content; see arms.py
        case = cases[record["case_id"]]
        record["retried"] = bool(previous)
        record["retry_reasons"] = [x for p in previous for x in (p["invalid_reasons"] or ["fetch_failed"])]
        # The benchmark defines one verdict per task: does the content the fetch tool returned pass the acceptance
        # rules. The agent's final answer is rewritten text and has no annotation, so it is stored but never scored.
        record["passed"] = record["valid"] and verdict(case, markdown, bool(markdown), unscorable,
                                                           declared_format(record["arm"], case))
        rows.append(record)
        earlier.extend(previous)
    # A task/repetition enters the comparison only if every arm's final run is valid and the task is satisfiable,
    # so no arm is helped or hurt by another arm's protocol failure.
    by_key: dict = {}
    for r in rows:
        by_key.setdefault((r["case_id"], r["rep"]), {})[r["arm"]] = r
    arm_names = sorted({r["arm"] for r in rows})
    included = {k for k, v in by_key.items()
                if set(v) == set(arm_names) and all(x["valid"] for x in v.values())}
    excluded_pairs = [{"case_id": k[0], "rep": k[1], "reasons": {a: x["invalid_reasons"] for a, x in v.items() if x["invalid_reasons"]}}
                      for k, v in sorted(by_key.items()) if k not in included]
    unit = price_per_1000_usd / 1000
    tokens = lambda r: sum((r.get(k) or 0) for k in
                           ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"))
    summary = {"plan": {"args": plan["args"], "versions": plan["versions"], "dataset": plan["dataset"],
                        "ineligible_for_all_arms": plan.get("ineligible_for_all_arms", {})},
               "controls": control, "unscorable": sorted(unscorable), "included_pairs": len(included),
               "excluded_pairs": excluded_pairs, "arms": {}}
    for arm in arm_names:
        mine = [r for r in rows if r["arm"] == arm]
        used = [r for r in mine if (r["case_id"], r["rep"]) in included]
        before = [r for r in earlier if r["arm"] == arm]
        priced = lambda r: r["backend_calls"] * unit if arm == "lexmount" else 0.0  # moli and builtin have no call price
        summary["arms"][arm] = {
            "runs": len(mine), "invalid_runs": sum(not r["valid"] for r in mine),
            "invalid_reasons": dict(Counter(x for r in mine for x in r["invalid_reasons"])),
            "retried_runs": sum(r["retried"] for r in mine), "retry_reasons": dict(Counter(x for r in mine for x in r["retry_reasons"])),
            "failed_fetch_after_retry": sum(r["fetch_failed"] for r in mine),
            "compared_runs": len(used), "routes": dict(Counter(r["route"] for r in mine)),
            "passed": sum(r["passed"] for r in used),
            "fetch_failed_runs": sum(bool(r["fetch_failed"]) for r in mine),
            "summarized_tail_runs": sum(bool(r.get("summarized_tail")) for r in mine),
            "replaced_by_claude_code_runs": sum(bool(r.get("replaced_by_claude_code")) for r in mine),
            "markdown_chars": _stats([r.get("markdown_chars") for r in used]),
            # Metrics describe final attempts only. Earlier attempts are listed under ``retry_cost``, never averaged in.
            "subagent_tokens": _stats([r["subagent_tokens"] for r in used]),
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
    summary["by_suite"] = {}
    for arm in arm_names:  # compared tasks and passes per task category, the first part of the task id
        table: dict = {}
        for r in rows:
            if r["arm"] == arm and (r["case_id"], r["rep"]) in included:
                cell = table.setdefault(r["case_id"].split("--")[0], {"compared": 0, "passed": 0, "fetch_failed": 0})
                cell["compared"] += 1
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
    (root / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    (root / "scored_runs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (root / "summary.md").write_text(render_markdown(summary))
    return summary


def render_markdown(summary: dict) -> str:
    c = summary["controls"]
    lines = ["# Agent fetch backend experiment", "",
             f"Dataset `{summary['plan']['dataset']['id']}` sha256 `{summary['plan']['dataset']['sha256'][:12]}`; "
             f"Claude Code `{summary['plan']['versions']['claude_code']}`.", "",
             f"Control: empty text fails {c['empty_fails']}/{c['cases']}.", "",
             f"Compared {summary['included_pairs']} task runs per arm; excluded because any arm's run broke the protocol: "
             + (", ".join(f"{p['case_id']} r{p['rep']} {p['reasons']}" for p in summary["excluded_pairs"]) or "none") + ".", "",
             "| Arm | Runs | Invalid | Compared | Pass | Subagent tokens mean | Subagent s P50 | Fetch tool s P50 | Fetch API USD | Subagent USD (upper bound) |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm, s in summary["arms"].items():
        lines.append(f"| {arm} | {s['runs']} | {s['invalid_runs']} | {s['compared_runs']} | {s['passed']}/{s['compared_runs']} | "
                     f"{s['subagent_tokens']['mean'] or 0:.0f} | "
                     f"{(s['subagent_duration_ms']['p50'] or 0) / 1000:.1f} | {(s['fetch_tool_ms']['p50'] or 0) / 1000:.1f} | "
                     f"{s['fetch_api_cost_usd']:.4f} | {s['subagent_cost_usd_upper_bound']:.4f} |")
    return "\n".join(lines) + "\n"
