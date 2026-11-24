"""The one Docker-isolated Trajectory Replay runner loop.

A replay benchmark supplies the steps that differ through a ``ReplayBenchmark``
adapter: its tasks, its input environment, one measured attempt, its manifest
protocol, its aggregation and its report. The loop owns engine start-up, the
fixture server, the balanced task/engine/retry order, record writing and
cleanup, so every replay benchmark runs through the same skeleton.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

import docker
from playwright.sync_api import Page, sync_playwright

from harness.environments.browser import cleanup_browser_runtime, rotated_engine_order
from harness.safety import unlinked_path
from harness.trajectory_replay.core.diagnostics import diagnose_summary
from harness.trajectory_replay.core.docker_runner import (
    EngineRuntime,
    ground_truth_validation,
    http_origin,
    materialize_task,
    open_task_page,
    print_attempt_progress,
    reset_browser_state,
    start_engine,
    start_fixture_server,
    stop_fixture_server,
    write_run_artifacts,
)
from harness.trajectory_replay.core.suite import (
    BrowserTask,
    TaskSuite,
    validate_ground_truth_evidence,
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 900}


@dataclass(frozen=True)
class ReplayOptions:
    """Run options every replay benchmark shares."""

    output_dir: Path
    reports_dir: Path
    engines: tuple[str, ...]
    ground_truth_evidence_path: str | Path | None = None
    agent_smoke_path: str | Path | None = None
    max_retries: int = 1
    sample_interval_ms: int = 50
    settle_ms: int = 1500
    cpus: float = 2.0
    memory: str = "2g"
    llm_diagnostics: bool = False
    diagnosis_model: str = ""
    diagnosis_reasoning_effort: str = "low"
    generate_report: bool = True


@dataclass
class ReplayContext:
    """What one measured attempt may use; built by the loop."""

    options: ReplayOptions
    runtime: EngineRuntime
    run_id: str
    output: Path


class ReplayBenchmark(Protocol):
    """The steps a replay benchmark supplies to the shared loop."""

    name: str
    # Locked suite family: the default input and the manifest's dataset binding.
    family: str

    def images(self, options: ReplayOptions) -> dict[str, str]:
        """Container image per runnable engine; rejects options the benchmark cannot run."""
        ...

    def suite(self) -> tuple[Path, TaskSuite, tuple[BrowserTask, ...], tuple[BrowserTask, ...]]:
        """Suite path, suite, selected tasks and the runnable subset."""
        ...

    def inputs(self, client: Any) -> AbstractContextManager[None]:
        """Input environment for the calls: live web, served fixtures or deployed sites."""
        ...

    def started(self, runtime: EngineRuntime) -> None:
        """Check the started engines before any task runs."""
        ...

    def prepare_task(self, task: BrowserTask) -> BrowserTask:
        """Per-run task rewrite applied once before the engines run it."""
        ...

    def attempt(
        self, context: ReplayContext, engine: str, task: BrowserTask, attempt: int
    ) -> dict[str, Any]:
        """Run and score one physical attempt and return its row."""
        ...

    def manifest(
        self,
        context: ReplayContext,
        client: Any,
        runnable: tuple[BrowserTask, ...],
        selected: tuple[BrowserTask, ...],
        uses_local_fixtures: bool,
    ) -> dict[str, Any]:
        """``suite``, ``protocol``, ``environment`` and any benchmark-specific keys."""
        ...

    def summarize(self, rows: list[dict[str, Any]], engines: tuple[str, ...]) -> dict[str, Any]:
        ...

    def write_report(
        self, reports_dir: Path, manifest: dict[str, Any], summary: dict[str, Any]
    ) -> None:
        """Build the benchmark's local report; a no-op when the run is not reportable."""
        ...


def run_engines(
    profile_path: str | Path | None, engines: str | None, default: tuple[str, ...]
) -> tuple[str, ...]:
    """Participants for one run: the profile's methods, optionally narrowed by ``engines``.

    Without a profile, ``engines`` selects from ``default`` (the registered engines).
    A supplied ``engines`` that names nothing is an error, never "all engines".
    """
    selected = tuple(item.strip() for item in (engines or "").split(",") if item.strip())
    if engines is not None and not selected:
        raise ValueError("--engines must name at least one engine")
    if profile_path is None:
        return selected or default
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    methods = profile.get("methods") or []
    declared = tuple(str((method or {}).get("engine") or "") for method in methods)
    if not declared or not all(declared) or len(set(declared)) != len(declared):
        raise ValueError("profile methods must name unique engines")
    if set(selected) - set(declared):
        raise ValueError(f"engines {sorted(set(selected) - set(declared))} are not profile methods")
    return tuple(engine for engine in declared if engine in selected) if selected else declared


def reset_state(page: Page, visited_origins: set[str], *, all_tabs: bool = True) -> None:
    """Clear every origin the task reached; ``all_tabs`` adds those of extra open tabs."""
    origins = set(visited_origins)
    pages = list(getattr(page.context, "pages", [page])) if all_tabs else [page]
    for existing in pages:
        current_origin = http_origin(existing.url)
        if current_origin:
            origins.add(current_origin)
    reset_browser_state(page, origins)


def prepare_page(
    page: Page,
    visited_origins: set[str],
    *,
    settle_ms: int,
    viewport: dict[str, int] | None = None,
    all_tabs: bool = True,
) -> Page:
    """Reset task state and replace the renderer page, keeping the browser/context warm.

    With ``all_tabs`` every tab the task opened is reset and closed; otherwise only
    the task page is, as the curated benchmark's published runs did.
    """
    context = page.context
    reset_state(page, visited_origins, all_tabs=all_tabs)
    visited_origins.clear()
    pages = list(getattr(context, "pages", [page])) if all_tabs else [page]
    for existing in pages:
        existing.close(run_before_unload=False)
    return open_task_page(context, viewport or DEFAULT_VIEWPORT, settle_ms=settle_ms)


@contextmanager
def _fixtures(root: Path | None) -> Iterator[str]:
    runtime: tuple[ThreadingHTTPServer, threading.Thread, str] | None = None
    if root is not None:
        runtime = start_fixture_server(root)
    try:
        yield runtime[2] if runtime else ""
    finally:
        if runtime:
            stop_fixture_server(runtime)


def run_replay(benchmark: ReplayBenchmark, options: ReplayOptions) -> dict[str, Any]:
    """Run ``benchmark`` on ``options.engines`` and write its evidence."""
    if options.max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if options.cpus <= 0:
        raise ValueError("cpus must be positive for measured runs")
    engines = tuple(options.engines)
    images = benchmark.images(options)
    if not engines or len(set(engines)) != len(engines) or set(engines) - set(images):
        raise ValueError(f"engines must be a unique subset of {tuple(images)}")
    output = unlinked_path(Path(options.output_dir))
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is immutable")
    from harness.trajectory_replay.report_inputs import (
        agent_smoke_input,
        replay_fixture_inputs,
        replay_suite_binding,
    )

    smoke = agent_smoke_input(options.agent_smoke_path)
    suite_path, suite, selected, runnable = benchmark.suite()
    fixture_root, fixture_hashes = replay_fixture_inputs(
        Path(suite_path), (call for task in selected for call in task.tool_calls)
    )
    ground_truth_evidence = (
        validate_ground_truth_evidence(suite_path, suite, options.ground_truth_evidence_path)
        if options.ground_truth_evidence_path is not None
        else None
    )
    # Inputs may load slowly; the destination must still be new and unlinked.
    output = unlinked_path(output)
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is immutable")
    client = docker.from_env()
    run_id = uuid.uuid4().hex[:12]
    runtime = EngineRuntime()
    context = ReplayContext(options=options, runtime=runtime, run_id=run_id, output=output)
    rows: list[dict[str, Any]] = []
    with benchmark.inputs(client):
        playwright = sync_playwright().start()
        try:
            with _fixtures(fixture_root) as fixture_origin:
                for engine in engines:
                    start_engine(
                        runtime,
                        client,
                        playwright,
                        engine=engine,
                        image=images[engine],
                        run_id=run_id,
                        cpus=options.cpus,
                        memory=options.memory,
                        reset_state=reset_state,
                    )
                benchmark.started(runtime)
                try:
                    output.mkdir(parents=True, exist_ok=False)
                except FileExistsError as error:
                    raise ValueError(
                        "Output must be a new directory; existing evidence is immutable"
                    ) from error
                for task_index, declared in enumerate(runnable):
                    task = benchmark.prepare_task(materialize_task(declared, fixture_origin))
                    for engine in rotated_engine_order(engines, task_index):
                        for attempt in range(1, options.max_retries + 2):
                            row = benchmark.attempt(context, engine, task, attempt)
                            rows.append(row)
                            print_attempt_progress(row)
                            if row["status"] == "pass":
                                break
            summary = benchmark.summarize(rows, engines)
            suite_sha256 = hashlib.sha256(Path(suite_path).read_bytes()).hexdigest()
            declared = benchmark.manifest(
                context, client, runnable, selected, fixture_root is not None
            )
            manifest = {
                "schema": "lex_browser_use_manifest/2",
                "run_id": run_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fixture_files": fixture_hashes,
                "suite_sha256": suite_sha256,
                "suite_dataset": replay_suite_binding(suite_sha256, family=benchmark.family),
                "suite": declared.pop("suite"),
                "ground_truth_validation": ground_truth_validation(ground_truth_evidence),
                **declared,
            }
            if smoke is not None:
                manifest["agent_smoke"] = smoke
            # Persist core evidence before optional remote diagnostics can fail or stall.
            write_run_artifacts(output, rows, summary, manifest)
            diagnose_summary(
                summary,
                use_llm=options.llm_diagnostics,
                model=options.diagnosis_model,
                reasoning_effort=options.diagnosis_reasoning_effort,
            )
            write_run_artifacts(output, rows, summary, manifest)
            if options.generate_report:
                benchmark.write_report(options.reports_dir, manifest, summary)
            return summary
        finally:
            cleanup_browser_runtime(
                runtime.browsers, playwright, runtime.containers, runtime.networks
            )

