"""Command line for the agent fetch backend experiment: check, plan, run, score."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

from harness.web_fetch.agent_backend import arms as arm_module
from harness.web_fetch.agent_backend import backend_params, experiment, scoring

DEFAULT_ARMS = "builtin,lexmount,moli"


def _common(parser):
    parser.add_argument("--dataset", type=pathlib.Path, default=None,
                        help="executable dataset.json with full references (default: evaluation/dataset.json, "
                             "resolved to the verified external copy)")
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))


def _plan_args(parser):
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None, help="default: <data home>/experiments/agent-fetch")
    parser.add_argument("--task-kind", default="body")
    parser.add_argument("--limit", type=int, default=20, help="stratified sample size; 0 = every selected task")
    parser.add_argument("--include", default="", help="comma list of task ids always included")
    parser.add_argument("--case-ids", default="", help="explicit comma list; overrides sampling")
    parser.add_argument("--exclude", default="", help="comma list of task ids to skip (recorded in plan.json)")
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--arms", default=DEFAULT_ARMS)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--max-attempts", type=int, choices=(1, 2), default=2,
                        help="2 = one declared retry after a protocol violation or a failed fetch call; the last attempt is the only result")
    parser.add_argument("--serial-arms", action="store_true", help="run the arms of a task one after another instead of at the same time")


def _split(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def _prepare(args):
    import time
    meta, cases, sha = experiment.load_cases(args.dataset)
    moli = backend_params.moli_provider() if "moli" in _split(args.arms) else None
    backends = [arm_module.ARMS[a]["backend"] for a in _split(args.arms)]
    pool = {c: v for c, v in cases.items() if v.get("task_kind", "body") == args.task_kind}
    ineligible = backend_params.ineligible(pool, backends, moli=moli)
    ids = experiment.select_cases(cases, task_kind=args.task_kind, limit=args.limit, seed=args.seed,
                                  include=_split(args.include), case_ids=_split(args.case_ids),
                                  exclude=_split(args.exclude), ineligible=ineligible)
    plan = experiment.make_plan(ids, _split(args.arms), args.reps, args.seed)
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    root = (args.out or experiment.default_root()) / run_id
    document = experiment.write_plan(root, args={k: str(v) for k, v in vars(args).items() if k != "command"},
                                     meta=meta, sha=sha, case_ids=ids, plan=plan, claude_bin=args.claude_bin,
                                     ineligible=ineligible)
    return root, document, cases, moli


def cmd_plan(args) -> int:
    root, document, _, _ = _prepare(args)
    print(f"{len(document['cases'])} tasks, {len(document['runs'])} runs -> {root}; "
          f"{len(document['ineligible_for_all_arms'])} tasks not configurable for every arm")
    return 0


def cmd_run(args) -> int:
    root, document, cases, moli = _prepare(args)
    print(f"{len(document['cases'])} tasks, {len(document['runs'])} runs -> {root}; "
          f"{len(document['ineligible_for_all_arms'])} tasks not configurable for every arm")
    experiment.execute(root, document, cases, claude_bin=args.claude_bin, model=args.model, timeout=args.timeout,
                       params_for=lambda backend, case: backend_params.params_for(backend, case, moli=moli),
                       max_attempts=args.max_attempts, concurrent_arms=not args.serial_arms)
    return cmd_score(argparse.Namespace(run=root, dataset=args.dataset, price_per_1000=args.price_per_1000))


def cmd_resume(args) -> int:
    root = args.run
    document = json.loads((root / "plan.json").read_text())
    _, cases, sha = experiment.load_cases(args.dataset)
    if sha != document["dataset"]["sha256"]:
        raise SystemExit("dataset changed since the plan was written; start a new run")
    moli = backend_params.moli_provider() if "moli" in document["args"]["arms"] else None
    experiment.execute(root, document, cases, claude_bin=args.claude_bin, model=document["args"]["model"],
                       timeout=int(document["args"]["timeout"]),
                       params_for=lambda backend, case: backend_params.params_for(backend, case, moli=moli),
                       max_attempts=int(document["args"]["max_attempts"]),
                       concurrent_arms=document["args"]["serial_arms"] != "True")
    return cmd_score(argparse.Namespace(run=root, dataset=args.dataset, price_per_1000=args.price_per_1000))


def cmd_score(args) -> int:
    document = json.loads((args.run / "plan.json").read_text())
    _, cases, sha = experiment.load_cases(args.dataset)
    if sha != document["dataset"]["sha256"]:
        raise SystemExit("dataset differs from the one recorded in plan.json; rescoring would not be comparable")
    summary = scoring.score_run(args.run, cases, price_per_1000_usd=args.price_per_1000)
    print((args.run / "summary.md").read_text())
    return 0 if summary["controls"]["empty_fails"] == summary["controls"]["cases"] else 1


def cmd_check(args) -> int:
    rows: list[tuple[str, str, str]] = []

    def add(name, status, detail=""):
        rows.append((name, status, detail))

    version = arm_module.claude_version(args.claude_bin)
    add("claude CLI", "FAIL" if version.startswith("unavailable") else "PASS", version)
    try:
        meta, cases, sha = experiment.load_cases(args.dataset)
        body = [c for c in cases if cases[c].get("task_kind", "body") == "body"]
        control = scoring.controls(cases, body)
        add("dataset", "PASS", f"{meta.get('id')} sha256 {sha[:12]}, {len(cases)} tasks, {len(body)} body")
        add("scorer controls", "PASS" if control["empty_fails"] == control["cases"] else "FAIL",
            f"empty fails {control['empty_fails']}/{control['cases']}; reference passes "
            f"{control['reference_passes']}/{control['reference_available']}")
    except Exception as error:  # report any loader failure as a failed check, not a traceback
        add("dataset", "FAIL", f"{type(error).__name__}: {error}")
    probe = subprocess.run([sys.executable, "-I", str(arm_module.MCP_SERVER), "--backend", "moli", "--log", os.devnull],
                           input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
                                 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
                           capture_output=True, text=True, timeout=20)
    add("MCP server handshake", "PASS" if "web_fetch" in probe.stdout else "FAIL")
    import shutil
    moli = shutil.which(os.environ.get("MOLI_BINARY", "moli"))
    add("Moli binary", "PASS" if moli else "FAIL",
        (subprocess.run([moli, "--version"], capture_output=True, text=True, timeout=30).stdout.strip() if moli else "set MOLI_BINARY to the moli executable"))
    missing = [n for n in arm_module.LEXMOUNT_ENV if not os.environ.get(n)]
    add("Lexmount credentials", "FAIL" if missing else "PASS",
        ("missing " + ", ".join(missing)) if missing else "environment variables present")
    if args.offline:
        add("live probes", "SKIP", "--offline")
    else:
        done = subprocess.run([args.claude_bin, "-p", "Reply with exactly: OK", "--model", "claude-haiku-5-5",
                               "--setting-sources", "project", "--no-session-persistence"],
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        add("Claude Code login and model", "PASS" if "OK" in done.stdout else "FAIL", (done.stdout or done.stderr)[:80].strip())
        if not missing:
            from harness.web_fetch.agent_backend import fetch_mcp
            probe_case = {"id": "probe", "name": "probe", "url": "https://example.com/", "category": "static_html",
                          "expected_any": [], "track": "web"}
            provider = backend_params.moli_provider(moli) if moli else None
            for backend in ("lexmount", "moli") if moli else ("lexmount",):
                params = backend_params.params_for(backend, probe_case, moli=provider)
                text, is_error, row = fetch_mcp.fetch(backend, "https://example.com/", 60, params)
                add(f"{backend} fetch call", "FAIL" if is_error else "PASS", f"status {row['status']}, {row['wall_ms']} ms, {row['chars']} chars")
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"{status:<5} {name:<{width}}  {detail}")
    return 1 if any(r[1] == "FAIL" for r in rows) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agent_fetch_experiment", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (("check", cmd_check, "verify prerequisites; makes at most two tiny live calls"),
                                     ("plan", cmd_plan, "select tasks and write plan.json only"),
                                     ("run", cmd_run, "plan, execute serially, then score"),
                                     ("resume", cmd_resume, "continue an interrupted run"),
                                     ("score", cmd_score, "rescore saved runs; no provider or model calls")):
        p = sub.add_parser(name, help=help_text)
        _common(p)
        if name == "check":
            p.add_argument("--offline", action="store_true", help="skip live calls")
        if name in ("plan", "run"):
            _plan_args(p)
        if name in ("resume", "score"):
            p.add_argument("--run", type=pathlib.Path, required=True)
        if name in ("run", "resume", "score"):
            p.add_argument("--price-per-1000", type=float, default=0.75, help="USD per 1000 Lexmount API calls")
        p.set_defaults(handler=handler)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
