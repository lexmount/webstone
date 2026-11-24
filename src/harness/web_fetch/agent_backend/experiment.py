"""Select tasks, plan the run schedule and execute it (serial, resumable)."""
from __future__ import annotations

import hashlib
import json
import re
import os
import pathlib
import shutil
import platform
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from harness.data_home import data_home
from harness.web_fetch.agent_backend import arms as arm_module
from harness.web_fetch.config import load_dataset, read_dataset_bytes

DEFAULT_DATASET = pathlib.Path("evaluation/dataset.json")
# One prompt for every task and arm. The score is computed from the page the harness saved by code, so the subagent is
# asked for the fetch status only, never for page content.
PROMPT = ("Fetch {url} once. Do not summarize, quote or describe the page. Reply with one line: whether the fetch "
          "succeeded, and if it failed, the HTTP status or error.")


def default_root() -> pathlib.Path:
    return data_home() / "experiments" / "agent-fetch"


def definitions_path() -> pathlib.Path:
    """The public task definitions of the active 530-task edition (no reference texts)."""
    from lexbench_webfetch.inputs import definition_path
    return definition_path("evaluation/content-scopes-530/tasks.json")


def load_cases(dataset: pathlib.Path | None = None):
    """Load tasks; returns (metadata, {id: case dict}, sha256).

    Without ``dataset`` the public definitions of the pinned edition are read: they carry each task's URL,
    prompts and family, which is all that running needs. Scoring needs the approved executable dataset (it
    holds the reference texts); pass its ``dataset.json`` as ``dataset``.
    """
    path = pathlib.Path(dataset) if dataset else definitions_path()
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("tool") == "web_fetch":
        meta, cases = load_dataset(path)
        return {**meta, "kind": "executable"}, {case.id: asdict(case) for case in cases}, hashlib.sha256(read_dataset_bytes(path)).hexdigest()
    cases = {case["id"]: case for case in payload["cases"]}
    if len(cases) != len(payload["cases"]):
        raise ValueError("task ids must be unique")
    return {"id": payload.get("id"), "kind": "definitions"}, cases, hashlib.sha256(raw).hexdigest()


def load_materials(root: pathlib.Path | None = None):
    """Load the scorable 530-task records (with reference texts) shipped in the pinned dataset snapshot.

    Returns (metadata, {"<suite>--<id>": case dict}, sha256 of the five suite hashes). Each suite's
    ``dataset.json`` is checked against the hash in its ``source-inputs.json``.
    """
    from harness.benchmarks import data
    base = pathlib.Path(root) if root else data.snapshot("webfetch") / "definitions" / "materials" / "primary-content-20261103"
    cases, digest = {}, hashlib.sha256()
    for suite in ("webfetch", "dynamic-content", "popular-sites", "access-restrictions", "articles"):
        raw = (base / suite / "dataset.json").read_bytes()
        expected = json.loads((base / suite / "source-inputs.json").read_text(encoding="utf-8"))["dataset_sha256"]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"materials for {suite} do not match their recorded hash")
        digest.update(hashlib.sha256(raw).digest())
        for case in json.loads(raw)["cases"]:
            cases[f"{suite}--{case['id']}"] = case
    return {"id": "primary-content-20261103", "kind": "executable"}, cases, digest.hexdigest()


def select_cases(cases: dict, *, task_kinds: tuple | None = None, limit: int = 20, seed: int = 20261123,
                 include: list[str] | None = None, case_ids: list[str] | None = None,
                 exclude: list[str] | None = None, ineligible: dict | None = None, per_suite: int = 0) -> list[str]:
    """Deterministic stratified sample: round-robin over categories after a seeded shuffle.

    ``exclude`` removes tasks from the pool before sampling; the caller records the list in the plan.
    ``ineligible`` (task id -> reason) are tasks some backend cannot be configured for; they are removed for
    every arm, and naming one explicitly is an error.
    """
    if set(ineligible or {}) & set(case_ids or []):
        raise ValueError("explicitly selected tasks are not configurable for every arm: "
                         + ", ".join(sorted(set(ineligible) & set(case_ids))))
    if per_suite:  # the same sampling inside every suite (the id prefix before "--"), spread over its categories
        suites = sorted({cid.split("--")[0] for cid in cases if "--" in cid})
        picked = []
        for suite in suites:
            inner = {cid: case for cid, case in cases.items() if cid.startswith(suite + "--")}
            picked += select_cases(inner, task_kinds=task_kinds, limit=per_suite, seed=seed, include=[i for i in (include or []) if i in inner],
                                   case_ids=None, exclude=[i for i in (exclude or []) if i in inner],
                                   ineligible={i: r for i, r in (ineligible or {}).items() if i in inner})
        return sorted(picked)
    skipped = set(exclude or []) | set(ineligible or {})
    if (set(exclude or []) - set(cases) or skipped & set(include or []) or set(exclude or []) & set(case_ids or [])):
        raise ValueError("exclude must name known tasks that are not also included")
    if case_ids:
        unknown = [i for i in case_ids if i not in cases]
        if unknown:
            raise ValueError("unknown case ids: " + ", ".join(unknown))
        return sorted(case_ids)
    pool: dict[str, list[str]] = {}
    for cid in sorted(cases):
        if (task_kinds is None or cases[cid].get("task_kind", "body") in task_kinds) and cid not in skipped:
            pool.setdefault(cases[cid]["category"], []).append(cid)
    chosen = [i for i in (include or [])]
    for cid in chosen:
        pool[cases[cid]["category"]] = [x for x in pool.get(cases[cid]["category"], []) if x != cid]
    rng = random.Random(seed)
    for ids in pool.values():
        rng.shuffle(ids)
    categories = sorted(pool)
    while categories and (limit == 0 or len(chosen) < limit):
        for category in list(categories):
            if not pool[category]:
                categories.remove(category)
                continue
            chosen.append(pool[category].pop())
            if limit and len(chosen) >= limit:
                break
    return sorted(chosen)


def make_plan(case_ids: list[str], arm_names: list[str], reps: int, seed: int) -> list[dict]:
    """Round-robin over tasks for each repetition; the arm order rotates per (task, repetition)."""
    unknown = [a for a in arm_names if a not in arm_module.ARMS]
    if unknown:
        raise ValueError("unknown arms: " + ", ".join(unknown))
    plan = []
    for rep in range(1, reps + 1):
        for cid in case_ids:
            first = int(hashlib.sha256(f"{cid}:{rep}:{seed}".encode()).hexdigest(), 16) % len(arm_names)
            for arm in arm_names[first:] + arm_names[:first]:
                plan.append({"rep": rep, "case_id": cid, "arm": arm})
    return plan


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                              cwd=pathlib.Path(__file__).parent).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def dataset_commit() -> dict:
    """The dataset commit this run's task definitions come from: repository, full commit and archive hash."""
    from harness.benchmarks import data
    binding = data.binding("webfetch")
    return {"repository": binding["repository"], "revision": binding["revision"],
            "archive_sha256": binding["archive"]["sha256"]}


def _moli_identity() -> dict:
    """Version and SHA-256 of the Moli binary the run will use."""
    binary = shutil.which(os.environ.get("MOLI_BINARY", "moli"))
    if not binary:
        return {"error": "moli binary not found"}
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    return {"version": version, "sha256": hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest()}


def write_plan(root: pathlib.Path, *, args: dict, meta: dict, sha: str, case_ids: list[str], plan: list[dict],
               claude_bin: str, ineligible: dict | None = None) -> dict:
    moli = _moli_identity() if "moli" in args.get("arms", "") else None
    document = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "args": args,
        "versions": {"claude_code": arm_module.claude_version(claude_bin), "python": platform.python_version(),
                     "webstone_commit": _git_head(), "moli": moli,
                     "dataset_commit": dataset_commit() if meta.get("kind") == "definitions" else None},
        "dataset": {"id": meta.get("id"), "kind": meta.get("kind"), "sha256": sha}, "prompt": PROMPT,
        "fixed_environment": arm_module.FIXED_ENV,
        "ineligible_for_all_arms": ineligible or {}, "cases": case_ids, "runs": plan,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    return document


# Claude Code itself could not serve the request (account usage limit, rate limit, overload). That is not a result
# of any backend, so the run stops instead of recording it; ``resume`` repeats the whole task later.
CLAUDE_UNAVAILABLE = re.compile(r"usage limit|rate[ _-]?limit|overloaded|API Error: (?:429|5\d\d)|credit balance", re.I)


class ClaudeUnavailable(RuntimeError):
    pass


def _claude_unavailable(record: dict, directory: pathlib.Path) -> bool:
    if not (record.get("is_error") or record.get("exit_code")):
        return False
    stderr = (directory / "stderr.txt").read_text(errors="replace", encoding="utf-8") if (directory / "stderr.txt").exists() else ""
    return bool(CLAUDE_UNAVAILABLE.search((record.get("final_text") or "") + "\n" + stderr))


def _run_arm(root, item, case, *, claude_bin, model, timeout, params_for, max_attempts, progress, index, total):
    """One arm on one task: the attempt, and at most one declared retry. The last attempt is the only result."""
    base = f"{item['case_id']}__{item['arm']}__r{item['rep']}"
    prompt = PROMPT.format(url=case["url"])
    for attempt in range(1, max_attempts + 1):
        directory = root / "runs" / f"{base}__a{attempt}"
        record_path = directory / "record.json"
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding="utf-8"))
        else:
            params = params_for(arm_module.ARMS[item["arm"]]["backend"], case)
            record = arm_module.run_one(item["arm"], item["case_id"], case["url"], prompt, directory, claude_bin=claude_bin,
                                        model=model, timeout=timeout, params=params)
            if _claude_unavailable(record, directory):
                raise ClaudeUnavailable(f"{base}: {(record.get('final_text') or '').strip()[:200]}")
            record["rep"], record["attempt"] = item["rep"], attempt
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            progress(f"[{index}/{total}] {base} attempt {attempt} valid={record['valid']} fetch_failed={record['fetch_failed']} "
                     f"{','.join(record['invalid_reasons'])} wall={record['wall_ms']}ms")
        if record["valid"] and not record["fetch_failed"]:
            break


def execute(root: pathlib.Path, plan: dict, cases: dict, *, claude_bin: str, model: str, timeout: int,
            params_for=lambda backend, case: {}, retries: int = 0, concurrent_arms: bool = True,
            parallel_tasks: int = 1, per_suite_limit: int | None = None, progress=print) -> None:
    """Run the plan task by task, resumably.

    ``per_suite_limit`` runs only the first N planned tasks of every suite in this call, so a run can be checked in
    stages; a later call without it continues with the remaining tasks and never repeats a finished one.

    The arms of one task run at the same time, each in its own process, working directory and MCP server, so
    all arms see the same time window and network conditions. A run that breaks the protocol or whose fetch
    tool call failed gets ``retries`` declared retries (default 0); the last attempt is the only result and
    earlier attempts are kept on disk as evidence. A run that is still invalid counts as not passed.
    """
    groups: dict = {}
    for item in plan["runs"]:
        groups.setdefault((item["rep"], item["case_id"]), []).append(item)
    if per_suite_limit is not None:
        taken: dict = {}
        for key in list(groups):
            suite = key[1].split("--")[0]
            taken[suite] = taken.get(suite, 0) + 1
            if taken[suite] > per_suite_limit:
                del groups[key]
    lock = threading.Lock()

    def say(message):
        with lock:
            progress(message)

    def run_task(index, items):
        def work(item):
            _run_arm(root, item, cases[item["case_id"]], claude_bin=claude_bin, model=model, timeout=timeout,
                     params_for=params_for, max_attempts=retries + 1, progress=say, index=index, total=len(groups))
        try:
            if concurrent_arms and len(items) > 1:
                with ThreadPoolExecutor(max_workers=len(items)) as pool:
                    list(pool.map(work, items))
            else:
                for item in items:
                    work(item)
        except ClaudeUnavailable:
            # Drop every arm's attempts for this task so that resume runs its arms together again.
            for directory in (root / "runs").glob(f"{items[0]['case_id']}__*__r{items[0]['rep']}__a*"):
                shutil.rmtree(directory)
            raise

    # ``parallel_tasks`` tasks are in flight at once; every arm of a task still shares the same time window.
    with ThreadPoolExecutor(max_workers=max(1, parallel_tasks)) as tasks:
        list(tasks.map(lambda pair: run_task(*pair), enumerate(groups.values(), 1)))
