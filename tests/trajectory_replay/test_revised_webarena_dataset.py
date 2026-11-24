from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_corrections import revise_dataset as revise

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
CORRECTIONS = ROOT / "evaluation/trajectory-replay/webarena-verified/task-corrections.json"
REVISED = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories-corrected.json"
SELECTION = ROOT / "evaluation/trajectory-replay/webarena-verified/selection.json"
FROZEN_CORRECTIONS = CORRECTIONS.parent / "dataset-67-225-243-corrections.json"


def test_revised_dataset_is_reproducible_bound_and_stable_across_catalog_updates() -> None:
    historical = revise(
        BASE,
        FROZEN_CORRECTIONS,
        [67, 225, 243],
        SELECTION,
        require_current_upstream=False,
    )
    assert historical == json.loads(REVISED.read_text(encoding="utf-8"))
    assert historical["selection_sha256"] == hashlib.sha256(SELECTION.read_bytes()).hexdigest()
    assert (
        historical["revision"]["base_dataset_sha256"]
        == hashlib.sha256(BASE.read_bytes()).hexdigest()
    )
    assert (
        historical["revision"]["correction_catalog_sha256"]
        == hashlib.sha256(FROZEN_CORRECTIONS.read_bytes()).hexdigest()
    )
    original = {row["task_id"]: row for row in json.loads(BASE.read_bytes())["trajectories"]}
    changed = {item["task_id"] for item in historical["revision"]["changed_tasks"]}
    assert changed == {67, 225, 243}
    for row in historical["trajectories"]:
        if row["task_id"] in changed:
            assert row["verification"]["process_status"] == "pending_requalification"
        else:
            assert row == original[row["task_id"]]

    current = revise(BASE, CORRECTIONS, [67, 225, 243], SELECTION)
    assert (
        current["revision"].pop("correction_catalog_sha256")
        == hashlib.sha256(CORRECTIONS.read_bytes()).hexdigest()
    )
    historical["revision"].pop("correction_catalog_sha256")
    assert current == historical


def test_revised_dataset_rejects_missing_or_repeated_scope() -> None:
    with pytest.raises(ValueError, match="nonempty and unique"):
        revise(BASE, CORRECTIONS, [])
    with pytest.raises(ValueError, match="nonempty and unique"):
        revise(BASE, CORRECTIONS, [225, 225])
    with pytest.raises(ValueError, match="revision task not in"):
        revise(BASE, CORRECTIONS, [999999])


def test_revision_uses_declared_population_instead_of_historical_size(tmp_path: Path) -> None:
    source = json.loads(BASE.read_bytes())
    source["trajectories"] = [row for row in source["trajectories"] if row["task_id"] == 225]
    source["counts"] = {"verified_tasks": 1, "verified_trajectories": 1}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    result = revise(path, CORRECTIONS, [225])
    assert result["counts"] == source["counts"]
    assert [row["task_id"] for row in result["trajectories"]] == [225]
    assert result["revision"]["changed_tasks"][0]["task_id"] == 225
    assert result["trajectories"][0]["verification"]["process_status"] == "pending_requalification"


@pytest.mark.parametrize("count", [2, True])
def test_revision_rejects_inconsistent_declared_population(tmp_path: Path, count) -> None:
    source = json.loads(BASE.read_bytes())
    source["trajectories"] = [row for row in source["trajectories"] if row["task_id"] == 225]
    source["counts"] = {"verified_tasks": count, "verified_trajectories": 1}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="population"):
        revise(path, CORRECTIONS, [225])


@pytest.mark.parametrize(
    "field", ["task_sha256", "candidate"]
)
def test_bound_revision_rejects_changed_authority(monkeypatch, field) -> None:
    from harness.trajectory_replay import webarena_corrections as owner

    bindings = owner._load_dataset_revision_bindings()
    if field in ("candidate", "reference"):
        bindings[67][field]["sha256"] = "0" * 64
    else:
        bindings[67][field] = "0" * 64
    monkeypatch.setattr(owner, "_load_dataset_revision_bindings", lambda path=None: bindings)
    with pytest.raises(ValueError, match="authority differs|artifact hash mismatch"):
        revise(BASE, CORRECTIONS, [67])


def test_explicit_revision_bindings_do_not_require_historical_replacements(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps({"schema": "webarena_dataset_revision_bindings/1", "revisions": []}),
        encoding="utf-8",
    )
    result = revise(BASE, CORRECTIONS, [225], revision_bindings_path=path)
    assert [row["task_id"] for row in result["revision"]["changed_tasks"]] == [225]


@pytest.mark.parametrize("damage", ["duplicate", "invalid-hash", "escape"])
def test_revision_bindings_reject_invalid_records(tmp_path: Path, damage) -> None:
    from harness.trajectory_replay.webarena_corrections import _load_dataset_revision_bindings

    rows = list(_load_dataset_revision_bindings().values())
    if damage == "duplicate":
        rows.append(rows[0])
    elif damage == "invalid-hash":
        rows[0]["task_sha256"] = "not-a-hash"
    else:
        rows[0]["candidate"]["path"] = "../outside.json"
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps({"schema": "webarena_dataset_revision_bindings/1", "revisions": rows}),
        encoding="utf-8"
    )
    with pytest.raises(ValueError):
        _load_dataset_revision_bindings(path)


def test_revision_command_preserves_sources_and_rejects_existing_output(tmp_path: Path) -> None:
    from harness.trajectory_replay import cli

    output = tmp_path / "revision.json"
    before = {path: path.read_bytes() for path in (BASE, CORRECTIONS)}
    command = [
        "dataset-revision", "--base", str(BASE), "--corrections", str(CORRECTIONS),
        "--task-id", "225", "--output", str(output),
    ]
    cli.main(command)
    assert json.loads(output.read_bytes()) == revise(BASE, CORRECTIONS, [225])
    saved = output.read_bytes()
    with pytest.raises(ValueError, match="output exists"):
        cli.main(command)
    assert output.read_bytes() == saved
    assert all(path.read_bytes() == raw for path, raw in before.items())
    cli.main(command + ["--replace-generated"])
    assert output.read_bytes() == saved


def test_revision_rejects_source_change_during_build(tmp_path: Path, monkeypatch) -> None:
    from harness.environments.webarena import runtime

    source = tmp_path / "source.json"
    source.write_bytes(BASE.read_bytes())
    benchmark = runtime.configure_benchmark()

    def changed_source():
        value = json.loads(source.read_bytes())
        value["unexpected_change"] = True
        source.write_text(json.dumps(value), encoding="utf-8")
        return benchmark

    monkeypatch.setattr(runtime, "configure_benchmark", changed_source)
    with pytest.raises(ValueError, match="source changed during build"):
        revise(source, CORRECTIONS, [225])


def test_offline_tools_need_no_site_accounts(tmp_path: Path, monkeypatch) -> None:
    from harness import env_file
    from harness.environments.webarena.runtime import configure_benchmark
    from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark

    monkeypatch.setattr(env_file, "_repository_credentials_file", tmp_path / "absent.env")

    assert revise(BASE, CORRECTIONS, [225])["revision"]["changed_tasks"]
    assert configure_benchmark().config.environments
    site_map = tmp_path / "sites.json"
    site_map.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_remote_sites/1",
                "sites": {
                    "gitlab": "https://gitlab.remote.test",
                    "map": "https://map.remote.test",
                    "reddit": "https://reddit.remote.test",
                    "shopping": "https://shopping.remote.test",
                    "shopping_admin": "https://admin.remote.test/admin",
                    "wikipedia": "https://wikipedia.remote.test",
                },
            }
        ),
        encoding="utf-8",
    )
    assert remote_benchmark(load_remote_sites(site_map)).config.environments
