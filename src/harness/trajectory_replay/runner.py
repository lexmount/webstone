"""The curated public-web Trajectory Replay benchmark on the shared replay runner."""

from __future__ import annotations

import time
import traceback
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from harness.environments.browser import configured_browser_images
from harness.resource_accounting import DockerStatsResources, ResourceSampler
from harness.trajectory_replay.core.docker_runner import (
    EngineRuntime,
    attempt_identity,
    docker_environment,
    main_frame_origin_recorder,
    manifest_task,
    output_volume,
    process_validation,
    resource_result,
)
from harness.trajectory_replay.core.executor import judge_outputs, validate_outputs
from harness.trajectory_replay.core.runner import (
    ReplayContext,
    ReplayOptions,
    prepare_page,
)
from harness.trajectory_replay.core.suite import BrowserTask
from harness.trajectory_replay.executor import execute_steps
from harness.trajectory_replay.metrics import summarize_attempts
from harness.trajectory_replay.report import write_latest_report
from harness.trajectory_replay.suite import load_suite


def _run_task(
    engine: str,
    page: Page,
    visited_origins: set[str],
    resources: DockerStatsResources,
    task: BrowserTask,
    attempt: int,
    sample_interval_ms: int,
    *,
    is_retry: bool = False,
) -> dict[str, Any]:
    record_origin = main_frame_origin_recorder(page, visited_origins)
    page.on("framenavigated", record_origin)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    sampler.start()
    started = time.perf_counter()
    outputs: dict[str, str] = {}
    trace: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    error = ""
    try:
        execute_steps(page, task.tool_calls, trace=trace, outputs=outputs)
        validation = judge_outputs(outputs, task.assertions)
        failures = validate_outputs(outputs, task.assertions)
        status = "pass" if not failures else "fail"
        error = "; ".join(failures)
        trace.append(
            {
                "index": len(task.tool_calls) + 1,
                "action": "validate_assertions",
                "arguments": {"assertions": list(task.assertions)},
                "status": status,
                "duration_ms": 0.0,
                "output": "all assertions passed" if not failures else error,
                "checks": validation,
            }
        )
    except Exception:
        status = "fail"
        error = traceback.format_exc()
        validation = judge_outputs(outputs, task.assertions)
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        sampler.stop()
        page.remove_listener("framenavigated", record_origin)
    after = resources.counters()
    process_check = process_validation(task, trace)
    process_passed = process_check["passed"]
    result_passed = bool(validation) and all(item.get("passed") is True for item in validation)
    return {
        **attempt_identity(engine, task, attempt, is_retry=is_retry),
        "status": status,
        "result_status": "pass" if result_passed else "fail",
        "process_status": "pass" if process_passed else "fail",
        "combined_status": "pass" if result_passed and process_passed else "fail",
        "duration_ms": round(duration_ms, 3),
        **output_volume(outputs),
        "error": error,
        "trace": trace,
        "validation": validation,
        "process_validation": process_check,
        "resource": resource_result(sampler, before, after, duration_ms),
    }


class CuratedReplay:
    """Exact-output tasks on public pages and served fixtures, one retry on failure."""

    name = "curated"
    family = "curated"

    def __init__(self, *, suite_path: str | Path | None = None) -> None:
        self.suite_path = suite_path

    def images(self, options: ReplayOptions) -> dict[str, str]:
        return configured_browser_images()

    def suite(self):
        from harness.trajectory_replay.report_inputs import replay_input_path

        path = replay_input_path(self.suite_path, current="curated")
        suite = load_suite(path)
        return Path(path), suite, suite.tasks, suite.tasks

    def inputs(self, client: Any):
        return nullcontext()

    def started(self, runtime: EngineRuntime) -> None:
        return None

    def prepare_task(self, task: BrowserTask) -> BrowserTask:
        return task

    def attempt(
        self, context: ReplayContext, engine: str, task: BrowserTask, attempt: int
    ) -> dict[str, Any]:
        runtime = context.runtime
        runtime.pages[engine] = prepare_page(
            runtime.pages[engine],
            runtime.visited_origins[engine],
            settle_ms=context.options.settle_ms,
            all_tabs=False,
        )
        row = _run_task(
            engine,
            runtime.pages[engine],
            runtime.visited_origins[engine],
            runtime.resources[engine],
            task,
            attempt,
            context.options.sample_interval_ms,
            is_retry=attempt > 1,
        )
        row["run_id"] = context.run_id
        return row

    def manifest(self, context, client, runnable, selected, uses_local_fixtures):
        options = context.options
        return {
            "suite": [manifest_task(task) for task in runnable],
            "protocol": {
                "browser_reuse": True,
                "viewport": {"width": 1280, "height": 900},
                "viewport_verified_before_each_task": True,
                "step_definition": (
                    "one declared CowAgent BrowserTool-equivalent call; implicit waits excluded"
                ),
                "llm_scope": ("LLM never judges success; optional LLM only explains failed traces"),
                "llm_failure_diagnostics": options.llm_diagnostics,
                "diagnosis_model": options.diagnosis_model if options.llm_diagnostics else None,
                "diagnosis_reasoning_effort": (
                    options.diagnosis_reasoning_effort if options.llm_diagnostics else None
                ),
                "cold_start_excluded": True,
                "state_reset_between_tasks": True,
                "state_reset_method": (
                    "warm browser/context; clear all top-level navigation origins, "
                    "browser cache, cookies, permissions and history; replace renderer page"
                ),
                "sample_interval_ms": options.sample_interval_ms,
                "post_reset_settle_ms": options.settle_ms,
                "primary_attempts_per_task": 1,
                "max_failure_retries": options.max_retries,
                "engines": list(options.engines),
                "engine_order": "balanced by task index",
                "target_mode": "public_web" if not uses_local_fixtures else "mixed",
                "fixture_origin": (
                    "temporary self-hosted deterministic fixture server"
                    if uses_local_fixtures
                    else None
                ),
                "resource_scope": "browser container cgroup/network namespace",
                "controller_excluded": True,
                "cpus_per_browser": options.cpus,
                "memory_per_browser": options.memory,
                "network_isolation": "one dedicated bridge with no peer containers per engine",
            },
            "environment": docker_environment(client, context.runtime.containers),
        }

    def summarize(self, rows: list[dict[str, Any]], engines: tuple[str, ...]) -> dict[str, Any]:
        return summarize_attempts(rows, engines=engines)

    def write_report(self, reports_dir: Path, manifest: dict, summary: dict) -> None:
        write_latest_report(reports_dir, manifest=manifest, summary=summary)
