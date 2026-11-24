"""Repair commands preserve their inputs and publish only complete pending proposals."""
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena import qualification as q

ROOT = Path(__file__).resolve().parents[3]
KINDS = list(q.REPAIR_KINDS.names())
BATCH = {name for name, kind in q.REPAIR_KINDS.items() if kind.batch}


def setup_command(tmp_path, kind):
    actions = [
        {"action": "fill", "target": {"by": "label", "value": "Title"}, "text": "Wrong"},
        {"action": "click", "target": {"by": "role", "role": "button", "name": "Submit"}},
    ]
    suite = {"tasks": [{"id": "task-1", "tags": ["retrieve"],
        "trajectory": {"actions": actions},
        "ground_truth": {"response_builder": {"kind": "null_after_verified_path",
                                               "task_type": "mutate"}},
        "source": {"verified_evaluators": [{"evaluator": "NetworkEventEvaluator",
                   "expected": {"post_data": {"title": "Exact title"}}}]}}]}
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    args = ["--suite", str(suite_path)]
    if kind == "action-repair":
        config = {"schema": "lex_browser_use_action_repair/1", "task_id": "task-1",
                  "reason": "Correct declared input", "edits": [{"start_index": 1,
                  "expected_actions": actions[:1], "replacement_actions": [
                      {**actions[0], "text": "Exact title"}]}]}
    elif kind == "observed-repair":
        config = {"task_id": "task-1", "reason": "Observe visible output",
                  "observation_actions": [{"action": "get_text", "selector": "#answer",
                                           "save_as": "answer"}],
                  "response_builder": {"kind": "observed_json", "task_type": "retrieve",
                                       "output": "answer"}}
    if kind in {"action-repair", "observed-repair"}:
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        args += ["--config", str(config_path)]
    elif kind in BATCH:
        args += ["--task-ids", "task-1"]
        if kind == "autowait-repairs":
            qualification = {"tasks": [{"task_id": "task-1", "disposition": "needs_trace_repair",
                "task_contract_sha256": q.task_contract_sha256(suite["tasks"][0]),
                "failed_step": {"index": 2, "action": "click", "error":
                    "click action done; waiting for scheduled navigations to finish"}}]}
            path = tmp_path / "qualification.json"
            path.write_text(json.dumps(qualification), encoding="utf-8")
            args += ["--qualification", str(path)]
    else:
        args += ["--task-id", "task-1", "--action-index", "2"]
        if kind == "capture-target":
            args += ["--output-name", "answer"]
        else:
            args += ["--target-json", json.dumps(actions[1]["target"]),
                     "--reason", "Replace independently located target"]
    output = tmp_path / "output"
    args += ["--output-dir" if kind in BATCH else "--output", str(output)]
    return suite, args, output


def run_command(kind, args):
    """Run the shared proposal entry point in-process; return the refusal, if any."""
    try:
        q.main_repair_proposal([kind, *args])
    except (ValueError, OSError, RuntimeError, SystemExit) as error:
        return str(error) or type(error).__name__
    return None


@pytest.mark.parametrize("kind", KINDS)
def test_real_command_creates_bound_pending_proposal_and_refuses_overwrite(tmp_path, kind):
    suite, args, output = setup_command(tmp_path, kind)
    original = (tmp_path / "suite.json").read_bytes()
    assert run_command(kind, args) is None
    path = output / "task-1.json" if kind in BATCH else output
    content = path.read_bytes()
    proposal = json.loads(content)
    assert proposal["input_sha256"]["suite"] == hashlib.sha256(original).hexdigest()
    probe = q.build_probe(suite, proposal)
    assert probe["benchmark"]["repair_probe"]["non_canonical"] is True
    assert probe["tasks"][0]["ground_truth"]["qualification"]["status"] == "pending"
    assert probe["tasks"][0]["source"] == suite["tasks"][0]["source"]
    changed = deepcopy(suite)
    changed["tasks"][0]["trajectory"]["actions"][0]["text"] = "Changed"
    with pytest.raises(ValueError, match="source .* changed"):
        q.build_probe(changed, proposal)
    changed = deepcopy(suite)
    evaluator = changed["tasks"][0]["source"]["verified_evaluators"][0]
    evaluator["expected"]["post_data"]["title"] = "New title"
    with pytest.raises(ValueError, match="source task contract changed"):
        q.build_probe(changed, proposal)
    assert "already exists" in run_command(kind, args)
    assert path.read_bytes() == content
    assert (tmp_path / "suite.json").read_bytes() == original


@pytest.mark.parametrize("kind", ["action-repair", "autowait-repairs"])
@pytest.mark.parametrize("boundary", ["input", "link", "parent_link"])
def test_commands_preserve_inputs_and_reject_linked_outputs(tmp_path, kind, boundary):
    _, args, output = setup_command(tmp_path, kind)
    source = tmp_path / "suite.json"
    original = source.read_bytes()
    if boundary == "input":
        args[-1] = str(source)
    elif boundary == "link":
        output.symlink_to(source)
    else:
        real = tmp_path / "real"
        real.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        args[-1] = str(linked / "output")
    assert run_command(kind, args) is not None
    assert source.read_bytes() == original
    if boundary == "parent_link":
        assert not list(real.iterdir())


@pytest.mark.parametrize(
    ("kind", "ids"),
    [("evaluator-fill-repair", "task-1,missing"), ("autowait-repairs", "task-1,../escape")],
)
def test_batch_failure_exposes_no_partial_directory(tmp_path, kind, ids):
    _, args, output = setup_command(tmp_path, kind)
    args[args.index("--task-ids") + 1] = ids
    assert run_command(kind, args) is not None
    assert not output.exists()
    assert not (tmp_path / "escape.json").exists()


def test_generation_rejects_inputs_changed_before_publication(tmp_path, monkeypatch):
    _, args, output = setup_command(tmp_path, "action-repair")
    original = q.propose_action_repair

    def change_input(suite, config):
        proposal = original(suite, config)
        (tmp_path / "config.json").write_text('{}', encoding="utf-8")
        return proposal

    monkeypatch.setattr(q, "propose_action_repair", change_input)
    with pytest.raises(ValueError, match="input changed"):
        q.main_repair_proposal(["action-repair", *args])
    assert not output.exists()


def test_script_entry_point_supports_the_standard_temporary_directory():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        _, args, output = setup_command(Path(directory), "action-repair")
        result = subprocess.run(
            [sys.executable, "-m", "harness.trajectory_replay", "qualification", "propose",
             "action-repair", *args],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert output.is_file()
