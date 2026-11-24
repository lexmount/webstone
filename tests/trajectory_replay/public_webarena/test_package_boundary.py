"""Keep the curated tool microbenchmark separate from WebArena replay."""

from __future__ import annotations

import json
import sys

import pytest

from harness.trajectory_replay import cli as curated_cli
from harness.trajectory_replay.public_webarena import cli as public_cli
from harness.trajectory_replay.report_inputs import replay_input_path


def test_suites_have_separate_task_contracts() -> None:
    curated = json.loads(replay_input_path(current="curated").read_text(encoding="utf-8"))
    webarena = json.loads(
        replay_input_path(current="human-candidates").read_text(encoding="utf-8")
    )
    assert len(curated["tasks"]) == 30
    assert all("tool_calls" in task for task in curated["tasks"])
    assert all("trajectory" not in task for task in curated["tasks"])
    assert len(webarena["tasks"]) == 179
    assert all("trajectory" in task for task in webarena["tasks"])


def test_public_web_arena_options_exist_only_on_public_entrypoint(monkeypatch, capsys) -> None:
    def help_text(entry) -> str:
        monkeypatch.setattr(sys, "argv", ["prog", "--help"])
        with pytest.raises(SystemExit):
            entry()
        return capsys.readouterr().out

    curated_help = help_text(lambda: curated_cli.run_curated(sys.argv[1:]))
    trajectory_help = help_text(public_cli.main)
    assert "--webarena-config" not in curated_help
    assert "--qualification-run" not in curated_help
    assert "--webarena-config" in trajectory_help
    assert "--qualification-run" in trajectory_help
