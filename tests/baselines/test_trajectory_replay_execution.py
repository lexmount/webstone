"""Trajectory Replay execution baseline: the WebArena catalog loops with fake browsers.

The catalog, cohort and Docker-site replays drive CowAgent BrowserService against
deployed WebArena sites, so a real run needs live infrastructure. These scenarios
replace the browser process, hosted CDP transport, BrowserTool, Docker client and
evaluator with fakes that record every call, then snapshot the ordered calls, the
written attempt files and the evidence fields. A change to the execution loops,
backends or evaluators that alters any of them changes this baseline.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from harness.record_io import file_sha256
from tests.baselines.snapshot import check_baseline

# Run-clock fields and checkout revisions vary by run, not by behaviour.
_VOLATILE = {
    "duration_ms",
    "started_at_unix_ms",
    "finished_at_unix_ms",
    "completed_at_unix_ms",
    "lock_wait_ms",
    "runner_revision",
    "webstone_commit",
}
# Cohort receipts bind the executing source files; their bytes change with any edit.
_SOURCE_BINDINGS = {
    "runner",
    "remote_execution",
    "replay_logic",
    "retrieval_acceptance",
    "correction_logic",
    "remote_runtime",
}
_TRACEBACK = re.compile(r"Traceback \(most recent call last\):\n(?:.*\n)*?(?=\S)")


class _Normal:
    def __init__(self, root: Path) -> None:
        self.roots = sorted({str(root), str(root.resolve())}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for root in self.roots:
            value = value.replace(root, "<tmp>")
        # Keep the raised exception, not file names and line numbers.
        return _TRACEBACK.sub("<traceback>\n", value)

    def __call__(self, value, key: str = ""):
        if key in _VOLATILE and value is not None:
            return "<volatile>"
        if key == "evidence_sha256" and isinstance(value, dict):
            # Traces carry measured durations; the file contents are compared instead.
            return sorted(value)
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(k)): self(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(self(item) for item in value)
        if isinstance(value, Path):
            return self.text(str(value))
        if hasattr(value, "__dict__") and not callable(value):
            # A fake runtime object passed to a call: record its type and state.
            state = {k: v for k, v in vars(value).items() if not callable(v)}
            return {"<object>": type(value).__name__, **self(state)}
        return value


def _files(root: Path, normal: _Normal) -> dict:
    """Every written file: JSON content (normalized) or its byte digest."""
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
            content = _jsonl(path) if path.suffix == ".jsonl" else json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            files[name] = {"sha256": file_sha256(path)}
            continue
        files[name] = normal(content)
    return files


def _jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def sequential_uuids(monkeypatch):
    counter = iter(range(1, 10_000))
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))


def _kwargs(kwargs: dict) -> dict:
    ignored = {"benchmark", "catalog", "portable_task"}
    return {key: value for key, value in sorted(kwargs.items()) if key not in ignored}


# --- catalog runner -----------------------------------------------------------------


def _catalog_inputs(tmp_path: Path, monkeypatch, catalog_runner, task_ids: list[int]) -> None:
    catalog = {
        "task_ids": task_ids,
        "rows": [{"task_id": task_id, "mode": "recorded"} for task_id in task_ids],
    }
    for name in ("catalog.json", "suite.json", "sites.json", "receipt.json", "environment.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "browser").write_text("{}", encoding="utf-8")
    stubs = {
        "verify_webarena_runtime_source": lambda: SimpleNamespace(revision="a" * 40),
        "load_catalog_inputs": lambda *args, **kwargs: (catalog, {}, {}),
        "validate_environment_preparation": lambda *args: None,
        "load_corrections": lambda: SimpleNamespace(source_sha256="b" * 64),
        "remote_benchmark": lambda sites: None,
        "require_site_credentials": lambda sites: None,
        "_task_origins": lambda _benchmark, task_id, _sites: {f"https://site-{task_id}.test"},
        "_bound_catalog_row": lambda _catalog, frozen, *args: {
            **frozen,
            "actions": [{"action": "evaluate", "script": f"task {frozen['task_id']}"}],
        },
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(catalog_runner, name, stub)


def _catalog_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run",
        *("--catalog", str(tmp_path / "catalog.json")),
        *("--portable-suite", str(tmp_path / "suite.json")),
        *("--site-map", str(tmp_path / "sites.json")),
        *("--site-reset-receipt", str(tmp_path / "receipt.json")),
        *("--environment-profile", str(tmp_path / "environment.json")),
        *("--backend", "playwright"),
        *("--output", str(tmp_path / "output")),
        *extra,
    ]


def _run_catalog(monkeypatch, argv: list[str]) -> None:
    from harness.trajectory_replay import catalog_runner

    monkeypatch.setattr("sys.argv", argv)
    prior = os.umask(0o022)
    try:
        catalog_runner.main()
    finally:
        os.umask(prior)


def _catalog_snapshot(tmp_path: Path, events: list, stdout: str) -> dict:
    normal = _Normal(tmp_path)
    output = tmp_path / "output"
    return normal(
        {
            "events": events,
            "progress": _jsonl(output / "progress.jsonl"),
            "stdout": [json.loads(line) for line in stdout.splitlines() if line.startswith("{")],
            "files": _files(output, normal),
        }
    )


def _local_catalog(tmp_path: Path, monkeypatch, capsys) -> dict:
    from harness.trajectory_replay import catalog_runner

    _catalog_inputs(tmp_path, monkeypatch, catalog_runner, [1, 2, 3, 4, 5, 6, 7])
    events: list = []
    browsers: list = []

    class Browser:
        def __init__(self, number: int) -> None:
            self.number = number
            self.returncode = None
            self.stopped = threading.Event()

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            events.append(["terminate", self.number])
            self.returncode = -15
            self.stopped.set()

        def wait(self, timeout=None):
            return self.returncode

    def start_local_browser(args, profile):
        browsers.append(Browser(len(browsers)))
        events.append(
            ["start", browsers[-1].number, args.engine, args.host_resolver_rules, bool(profile)]
        )
        return browsers[-1], f"http://127.0.0.1:{9000 + browsers[-1].number}"

    calls: dict[int, int] = {}

    def run_replay_task(**kwargs):
        task_id = kwargs["task_id"]
        calls[task_id] = calls.get(task_id, 0) + 1
        events.append(["task", task_id, calls[task_id], _kwargs(kwargs)])
        task_dir = kwargs["output"] / f"task-{task_id}"
        current = browsers[-1]
        if task_id == 2 and calls[task_id] == 1:
            current.returncode = 1
            raise ConnectionError("browser closed before session setup")
        if task_id == 3:
            return {"task_id": 3, "status": "fail", "runtime_poisoned": True, "error": "lost"}
        if task_id == 4:
            current.returncode = 1
            return {"task_id": 4, "status": "fail", "error": "browser exited"}
        if task_id == 5:
            task_dir.mkdir()
            raise RuntimeError("failure after evidence began")
        if task_id == 6:
            task_dir.mkdir()
            (task_dir / ".trace.partial.json").write_text(
                json.dumps(
                    [
                        {
                            "index": 0,
                            "action": "evaluate",
                            "arguments": {},
                            "status": "running",
                            "duration_ms": None,
                            "output": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            assert current.stopped.wait(10)
            return {"task_id": 6, "status": "fail", "error": "browser disconnected"}
        return {"task_id": task_id, "status": "pass", "score": 1.0, "error": ""}

    monkeypatch.setattr(catalog_runner, "start_local_browser", start_local_browser)
    monkeypatch.setattr(catalog_runner, "run_replay_task", run_replay_task)
    _run_catalog(
        monkeypatch,
        _catalog_argv(
            tmp_path,
            *("--binary", str(tmp_path / "browser")),
            *("--engine", "chromium"),
            *("--task-timeout-ms", "400"),
        ),
    )
    return _catalog_snapshot(tmp_path, events, capsys.readouterr().out)


def _hosted_catalog(tmp_path: Path, monkeypatch, capsys) -> dict:
    from harness.trajectory_replay import catalog_runner

    _catalog_inputs(tmp_path, monkeypatch, catalog_runner, [1, 2, 3, 4])
    endpoint = "wss://hosted.example.test/devtools?account=private-account"
    headers = {"Authorization": "Bearer private-token"}
    metadata = {"product": "Hosted/1.0"}
    monkeypatch.setattr(
        catalog_runner,
        "_hosted_cdp_config",
        lambda path, adapter: (endpoint, dict(headers), {"kind": "hosted", "engine": adapter.name}),
    )
    monkeypatch.setattr(catalog_runner, "_probe_hosted_runtime", lambda *args: dict(metadata))
    monkeypatch.setattr(catalog_runner, "load_remote_sites", lambda path: {})
    # Hosted tasks run in a forked worker, so the fake records calls in a file.
    log = tmp_path / "calls.jsonl"

    def run_replay_task(**kwargs):
        task_id = kwargs["task_id"]
        previous = sum(1 for row in (_jsonl(log) if log.exists() else []) if row[1] == task_id)
        with log.open("a", encoding="utf-8") as target:
            target.write(
                json.dumps(["task", task_id, previous + 1, _Normal(tmp_path)(_kwargs(kwargs))])
                + "\n"
            )
        task_dir = kwargs["output"] / f"task-{task_id}"
        if task_id == 2 and previous == 0:
            raise ConnectionError(f"could not reach {endpoint} with {headers['Authorization']}")
        if task_id == 3:
            task_dir.mkdir()
            (task_dir / ".trace.partial.json").write_text(
                json.dumps(
                    [
                        {
                            "index": 0,
                            "action": "evaluate",
                            "arguments": {"url": endpoint},
                            "status": "running",
                            "duration_ms": None,
                            "output": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            threading.Event().wait(30)
        return {
            "task_id": task_id,
            "status": "pass",
            "error": f"warning from {endpoint}" if task_id == 4 else "",
        }

    monkeypatch.setattr(catalog_runner, "run_replay_task", run_replay_task)
    _run_catalog(
        monkeypatch,
        _catalog_argv(tmp_path, *("--engine", "kitesurf"), *("--task-timeout-ms", "1500")),
    )
    snapshot = _catalog_snapshot(tmp_path, _jsonl(log), capsys.readouterr().out)
    snapshot["inputs"] = snapshot["files"].pop("inputs.json")
    return snapshot


# --- remote three-method cohort ------------------------------------------------------


def _cohort(tmp_path: Path, monkeypatch, capsys) -> dict:
    from harness.trajectory_replay import remote_execution
    from tests.trajectory_replay.test_remote_cohort_execution import environment

    args, activity = environment(tmp_path, monkeypatch)
    synthetic = remote_execution.run_replay_task
    events: list = []

    def run_replay_task(**kwargs):
        events.append(["task", kwargs["task_id"], _kwargs(kwargs)])
        return synthetic(**kwargs)

    monkeypatch.setattr(remote_execution, "run_replay_task", run_replay_task)
    previous = os.umask(0o022)
    try:
        code = remote_execution.run_cohort(args)
    finally:
        os.umask(previous)
    normal = _Normal(tmp_path)
    files = _files(args.output, normal)
    for name in ("reference_sha256_before", "reference_sha256_after"):
        files["receipt.json"][name] = {
            key: "<source>" if key in _SOURCE_BINDINGS else value
            for key, value in files["receipt.json"][name].items()
        }
    return normal(
        {
            "exit_code": code,
            "activity": activity,
            "events": events,
            "stdout": [json.loads(line) for line in capsys.readouterr().out.splitlines()],
            "files": files,
        }
    )


# --- local WebArena sites in Docker --------------------------------------------------


def _docker_suite(tmp_path: Path, monkeypatch, capsys) -> dict:
    from harness.trajectory_replay import webarena_replay as replay

    events: list = []
    engines = ["alpha", "beta"]
    seeds = {
        "tasks": [
            {
                "task_id": task_id,
                "replay_status": "ready",
                "canonical_candidate_id": f"candidate-{task_id}",
                "candidates": [{"id": f"candidate-{task_id}", "tool_calls": []}],
            }
            for task_id in (1, 2)
        ]
    }
    plan = {
        "seed_manifest": seeds,
        "task_ids": [1, 2],
        "engines": engines,
        "qualification_engine": "alpha",
        "methods": [{"engine": engine} for engine in engines],
        "profile": {
            "resources": {"cpus": 1.5, "memory": "1g", "sample_interval_ms": 100},
            "execution": {"reset_before_every_task": False, "retry_failed_task": True},
        },
        "profile_path": "profile.json",
        "profile_sha256": "c" * 64,
        "seed_manifest_path": "seeds.json",
        "seed_manifest_sha256": "d" * 64,
        "trusted_source_site_hosts": {"source.test"},
        "state_contracts": {},
        "retrieval_contracts": {2: {"schema": "retrieval/1"}},
        "retrieval_task_ids": [2],
    }
    cowagent = tmp_path / "cowagent"
    (cowagent / "agent/tools/browser").mkdir(parents=True)
    (cowagent / "agent/tools/browser/browser_tool.py").write_text("# fake\n", encoding="utf-8")

    class Container:
        def __init__(self, engine: str) -> None:
            self.id = f"container-{engine}"
            self.image = f"image-{engine}"
            self.engine = engine

        def reload(self) -> None:
            events.append(["container_reload", self.engine])

        def restart(self) -> None:
            events.append(["container_restart", self.engine])

    class Networks:
        def create(self, name):
            events.append(["network_create", name])
            return SimpleNamespace(name=name)

        def list(self, **kwargs):
            events.append(["network_list", kwargs])
            return []

    class Containers:
        def list(self, **kwargs):
            events.append(["container_list", kwargs])
            return []

    client = SimpleNamespace(
        info=lambda: {"ID": "daemon"},
        images=SimpleNamespace(get=lambda reference: f"image:{reference}"),
        networks=Networks(),
        containers=Containers(),
    )

    @contextmanager
    def lock(path):
        events.append(["lock", path.name])
        yield 1.0

    endpoints = iter(range(9000, 9100))

    def run_one(**kwargs):
        task_id = int(kwargs["task_seed"]["task_id"])
        events.append(
            ["run_one", kwargs["engine"], task_id, _kwargs({**kwargs, "task_seed": task_id})]
        )
        failed = kwargs["engine"] == "beta" and task_id == 2 and kwargs["physical_execution"] == 1
        status = "fail" if failed else "pass"
        return {
            "schema": "lex_webarena_trajectory_replay_attempt/1",
            "run_id": kwargs["run_id"],
            "engine": kwargs["engine"],
            "task_id": task_id,
            "physical_execution": kwargs["physical_execution"],
            "source_candidate_id": f"candidate-{task_id}",
            "process_status": status,
            "evaluator_status": status,
            "status": status,
            "duration_ms": 10.0,
            "evidence": {"trace": {"path": "trace.json"}},
        }

    stubs = {
        "load_replay_plan": lambda profile, seeds_path: plan,
        "require_site_credentials": lambda sites: None,
        "require_clean_git_checkout": lambda root: "cowagent-revision",
        "docker": SimpleNamespace(from_env=lambda: client),
        "docker_image_metadata": lambda image: {"image": str(image)},
        "site_container_manager": lambda site: SimpleNamespace(
            config=SimpleNamespace(docker_img=f"site-{site.value}"),
            is_running=lambda: False,
        ),
        "browser_session_contract": lambda: {"contract": "synthetic"},
        "docker_daemon_lock_namespace": lambda info: "daemon",
        "physical_execution_lock_path": lambda namespace: tmp_path / f"{namespace}.lock",
        "physical_execution_lock": lock,
        "prepare_sites": lambda sites: events.append(["prepare_sites", len(sites)]),
        "ENGINE_IMAGES": {"alpha": "alpha:1", "beta": "beta:1"},
        "engine_cdp_port": lambda engine: 9222,
        "start_browser_container": lambda client, **kwargs: (
            events.append(["start_container", kwargs]) or Container(kwargs["engine"])
        ),
        "published_endpoint": lambda container, **kwargs: f"http://127.0.0.1:{next(endpoints)}",
        "wait_cdp": lambda endpoint, container: events.append(["wait_cdp", endpoint])
        or endpoint.replace("http", "ws"),
        "configure_benchmark": lambda hostname: SimpleNamespace(hostname=hostname),
        "container_host_gateway": lambda container: "gateway.test",
        "cdp_metadata": lambda endpoint: {"endpoint": endpoint},
        "_run_one": run_one,
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(replay, name, stub)
    output = tmp_path / "suite"
    receipt = replay.run_replay_suite(
        profile_path=tmp_path / "profile.json",
        seed_manifest_path=tmp_path / "seeds.json",
        output_dir=output,
        cowagent_repo=cowagent,
        diagnostic_engines=engines,
    )
    normal = _Normal(tmp_path)
    assert normal(receipt) == normal(json.loads((output / "receipt.json").read_text()))
    return normal(
        {
            "events": events,
            "stdout": [json.loads(line) for line in capsys.readouterr().out.splitlines()],
            "files": _files(output, normal),
        }
    )


# --- one remote task with BrowserTool and the official evaluator ---------------------


class _Page:
    url = "https://shop.test/final"

    def __init__(self, events: list) -> None:
        self.events = events

    def locator(self, selector):
        return SimpleNamespace(inner_text=lambda: "final page text")

    def evaluate(self, script, selectors):
        return {selector: "value" for selector in selectors}

    def wait_for_timeout(self, milliseconds):
        self.events.append(["wait_for_timeout", milliseconds])

    def screenshot(self, path):
        self.events.append(["screenshot"])
        Path(path).write_bytes(b"png")


def _remote_task(tmp_path: Path, monkeypatch, case: str) -> dict:
    from harness.trajectory_replay import catalog_evaluators, remote_execution
    from tests.trajectory_replay.test_remote_execution_contracts import _retrieval_contract

    events: list = []
    page = _Page(events)
    service = SimpleNamespace(_page=page, _context="context", _submit=lambda callback: callback())
    observe_call = {"action": "get_text", "selector": "#answer"}
    results = {
        "pass": "Navigation completed",
        "retrieve": '{"value": "42"}',
        "insufficient": "not json",
        "step_failure": "Element not found",
        "evaluator_error": "Navigation completed",
        "correction": "Navigation completed",
    }

    class Tool:
        def __init__(self, config):
            events.append(["tool", sorted(config)])

        def _get_service(self):
            return service

        def execute(self, arguments):
            events.append(["execute", arguments])
            status = "error" if case == "step_failure" else "success"
            return SimpleNamespace(status=status, result=results[case])

        def close(self):
            events.append(["close"])

    module = ModuleType("agent.tools.browser.browser_tool")
    module.BrowserTool = Tool
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class Recorder:
        def __init__(self, **kwargs):
            events.append(["recorder", sorted(kwargs)])

        def start(self, context, page):
            events.append(["recorder_start"])

        def stop(self, context, path):
            events.append(["recorder_stop"])
            Path(path).write_text('{"log": {"entries": [1]}}', encoding="utf-8")

    class Result:
        def __init__(self, payload):
            self.payload = payload

        def model_dump(self, mode):
            return dict(self.payload)

    retrieve = case in {"retrieve", "insufficient"}

    class Benchmark:
        config = SimpleNamespace(render_url=lambda urls, sites: [f"rendered:{url}" for url in urls])

        def get_task(self, task_id):
            return SimpleNamespace(
                task_id=task_id,
                is_retrieve_task=retrieve,
                start_urls=["https://shop.test/"],
                sites=["shopping"],
                eval=[
                    SimpleNamespace(
                        evaluator="AgentResponseEvaluator",
                        expected=SimpleNamespace(
                            model_dump=lambda mode: {"task_type": "NAVIGATE"}
                        ),
                    )
                ],
            )

        def evaluate_task(self, *, task_id, agent_response, network_trace):
            events.append(["evaluate_task", task_id, agent_response, Path(network_trace).name])
            if case == "evaluator_error":
                raise RuntimeError("official evaluator failed")
            return Result({"task_id": task_id, "status": "success", "score": 1.0})

    correction = SimpleNamespace(
        replay_assertions=[{"kind": "page_text", "fields": {"#qty": "2"}}],
        correction_id="correction-7",
        corrected_task_sha256="e" * 64,
    )

    def evaluate_corrected_contract(benchmark, contract, *, agent_response, network_trace):
        events.append(["evaluate_corrected", contract.correction_id, agent_response])
        return Result({"task_id": 7, "status": "success", "score": 1.0})

    def check_replay_assertions(contract, *, page_url, page_text, field_values):
        events.append(["check_replay_assertions", page_url, page_text, field_values])
        return ({"kind": "page_text", "status": "fail", "detail": "quantity is 1"},)

    monkeypatch.setattr(remote_execution, "prepare_browser_viewport", lambda svc: {"w": 1})
    monkeypatch.setattr(
        remote_execution,
        "_clear_browser_state",
        lambda svc, origins: events.append(["clear", sorted(origins)]),
    )
    monkeypatch.setattr(
        remote_execution,
        "login_context",
        lambda svc, sites, benchmark, urls, viewport: events.append(["login", sites, urls]),
    )
    monkeypatch.setattr(remote_execution, "CdpHarRecorder", Recorder)
    monkeypatch.setattr(
        remote_execution,
        "_capture_failure_ui_state",
        lambda svc, arguments: {"selector": arguments.get("selector")},
    )
    monkeypatch.setattr(
        remote_execution, "apply_correction", lambda official, catalog: correction
    )
    monkeypatch.setattr(
        catalog_evaluators, "evaluate_corrected_contract", evaluate_corrected_contract
    )
    monkeypatch.setattr(remote_execution, "check_replay_assertions", check_replay_assertions)
    calls = [observe_call]
    contract = _retrieval_contract(7, calls)
    monkeypatch.setattr(
        remote_execution,
        "_read_retrieval_contract",
        lambda task_id, row, sites, path=None: {
            "task_id": task_id,
            "tool_calls": calls,
            "retrieval_contract": contract,
        },
    )
    output = tmp_path / "remote"
    output.mkdir()
    outcome = remote_execution.run_replay_task(
        task_id=7,
        row={"task_id": 7, "actions": calls, "readiness": []},
        benchmark=Benchmark(),
        endpoint="http://127.0.0.1:9222",
        output=output,
        run_id="run-7",
        origins={"https://shop.test"},
        sites={},
        catalog=SimpleNamespace(records={7: {}} if case == "correction" else {}),
    )
    normal = _Normal(tmp_path)
    task_dir = output / "task-7"
    evidence = {
        name: "matches file" if file_sha256(task_dir / filename) == digest else "differs"
        for name, digest in outcome.pop("evidence_sha256").items()
        for filename in [
            {
                "network_har": "network.har",
                "acceptance": "acceptance.json",
                "actual_response": "actual_response.json",
                "evaluation": "evaluation.json",
                "failure_screenshot": "failure.png",
                "trace": "trace.json",
                "readiness": "readiness.json",
            }[name]
        ]
    }
    return normal(
        {
            "events": events,
            "outcome": outcome,
            "evidence_files": evidence,
            "files": _files(task_dir, normal),
        }
    )


_REMOTE_TASK_CASES = (
    "pass",
    "retrieve",
    "insufficient",
    "step_failure",
    "evaluator_error",
    "correction",
)


def test_trajectory_replay_execution_loops(
    tmp_path: Path, monkeypatch, capsys, sequential_uuids
) -> None:
    scenarios = {}
    for name, scenario in (
        ("catalog_local", _local_catalog),
        ("catalog_hosted", _hosted_catalog),
        ("cohort", _cohort),
        ("docker_sites", _docker_suite),
    ):
        root = tmp_path / name
        root.mkdir()
        with monkeypatch.context() as patch:
            scenarios[name] = scenario(root, patch, capsys)
    for case in _REMOTE_TASK_CASES:
        root = tmp_path / f"remote-{case}"
        root.mkdir()
        with monkeypatch.context() as patch:
            scenarios[f"remote_task_{case}"] = _remote_task(root, patch, case)
    check_baseline("trajectory-replay-execution", scenarios)
