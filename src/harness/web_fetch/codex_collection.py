"""Collect Codex responses independently of reference loading and scoring."""

import base64
import hashlib
import json
import subprocess
from types import SimpleNamespace

from harness.benchmark_execution import ExecutionPolicy, Job, exclusive_policy, run_jobs
from harness.web_fetch.providers.base import build_provider


def collect(args):
    raw = args.dataset.read_bytes()
    dataset = json.loads(raw)
    tasks = [
        SimpleNamespace(id=c["id"], url=c["url"], task_prompt=c["task_prompt"])
        for c in dataset["cases"]
    ]
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("Expected nonempty cases with unique IDs")
    provider = build_provider(
        "codex", {"type": "codex_cli", "mode": args.mode, "executable": args.executable}
    )
    if provider.validate():
        raise ValueError("Codex CLI executable not found")
    version = subprocess.check_output([provider.cli, "--version"], text=True).strip()
    policy = exclusive_policy("conversation", "One sequential Codex session per collection")
    args.output.mkdir(parents=True, exist_ok=False)
    contract = dict(
        dataset_sha256=hashlib.sha256(raw).hexdigest(),
        mode=args.mode,
        model="gpt-6-luna",
        cli_version=version,
        cases=len(tasks),
        timeout_seconds=300,
        retries=0,
        policy=policy,
    )
    (args.output / "contract.json").write_text(json.dumps(contract, indent=2))

    def execute(task):
        return provider.fetch(task, 300)

    def commit(job, result, error):
        if error:
            raise error
        folder = args.output / job.key
        folder.mkdir()
        metadata = result.metadata.copy()
        (folder / "tool-log.json").write_bytes(
            base64.b64decode(metadata.pop("_diagnostic_log_b64"))
        )
        (folder / "response.txt").write_text(result.content, encoding="utf-8")
        for i, file in enumerate(result.files):
            (folder / f"file-{i}.bin").write_bytes(file["data"])
        receipt = dict(
            case_id=job.payload.id,
            url=job.payload.url,
            ok=result.ok,
            error=result.error_message,
            **metadata,
        )
        (folder / "receipt.json").write_text(json.dumps(receipt, indent=2))
        print(job.key, job.payload.id, "ok" if result.ok else "failed", flush=True)

    jobs = [Job(f"{i:04}", ("conversation",), task) for i, task in enumerate(tasks, 1)]
    run_jobs(jobs, execute, commit, ExecutionPolicy.from_dict(policy))
    return 0
