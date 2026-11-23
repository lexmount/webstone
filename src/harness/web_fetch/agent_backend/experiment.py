"""Select tasks, plan the run schedule and execute it (serial, resumable)."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import platform
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from harness.data_home import data_home
from harness.web_fetch.agent_backend import arms as arm_module
from harness.web_fetch.config import load_dataset, read_dataset_bytes

DEFAULT_DATASET = pathlib.Path("evaluation/dataset.json")
PROMPT = ("{task_prompt}\n\nURL: {url}\n\nReturn the content as Markdown. Do not summarize. "
          "Make a single fetch attempt; if it fails or the page lacks the content, say so instead of trying other ways.")


def default_root() -> pathlib.Path:
    return data_home() / "experiments" / "agent-fetch"


def load_cases(dataset: pathlib.Path | None = None):
    """Load the verified executable dataset as published; returns (metadata, {id: case dict}, sha256)."""
    path = pathlib.Path(dataset) if dataset else DEFAULT_DATASET
    meta, cases = load_dataset(path)
    sha = hashlib.sha256(read_dataset_bytes(path)).hexdigest()
    return meta, {case.id: asdict(case) for case in cases}, sha


def select_cases(cases: dict, *, task_kind: str = "body", limit: int = 20, seed: int = 20261123,
                 include: list[str] | None = None, case_ids: list[str] | None = None,
                 exclude: list[str] | None = None, ineligible: dict | None = None) -> list[str]:
    """Deterministic stratified sample: round-robin over categories after a seeded shuffle.

    ``exclude`` removes tasks from the pool before sampling; the caller records the list in the plan.
    ``ineligible`` (task id -> reason) are tasks some backend cannot be configured for; they are removed for
    every arm, and naming one explicitly is an error.
    """
    if set(ineligible or {}) & set(case_ids or []):
        raise ValueError("explicitly selected tasks are not configurable for every arm: "
                         + ", ".join(sorted(set(ineligible) & set(case_ids))))
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
        if cases[cid].get("task_kind", "body") == task_kind and cid not in skipped:
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
                     "webstone_commit": _git_head(), "moli": moli},
        "dataset": {"id": meta.get("id"), "sha256": sha}, "fixed_environment": arm_module.FIXED_ENV,
        "ineligible_for_all_arms": ineligible or {}, "cases": case_ids, "runs": plan,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(document, indent=1))
    return document


def _run_arm(root, item, case, *, claude_bin, model, timeout, params_for, max_attempts, progress, index, total):
    """One arm on one task: the attempt, and at most one declared retry. The last attempt is the only result."""
    base = f"{item['case_id']}__{item['arm']}__r{item['rep']}"
    prompt = PROMPT.format(task_prompt=case.get("task_prompt", ""), url=case["url"])
    for attempt in range(1, max_attempts + 1):
        directory = root / "runs" / f"{base}__a{attempt}"
        record_path = directory / "record.json"
        if record_path.exists():
            record = json.loads(record_path.read_text())
        else:
            params = params_for(arm_module.ARMS[item["arm"]]["backend"], case)
            record = arm_module.run_one(item["arm"], item["case_id"], case["url"], prompt, directory, claude_bin=claude_bin,
                                        model=model, timeout=timeout, params=params)
            record["rep"], record["attempt"] = item["rep"], attempt
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            progress(f"[{index}/{total}] {base} attempt {attempt} valid={record['valid']} fetch_failed={record['fetch_failed']} "
                     f"{','.join(record['invalid_reasons'])} wall={record['wall_ms']}ms")
        if record["valid"] and not record["fetch_failed"]:
            break


def execute(root: pathlib.Path, plan: dict, cases: dict, *, claude_bin: str, model: str, timeout: int,
            params_for=lambda backend, case: {}, max_attempts: int = 2, concurrent_arms: bool = True,
            progress=print) -> None:
    """Run the plan task by task, resumably.

    The arms of one task run at the same time, each in its own process, working directory and MCP server, so
    all arms see the same time window and network conditions. A run that breaks the protocol or whose fetch
    tool call failed gets one declared retry (``max_attempts`` = 2); the last attempt is the only result and
    earlier attempts are kept as evidence and accounted separately. A run that is still invalid is excluded
    from the comparison of every arm at scoring time.
    """
    groups: dict = {}
    for item in plan["runs"]:
        groups.setdefault((item["rep"], item["case_id"]), []).append(item)
    lock = threading.Lock()

    def say(message):
        with lock:
            progress(message)

    for index, items in enumerate(groups.values(), 1):
        def work(item, index=index):
            _run_arm(root, item, cases[item["case_id"]], claude_bin=claude_bin, model=model, timeout=timeout,
                     params_for=params_for, max_attempts=max_attempts, progress=say, index=index, total=len(groups))
        if concurrent_arms and len(items) > 1:
            with ThreadPoolExecutor(max_workers=len(items)) as pool:
                list(pool.map(work, items))
        else:
            for item in items:
                work(item)
