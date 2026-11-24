from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.trajectory_replay import cli, runner
from harness.trajectory_replay.core.suite import TaskSuite

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def wrapper_environment(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "calls"
    for name, body in {
        "docker": 'echo docker >> "$CALL_LOG"\nexit 0\n',
        "uv": (
            'if [ "$5" = python ] && [ "$6" = -c ]; then\n'
            '  shift 5; exec "$TEST_PYTHON" "$@"\n'
            'fi\n'
            'printf "%s\\n" "$@" >> "$CALL_LOG"\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = --output ]; then shift; mkdir "$1"; fi\n'
            '  shift\n'
            'done\nexit "${CALL_EXIT:-0}"\n'
        ),
    }.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "CALL_LOG": str(log),
        "TEST_PYTHON": sys.executable,
        "WEBSTONE_DATA_HOME": str(tmp_path / "data"),
        "TRAJECTORY_REPLAY_BUILD_IMAGES": "0",
    }
    env.pop("TRAJECTORY_REPLAY_OUTPUT_DIR", None)
    return env, log


def run_wrapper(env, *args, cwd=None):
    return subprocess.run(
        ["bash", str(ROOT / "examples/trajectory_replay/run.sh"), *args],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


def logged_calls(log):
    calls = log.read_text(encoding="utf-8").splitlines()
    log.unlink()
    return calls


@pytest.mark.parametrize("kind", ["directory", "dangling", "parent"])
def test_wrapper_preserves_existing_destination_before_external_work(
    tmp_path, wrapper_environment, kind
):
    env, log = wrapper_environment
    output = tmp_path / "evidence"
    original = tmp_path / "original"
    original.mkdir()
    artifact = original / "attempts.jsonl"
    artifact.write_bytes(b"original evidence\n")
    if kind == "directory":
        output.mkdir()
    else:
        output.symlink_to(original if kind == "parent" else tmp_path / "missing")
        if kind == "parent":
            output = output / "nested" / "new"
    env["TRAJECTORY_REPLAY_OUTPUT_DIR"] = str(output)
    assert run_wrapper(env).returncode != 0
    assert not log.exists()
    assert artifact.read_bytes() == b"original evidence\n"
    assert not (original / "nested").exists()
    assert (tmp_path / "evidence").is_symlink() is (kind != "directory")


def test_wrapper_selects_external_or_caller_relative_output_and_honours_no_report(
    tmp_path, wrapper_environment
):
    env, log = wrapper_environment
    result = run_wrapper(env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    calls = logged_calls(log)
    output = Path(calls[calls.index("--output") + 1])
    assert output.is_relative_to(Path(env["WEBSTONE_DATA_HOME"])) and output.is_dir()
    assert Path(calls[calls.index("--reports") + 1]) == output / "report"
    assert "--no-report" not in calls and "--artifacts" in calls

    env["TRAJECTORY_REPLAY_OUTPUT_DIR"] = "selected-run"
    result = run_wrapper(env, "--no-report", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    calls = logged_calls(log)
    assert calls[calls.index("--output") + 1] == str(tmp_path / "selected-run")
    assert (tmp_path / "selected-run").is_dir()
    assert calls.count("--no-report") == 1
    assert not (tmp_path / "selected-run" / "report").exists()
    assert "Latest report:" not in result.stdout


def test_failed_wrapper_run_preserves_a_previous_run(tmp_path, wrapper_environment):
    env, log = wrapper_environment
    previous = tmp_path / "data" / "trajectory-replay" / "runs" / "previous"
    previous.mkdir(parents=True)
    artifact = previous / "attempts.jsonl"
    artifact.write_bytes(b"completed run\n")
    env["CALL_EXIT"] = "23"
    result = run_wrapper(env)
    assert result.returncode == 23
    assert artifact.read_bytes() == b"completed run\n"
    assert "Evidence directory:" in result.stdout
    assert "--artifacts" not in log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("kind", ["directory", "dangling", "parent"])
def test_shared_runner_rejects_reused_or_linked_output_before_loading_inputs(
    tmp_path, monkeypatch, kind
):
    output = tmp_path / "evidence"
    original = tmp_path / "original"
    original.mkdir()
    artifact = original / "attempts.jsonl"
    artifact.write_bytes(b"immutable\n")
    if kind == "directory":
        output.mkdir()
    elif kind == "file":
        output.write_bytes(b"immutable\n")
    else:
        output.symlink_to(original if kind != "dangling" else tmp_path / "missing")
        if kind == "parent":
            output /= "new"

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid output must fail before input loading or browser setup")

    monkeypatch.setattr(runner, "load_suite", forbidden)
    monkeypatch.setattr(runner.docker, "from_env", forbidden)
    with pytest.raises(ValueError, match="[Oo]utput|symlink"):
        runner.run_evaluation(
            suite_path=tmp_path / "missing-suite.json", output_dir=output,
            reports_dir=tmp_path / "report",
        )
    assert artifact.read_bytes() == b"immutable\n"


@pytest.mark.parametrize("kind", ["occupied", "linked_parent"])
def test_runner_rechecks_destination_after_loading_inputs(tmp_path, monkeypatch, kind):
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "run"
    other = tmp_path / "other"
    other.mkdir()

    def load_suite(_):
        if kind == "occupied":
            output.mkdir()
            (output / "attempts.jsonl").write_bytes(b"competing run\n")
        else:
            parent.rmdir()
            parent.symlink_to(other)
        return TaskSuite("test", ())

    monkeypatch.setattr(runner, "load_suite", load_suite)
    monkeypatch.setattr(runner.docker, "from_env", lambda: pytest.fail("Browser setup reached"))
    with pytest.raises(ValueError, match="Output|symlink"):
        runner.run_evaluation(
            suite_path=tmp_path / "suite.json", output_dir=output,
            reports_dir=output / "report",
        )
    if kind == "occupied":
        assert (output / "attempts.jsonl").read_bytes() == b"competing run\n"
    else:
        assert not (other / "run").exists()


def test_direct_cli_requires_explicit_inputs_and_keeps_report_with_run(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "run_evaluation", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(SystemExit) as error:
        cli.main(["evaluate"])
    assert error.value.code == 2
    assert calls == []
    output = tmp_path / "run"
    cli.main([
        "evaluate", "--suite", "suite.json", "--ground-truth-evidence", "qualification.json",
        "--output", str(output),
    ])
    assert calls[0]["reports_dir"] == output / "report"
    assert calls[0]["suite_path"] == "suite.json"
    assert calls[0]["ground_truth_evidence_path"] == "qualification.json"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system aliases")
def test_wrapper_accepts_system_temp_alias(wrapper_environment):
    import tempfile

    env, log = wrapper_environment
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        root = Path(directory)
        env["TRAJECTORY_REPLAY_OUTPUT_DIR"] = str(Path("/tmp") / root.name / "new-run")
        result = run_wrapper(env)
        assert result.returncode == 0, result.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[calls.index("--output") + 1] == str(root / "new-run")
        assert (root / "new-run").is_dir()
