"""The public WebArena recordings benchmark on the shared Trajectory Replay runner."""

from __future__ import annotations

import hashlib
import json
import re
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from harness.container_isolation import require_linux_cgroup_v2
from harness.distribution import bundled_data_path
from harness.environments.browser import (
    BROWSERS,
    container_engines,
    reference_engine,
    validate_resource_controls,
)
from harness.record_io import write_json
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
from harness.trajectory_replay.core.evaluators import EvaluatorRegistry, TaskEvidence
from harness.trajectory_replay.core.runner import ReplayContext, ReplayOptions, prepare_page
from harness.trajectory_replay.core.suite import BrowserTask
from harness.trajectory_replay.public_webarena import evaluators as _evaluators
from harness.trajectory_replay.public_webarena.config import map_strings
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.public_webarena.har import CdpHarRecorder
from harness.trajectory_replay.public_webarena.metrics import summarize_attempts
from harness.trajectory_replay.public_webarena.qualification import task_contract_sha256
from harness.trajectory_replay.public_webarena.report import write_latest_report
from harness.trajectory_replay.public_webarena.response_builder import build_agent_response
from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.public_webarena.webarena_environment import (
    WebArenaEnvironment,
    wall_clock_timeout,
)

# The WebArena-Verified evaluator kind registers on import.
assert "webarena_verified" in _evaluators.EVALUATOR_KINDS
ENGINE_IMAGES = {engine: str(BROWSERS.get(engine).image) for engine in container_engines()}
# Public image recipes pin browser versions; the curated replay may build the same image
# tag from a newer release, so a measured container must report the pinned version.
IMAGE_RECIPES = "examples/trajectory_replay/public_webarena/docker"


def pinned_image_version(engine: str, root: Path | None = None) -> str | None:
    """Return the version the public image recipe pins with ``ARG <ENGINE>_VERSION``."""
    relative = f"{IMAGE_RECIPES}/{engine}.Dockerfile"
    try:
        # Version-pinning recipes ship as bundled data so installed runs read the same pin.
        recipe = bundled_data_path(relative) if root is None else root / relative
    except FileNotFoundError:
        return None
    if not recipe.is_file():
        return None
    match = re.search(
        rf"^ARG {re.escape(engine.upper())}_VERSION=v?(\S+)$",
        recipe.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return match.group(1) if match else None


def _verify_pinned_versions(containers: dict[str, Any]) -> None:
    """Refuse a container whose browser does not report its recipe's pinned version."""
    for engine, container in containers.items():
        adapter = BROWSERS.get(engine)
        if adapter.version_command is None:
            continue
        recipe = f"{IMAGE_RECIPES}/{engine}.Dockerfile"
        version = pinned_image_version(engine)
        if version is None:
            raise RuntimeError(f"public replay has no pinned {adapter.label} version in {recipe}")
        reported = container.exec_run(list(adapter.version_command)).output
        reported = reported.decode("utf-8", errors="replace").strip()
        if version not in [part.lstrip("v") for part in reported.split()]:
            raise RuntimeError(
                f"{ENGINE_IMAGES[engine]} reports {reported!r}; public replay requires "
                f"{adapter.label} {version} built from {recipe}"
            )


def _remaining_task_budget_ms(deadline: float, *, phase: str) -> int:
    remaining_ms = int((deadline - time.perf_counter()) * 1000)
    if remaining_ms < 1:
        raise TimeoutError(f"task wall-clock budget exhausted before {phase}")
    return remaining_ms


def _capture_final_page_evidence(page: Page, artifact_dir: Path) -> dict[str, Any]:
    """Persist post-HAR visual/page evidence and fail closed when it is unavailable."""
    screenshot_path = artifact_dir / "final.png"
    page_path = artifact_dir / "final-page.json"
    candidates = [candidate for candidate in page.context.pages if not candidate.is_closed()]
    capture_page = candidates[-1] if candidates else page
    capture_errors: list[dict[str, str]] = []
    try:
        capture_page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except Exception as exc:
        capture_errors.append({"stage": "load_state", "error": type(exc).__name__})

    screenshot_bytes = b""
    for _ in range(3):
        try:
            screenshot_bytes = capture_page.screenshot(
                path=str(screenshot_path), full_page=False, timeout=5_000
            )
            if not screenshot_bytes:
                screenshot_bytes = screenshot_path.read_bytes()
            break
        except Exception as exc:
            capture_errors.append({"stage": "screenshot", "error": type(exc).__name__})
            capture_page.wait_for_timeout(250)

    dom_evidence = None
    for _ in range(3):
        try:
            dom_evidence = capture_page.evaluate(
                r"""() => {
          const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
          const candidates = Array.from(document.querySelectorAll(
            'a, button, input, select, textarea, [role], [contenteditable="true"], [tabindex]'
          ));
          return {
            visibleText: normalize(document.body ? document.body.innerText : '').slice(0, 40000),
            interactiveElements: candidates.slice(0, 1000).map((element, index) => ({
              index,
              tag: element.tagName.toLowerCase(),
              role: element.getAttribute('role'),
              text: normalize(element.innerText || element.textContent).slice(0, 500),
              id: element.id || null,
              name: element.getAttribute('name'),
              ariaLabel: element.getAttribute('aria-label'),
              placeholder: element.getAttribute('placeholder'),
              title: element.getAttribute('title'),
              href: element.getAttribute('href'),
              type: element.getAttribute('type'),
              checked: 'checked' in element ? Boolean(element.checked) : null,
              selected: 'selected' in element ? Boolean(element.selected) : null,
              disabled: 'disabled' in element ? Boolean(element.disabled) : null,
              visible: Boolean(element.getClientRects().length),
            })),
          };
        }"""
            )
            break
        except Exception as exc:
            capture_errors.append({"stage": "dom", "error": type(exc).__name__})
            capture_page.wait_for_timeout(250)
    dom_captured = dom_evidence is not None
    if dom_evidence is None:
        dom_evidence = {"visibleText": "", "interactiveElements": []}

    open_pages = []
    for candidate in list(page.context.pages):
        try:
            open_pages.append({"url": str(candidate.url), "title": candidate.title()})
        except Exception as exc:
            open_pages.append(
                {
                    "url": str(getattr(candidate, "url", "")),
                    "title_error": type(exc).__name__,
                }
            )
    try:
        title = capture_page.title()
    except Exception as exc:
        title = ""
        capture_errors.append({"stage": "title", "error": type(exc).__name__})
    try:
        viewport = capture_page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    except Exception as exc:
        viewport = None
        capture_errors.append({"stage": "viewport", "error": type(exc).__name__})
    page_payload = {
        "url": str(capture_page.url),
        "title": title,
        "visible_text": dom_evidence["visibleText"],
        "interactive_elements": dom_evidence["interactiveElements"],
        "open_pages": open_pages,
        "viewport": viewport,
        "capture_errors": capture_errors,
    }
    page_bytes = (json.dumps(page_payload, indent=2, ensure_ascii=False) + "\n").encode()
    page_path.write_bytes(page_bytes)
    complete = bool(screenshot_bytes and dom_captured and viewport)
    return {
        "status": "complete" if complete else "partial",
        "screenshot": (
            {
                "path": screenshot_path.name,
                "sha256": hashlib.sha256(screenshot_bytes).hexdigest(),
                "bytes": len(screenshot_bytes),
            }
            if screenshot_bytes
            else None
        ),
        "page": {
            "path": page_path.name,
            "sha256": hashlib.sha256(page_bytes).hexdigest(),
            "bytes": len(page_bytes),
        },
        "capture_errors": capture_errors,
    }


def _task_viewport(task: BrowserTask) -> dict[str, int]:
    if (task.environment or {}).get("kind") == "webarena_verified":
        return {"width": 1280, "height": 720}
    return {"width": 1280, "height": 900}


def _may_reuse_webarena_site(
    task: BrowserTask, initialized_sites: set[str]
) -> bool:
    sites = set((task.environment or {}).get("sites") or [])
    return bool(sites) and "mutate" not in task.tags and sites <= initialized_sites


def _engine_site_state(engines: tuple[str, ...]) -> dict[str, set[str]]:
    """Keep reset/reuse state independent for every measured browser engine."""
    return {engine: set() for engine in engines}


def _run_task(
    engine: str,
    page: Page,
    visited_origins: set[str],
    resources: DockerStatsResources,
    task: BrowserTask,
    attempt: int,
    sample_interval_ms: int,
    evaluator_registry: EvaluatorRegistry,
    artifact_dir: Path,
    *,
    is_retry: bool = False,
    task_timeout_ms: int = 300_000,
    redact_text: Callable[[str], str] = str,
) -> dict[str, Any]:
    record_origin = main_frame_origin_recorder(page, visited_origins)
    page.on("framenavigated", record_origin)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    sampler.start()
    started = time.perf_counter()
    task_deadline = started + task_timeout_ms / 1000
    outputs: dict[str, str] = {}
    trace: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    error = ""
    recorder: CdpHarRecorder | None = None
    execution_status = "fail"
    try:
        execution_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="trajectory execution"
        )
        recorder = CdpHarRecorder(page)
        execute_steps(
            page,
            task.tool_calls,
            trace=trace,
            outputs=outputs,
            page_observer=recorder.attach,
            task_timeout_ms=execution_budget_ms,
        )
        execution_status = "pass"
    except Exception:
        error = traceback.format_exc()
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        sampler.stop()
        page.remove_listener("framenavigated", record_origin)
    after = resources.counters()
    process_check = process_validation(task, trace)
    process_passed = process_check["passed"]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    har_path = artifact_dir / "network.har"
    try:
        if recorder is None:
            raise RuntimeError("network recorder did not start")
        har_summary = recorder.stop(har_path)
    except Exception:
        har_summary = {"status": "error"}
        error = f"{error}\n{traceback.format_exc()}".strip()
    try:
        evidence_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="final evidence capture"
        )
        with wall_clock_timeout(
            evidence_budget_ms / 1000, operation="final evidence capture"
        ):
            final_page_evidence = _capture_final_page_evidence(page, artifact_dir)
    except Exception:
        final_page_evidence = {"status": "error"}
        error = f"{error}\n{traceback.format_exc()}".strip()
    try:
        evaluator_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="result evaluation"
        )
        with wall_clock_timeout(evaluator_budget_ms / 1000, operation="result evaluation"):
            agent_response = build_agent_response(task, outputs)
            if agent_response is not None:
                write_json(artifact_dir / "agent_response.json", agent_response)
            evaluation = evaluator_registry.evaluate(
                task,
                TaskEvidence(
                    outputs=outputs,
                    process_passed=process_passed,
                    agent_response=agent_response,
                    network_har_path=har_path if har_path.is_file() else None,
                ),
            )
        validation = list(evaluation.checks)
        result_passed = evaluation.passed
        if evaluation.error:
            error = "; ".join(part for part in (error, evaluation.error) if part)
    except Exception:
        result_passed = False
        error = f"{error}\n{traceback.format_exc()}".strip()
        evaluation = None
    evidence_passed = (
        har_summary.get("status") != "error" and final_page_evidence.get("status") == "complete"
    )
    status = (
        "pass"
        if execution_status == "pass" and process_passed and result_passed and evidence_passed
        else "fail"
    )
    # Harness and evaluator output only: the earlier steps stay what the frozen trajectory executed.
    error = redact_text(error)
    validation = map_strings(validation, redact_text)
    trace.append(
        {
            "index": len(task.tool_calls) + 1,
            "action": "evaluate_result",
            "arguments": {"evaluator": (task.evaluator or {}).get("kind")},
            "status": "pass" if result_passed else "fail",
            "duration_ms": 0.0,
            "output": "evaluator passed" if result_passed else error,
            "checks": validation,
        }
    )
    return {
        **attempt_identity(engine, task, attempt, is_retry=is_retry),
        "status": status,
        "result_status": "pass" if result_passed else "fail",
        "process_status": "pass" if process_passed else "fail",
        "combined_status": (
            "pass" if result_passed and process_passed and evidence_passed else "fail"
        ),
        "duration_ms": round(duration_ms, 3),
        **output_volume(outputs),
        "error": error,
        "trace": trace,
        "validation": validation,
        "evaluator": (task.evaluator or {}).get("kind"),
        "evaluator_status": evaluation.status if evaluation is not None else "error",
        "network_har": har_summary,
        "final_page_evidence": final_page_evidence,
        "evidence_status": "pass" if evidence_passed else "fail",
        "evidence_directory": str(artifact_dir),
        "process_validation": process_check,
        "resource": resource_result(sampler, before, after, duration_ms),
    }


def _environment_failure_row(
    engine: str,
    task: BrowserTask,
    attempt: int,
    artifact_dir: Path,
    *,
    is_retry: bool,
    duration_ms: float,
    error: str,
) -> dict[str, Any]:
    """Represent reset/prepare failure as a fail-closed physical attempt."""
    expected_actions = list(task.ground_truth.get("expected_actions") or [])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trace = [
        {
            "index": 0,
            "action": "prepare_environment",
            "arguments": {"environment": (task.environment or {}).get("kind")},
            "status": "fail",
            "duration_ms": round(duration_ms, 3),
            "output": "environment reset or preparation failed before trajectory execution",
        }
    ]
    return {
        **attempt_identity(engine, task, attempt, is_retry=is_retry),
        "status": "fail",
        "result_status": "fail",
        "process_status": "fail",
        "combined_status": "fail",
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": 0,
        "output_chars": 0,
        "error": error,
        "trace": trace,
        "validation": [],
        "evaluator": (task.evaluator or {}).get("kind"),
        "evaluator_status": "not_run",
        "network_har": {"status": "error", "reason": "environment_prepare_failed"},
        "final_page_evidence": {
            "status": "error",
            "reason": "environment_prepare_failed",
        },
        "evidence_status": "fail",
        "evidence_directory": str(artifact_dir),
        "process_validation": {
            "expected_actions": expected_actions,
            "actual_actions": [],
            "all_calls_passed": False,
            "passed": False,
        },
        "resource": {},
        "termination_reason": "environment_prepare_failed",
    }


def _bounded_authoring_task(task: BrowserTask, timeout_ms: int = 5_000) -> BrowserTask:
    """Cap diagnostic target waits even for ad-hoc repaired authoring suites."""
    calls: list[dict[str, Any]] = []
    for raw in task.tool_calls:
        call = dict(raw)
        if call.get("selector") is not None or isinstance(call.get("target"), dict):
            call["timeout_ms"] = min(int(call.get("timeout_ms", timeout_ms)), timeout_ms)
        if isinstance(call.get("target"), dict):
            call["target_timeout_ms"] = min(
                int(call.get("target_timeout_ms", timeout_ms)), timeout_ms
            )
        calls.append(call)
    trajectory = dict(task.trajectory or {})
    trajectory["actions"] = calls
    return BrowserTask(
        task_id=task.task_id,
        title=task.title,
        tool_calls=tuple(calls),
        assertions=task.assertions,
        tags=task.tags,
        difficulty=task.difficulty,
        ground_truth=task.ground_truth,
        source=task.source,
        evaluator=task.evaluator,
        environment=task.environment,
        trajectory=trajectory,
    )


def _environment(client: Any, containers: dict[str, Any]) -> dict[str, Any]:
    environment = docker_environment(client, containers)
    for engine, container in containers.items():
        environment["images"][engine]["build_source"] = {
            key: value
            for key, value in (
                (container.image.attrs.get("Config") or {}).get("Labels") or {}
            ).items()
            if key.startswith("lex.webstone.")
        }
    return environment


class PublicWebArenaReplay:
    """Public WebArena human recordings, with evidence capture and registered evaluators.

    Formal runs measure the qualified edition; qualification and trajectory
    authoring runs use the candidate edition on the reference engine only.
    """

    name = "public-webarena"
    family = "human"

    def __init__(
        self,
        *,
        suite_path: str | Path | None = None,
        task_timeout_ms: int = 300_000,
        webarena_config_path: str | Path | None = None,
        qualification_run: bool = False,
        qualification_pass_index: int | None = None,
        qualification_required_passes: int | None = None,
        trajectory_authoring_run: bool = False,
        task_ids: tuple[str, ...] | None = None,
        task_selection: dict[str, Any] | None = None,
    ) -> None:
        if task_timeout_ms < 1:
            raise ValueError("task_timeout_ms must be positive")
        if qualification_run and trajectory_authoring_run:
            raise ValueError(
                "qualification_run and trajectory_authoring_run are mutually exclusive"
            )
        if qualification_run:
            if qualification_required_passes != 2 or qualification_pass_index not in {1, 2}:
                raise ValueError(
                    "qualification requires pass index 1 or 2 and exactly two independent passes"
                )
        elif qualification_pass_index is not None or qualification_required_passes is not None:
            raise ValueError("qualification pass identity is valid only for qualification runs")
        self.suite_path = suite_path
        self.task_timeout_ms = task_timeout_ms
        self.webarena_config_path = webarena_config_path
        self.qualification_run = qualification_run
        self.qualification_pass_index = qualification_pass_index
        self.qualification_required_passes = qualification_required_passes
        self.trajectory_authoring_run = trajectory_authoring_run
        self.task_ids = task_ids
        self.task_selection = task_selection
        self.environment: WebArenaEnvironment | None = None
        self.redact_text: Callable[[str], str] = str
        self.evaluators: EvaluatorRegistry | None = None
        self.site_client: Any = None
        self.webarena_runtime: dict[str, Any] | None = None
        self.uses_webarena = False
        self.authoring_initialized_sites: set[str] = set()
        self.initialized_sites_by_engine: dict[str, set[str]] = {}
        self.engines: tuple[str, ...] = ()

    @property
    def _diagnostic_run(self) -> bool:
        return self.qualification_run or self.trajectory_authoring_run

    def images(self, options: ReplayOptions) -> dict[str, str]:
        engines = tuple(options.engines)
        if self._diagnostic_run and (engines != (reference_engine(),) or options.max_retries):
            raise ValueError(
                "qualification and trajectory authoring require one "
                f"{BROWSERS.get(reference_engine()).label} attempt and no retries"
            )
        self.engines = engines
        self.initialized_sites_by_engine = _engine_site_state(engines)
        return ENGINE_IMAGES

    def suite(self):
        from harness.trajectory_replay.report_inputs import replay_input_path

        suite_path = replay_input_path(
            self.suite_path,
            current="human-candidates" if self._diagnostic_run else "human",
        )
        suite = load_suite(suite_path)
        declared_ids = {task.task_id for task in suite.tasks}
        selected_ids = set(self.task_ids or declared_ids)
        unknown_ids = selected_ids - declared_ids
        if unknown_ids:
            raise ValueError(f"unknown task ids: {sorted(unknown_ids)}")
        if not selected_ids:
            raise ValueError("task_ids must select at least one task")
        selected = tuple(task for task in suite.tasks if task.task_id in selected_ids)
        self.uses_webarena = any(
            (task.environment or {}).get("kind") == "webarena_verified" for task in selected
        )
        if self.uses_webarena and self.webarena_config_path is None:
            raise ValueError("--webarena-config is required for WebArena tasks")
        states = {
            task.task_id: (task.ground_truth.get("qualification") or {}).get("status")
            for task in selected
            if (task.environment or {}).get("kind") == "webarena_verified"
        }
        allowed = {"pending"} if self._diagnostic_run else {"eligible", "skipped"}
        invalid = {task_id: state for task_id, state in states.items() if state not in allowed}
        if invalid:
            mode = (
                "qualification"
                if self.qualification_run
                else "trajectory authoring"
                if self.trajectory_authoring_run
                else "formal measurement"
            )
            raise ValueError(
                f"WebArena {mode} has invalid qualification states: {invalid}; "
                f"allowed states are {sorted(allowed)}"
            )
        runnable = tuple(
            task
            for task in selected
            if self.qualification_run
            or (task.ground_truth.get("qualification") or {}).get("status") != "skipped"
        )
        if self.trajectory_authoring_run:
            state_changing = [
                task.task_id
                for task in runnable
                if "mutate" in task.tags or not ({"retrieve", "navigate"} & set(task.tags))
            ]
            if state_changing:
                raise ValueError(
                    "trajectory_authoring_run permits non-mutating retrieve/navigate tasks only: "
                    + ", ".join(state_changing)
                )
        self.runnable = runnable
        # Validate the benchmark/runtime binding before touching Docker, so a wrong
        # environment config cannot waste a remote daemon round trip.
        if self.webarena_config_path is not None:
            self.environment = WebArenaEnvironment(self.webarena_config_path)
            self.redact_text = self.environment.redact_login_passwords
        return Path(suite_path), suite, selected, runnable

    @contextmanager
    def inputs(self, client: Any) -> Iterator[None]:
        validate_resource_controls(client.info())
        require_linux_cgroup_v2(client)
        self.evaluators = EvaluatorRegistry(config_path=self.webarena_config_path)
        self.site_client = client
        close_site_client = False
        if self.environment is not None and self.uses_webarena:
            self.site_client, close_site_client = self.environment.site_docker_client(client)
        try:
            self.webarena_runtime = (
                self.environment.verify_runtime(self.site_client, self.runnable)
                if self.environment is not None and self.uses_webarena
                else None
            )
            yield
        finally:
            if close_site_client:
                self.site_client.close()

    def started(self, runtime: EngineRuntime) -> None:
        _verify_pinned_versions(runtime.containers)

    def prepare_task(self, task: BrowserTask) -> BrowserTask:
        return _bounded_authoring_task(task) if self.trajectory_authoring_run else task

    def _reset_sites(
        self, engine: str, task: BrowserTask, deadline: float
    ) -> dict[str, Any]:
        """Reset the task's sites unless a warm, non-mutating reuse is allowed."""
        assert self.environment is not None
        initialized_sites = self.initialized_sites_by_engine[engine]
        task_sites = set((task.environment or {}).get("sites") or [])
        if self.trajectory_authoring_run and task_sites <= self.authoring_initialized_sites:
            return {
                "diagnostic_only": True,
                "mode": "warm_non_mutating_authoring_reuse",
                "sites": sorted(task_sites),
            }
        if _may_reuse_webarena_site(task, initialized_sites):
            return {
                "diagnostic_only": bool(self.qualification_run),
                "mode": "warm_non_mutating_site_reuse",
                "sites": sorted(task_sites),
            }
        if "mutate" in task.tags:
            initialized_sites.difference_update(task_sites)
        reset_budget_ms = _remaining_task_budget_ms(deadline, phase="environment reset")
        environment_reset = self.environment.reset(
            task, self.site_client, timeout_seconds=reset_budget_ms / 1000
        )
        if self.trajectory_authoring_run:
            self.authoring_initialized_sites.update(task_sites)
        if "mutate" not in task.tags:
            initialized_sites.update(task_sites)
        return environment_reset

    def attempt(
        self, context: ReplayContext, engine: str, base_task: BrowserTask, attempt: int
    ) -> dict[str, Any]:
        runtime = context.runtime
        options = context.options
        assert self.evaluators is not None
        task = base_task
        artifact_dir = context.output / "tasks" / task.task_id / engine / f"attempt-{attempt}"
        environment_reset = None
        attempt_started = time.perf_counter()
        attempt_deadline = attempt_started + self.task_timeout_ms / 1000
        try:
            runtime.pages[engine] = prepare_page(
                runtime.pages[engine],
                runtime.visited_origins[engine],
                settle_ms=options.settle_ms,
                viewport=_task_viewport(task),
            )
            if (task.environment or {}).get("kind") == "webarena_verified":
                assert self.environment is not None
                environment_reset = self._reset_sites(engine, task, attempt_deadline)
                task = self.environment.materialize_task_actions(base_task)
                prepare_budget_ms = _remaining_task_budget_ms(
                    attempt_deadline, phase="environment prepare"
                )
                runtime.pages[engine] = self.environment.prepare(
                    task, runtime.pages[engine], timeout_seconds=prepare_budget_ms / 1000
                )
            execution_budget_ms = _remaining_task_budget_ms(
                attempt_deadline, phase="trajectory execution"
            )
        except Exception:
            row = _environment_failure_row(
                engine,
                task,
                attempt,
                artifact_dir,
                is_retry=attempt > 1,
                duration_ms=(time.perf_counter() - attempt_started) * 1000,
                error=self.redact_text(traceback.format_exc()),
            )
        else:
            row = _run_task(
                engine,
                runtime.pages[engine],
                runtime.visited_origins[engine],
                runtime.resources[engine],
                task,
                attempt,
                options.sample_interval_ms,
                self.evaluators,
                artifact_dir,
                is_retry=attempt > 1,
                task_timeout_ms=execution_budget_ms,
                redact_text=self.redact_text,
            )
            row["execution_duration_ms"] = row["duration_ms"]
            row["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
        row["run_id"] = context.run_id
        row["qualification_run"] = self.qualification_run
        row["qualification_pass_index"] = (
            self.qualification_pass_index if self.qualification_run else None
        )
        row["qualification_required_passes"] = (
            self.qualification_required_passes if self.qualification_run else None
        )
        row["trajectory_authoring_run"] = self.trajectory_authoring_run
        row["environment_reset"] = environment_reset
        row["task_contract_sha256"] = task_contract_sha256(
            {
                "id": task.task_id,
                "title": task.title,
                "trajectory": task.trajectory,
                "evaluator": task.evaluator,
                "environment": task.environment,
                "ground_truth": task.ground_truth,
                "source": task.source,
            }
        )
        attempt_path = artifact_dir / "attempt.json"
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_text(
            json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return row

    def manifest(self, context, client, runnable, selected, uses_local_fixtures):
        options = context.options
        qualification = self.qualification_run
        return {
            "suite": [
                {
                    **manifest_task(task),
                    "trajectory": task.trajectory,
                    "evaluator": task.evaluator,
                    "environment": task.environment,
                }
                for task in runnable
            ],
            "protocol": {
                "browser_reuse": True,
                "viewport": {
                    "repository_curated": {"width": 1280, "height": 900},
                    "webarena_verified": {"width": 1280, "height": 720},
                },
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
                "browser_state_reset_between_tasks": True,
                "browser_state_reset_method": (
                    "warm browser/context; clear all top-level navigation origins, browser "
                    "cache, cookies, permissions and history; replace renderer page"
                ),
                "webarena_site_reset_policy": (
                    "reset once per site and engine for a contiguous/non-mutating task set; "
                    "reset before every state-changing task and invalidate reuse afterward"
                ),
                "sample_interval_ms": options.sample_interval_ms,
                "post_reset_settle_ms": options.settle_ms,
                "task_timeout_ms": self.task_timeout_ms,
                "primary_attempts_per_task": 1,
                "max_failure_retries": options.max_retries,
                "engine_order": "balanced by task index",
                "engines": list(options.engines),
                "qualification_run": qualification,
                "qualification_pass_index": (
                    self.qualification_pass_index if qualification else None
                ),
                "qualification_required_passes": (
                    self.qualification_required_passes if qualification else None
                ),
                "trajectory_authoring_run": self.trajectory_authoring_run,
                "trajectory_authoring_scope": (
                    "diagnostic-only non-mutating retrieve/navigate tasks; one site reset before "
                    "each site's warm batch; never accepted as qualification evidence"
                    if self.trajectory_authoring_run
                    else None
                ),
                "task_filter": [task.task_id for task in runnable],
                "task_selection": self.task_selection,
                "skipped_qualified_tasks": [
                    task.task_id
                    for task in selected
                    if (task.ground_truth.get("qualification") or {}).get("status") == "skipped"
                ],
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
                "required_attempt_evidence": [
                    "attempt.json",
                    "network.har",
                    "final.png",
                    "final-page.json",
                ],
                "missing_required_evidence": "attempt fails closed",
            },
            "environment": _environment(client, context.runtime.containers),
            "webarena_runtime": self.webarena_runtime,
        }

    def summarize(self, rows: list[dict[str, Any]], engines: tuple[str, ...]) -> dict[str, Any]:
        return summarize_attempts(rows, engines=engines)

    def write_report(self, reports_dir: Path, manifest: dict, summary: dict) -> None:
        if not self._diagnostic_run:
            write_latest_report(reports_dir, manifest=manifest, summary=summary)
