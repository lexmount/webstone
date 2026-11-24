"""User-facing agent entry points work without calls or replacing installed skills."""
import json
import sys
from pathlib import Path

import pytest

from harness.agent_workflows import install_skill
from harness.cli import main


def invoke(arguments, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["webstone"])
    main(arguments)
    return capsys.readouterr().out


def test_discovery_reads_selected_pins_without_fetching(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = json.loads(invoke(["tracks"], monkeypatch, capsys))
    assert {track["name"] for track in output["tracks"]} == {
        "Web Fetch", "Web Search", "Web Extract", "Browser Trajectory Replay", "Browser Use"}
    from importlib.resources import files

    lock = json.loads((files("harness.benchmarks") / "data-lock.json").read_text(encoding="utf-8"))
    for name, binding in output["datasets"].items():
        assert binding["revision"] == lock["datasets"][name]["revision"]
    assert json.loads(invoke(["datasets", "list"], monkeypatch, capsys)) == output["datasets"]
    assert not list(tmp_path.iterdir())


def test_bundled_skill_install_is_offline_and_never_overwrites(tmp_path):
    skill = install_skill(tmp_path / "agent-skills")
    assert skill.name == "SKILL.md"
    assert "name: webstone" in skill.read_text(encoding="utf-8")
    skill.write_text("user-maintained instructions", encoding="utf-8")
    with pytest.raises(FileExistsError):
        install_skill(tmp_path / "agent-skills")
    assert skill.read_text(encoding="utf-8") == "user-maintained instructions"
    other = tmp_path / "other"
    other.mkdir()
    (other / "webstone").symlink_to(skill.parent, target_is_directory=True)
    with pytest.raises(FileExistsError):
        install_skill(other)
    assert skill.read_text(encoding="utf-8") == "user-maintained instructions"


def test_cli_installs_the_same_skill_as_the_library(tmp_path, monkeypatch, capsys):
    data = json.loads(invoke(
        ["skills", "install", "--directory", str(tmp_path)], monkeypatch, capsys))
    assert Path(data["installed"]).read_bytes() == Path(json.loads(
        invoke(["skills", "list"], monkeypatch, capsys))["source"]).read_bytes()


@pytest.mark.parametrize("arguments, owner, expected", [
    (["datasets", "fetch", "websearch"], "data", ["websearch"]),
    (["datasets", "propose", "--checkout", "/chosen", "--revision", "a"],
     "publication", ["--checkout", "/chosen", "--revision", "a"]),
    (["datasets", "pin", "--root", "/chosen", "--track", "webfetch"],
     "pinning", ["--root", "/chosen", "--track", "webfetch"]),
])
def test_dataset_routes_preserve_owner_arguments(arguments, owner, expected, monkeypatch, capsys):
    import importlib

    module = importlib.import_module("harness.benchmarks." + owner)
    observed = []
    monkeypatch.setattr(module, "main", lambda: observed.extend(sys.argv[1:]))
    invoke(arguments, monkeypatch, capsys)
    assert observed == expected
    assert sys.argv == ["webstone"]


def test_pin_requires_explicit_checkout(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["webstone"])
    with pytest.raises(SystemExit) as stopped:
        main(["datasets", "pin", "--track", "webfetch", "--revision", "0" * 40])
    assert stopped.value.code == 2


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_dataset_pin_forwards_help_without_a_checkout(flag, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["webstone"])
    with pytest.raises(SystemExit) as stopped:
        main(["datasets", "pin", flag])
    assert stopped.value.code == 0
    assert "--select" in capsys.readouterr().out
    assert sys.argv == ["webstone"]


def track_stages():
    import importlib

    from harness.cli import TRACKS

    return [(track.command, stage) for track in TRACKS.values()
            for stage in importlib.import_module(track.cli).STAGES.supported()]


@pytest.mark.parametrize(("track", "stage"), track_stages())
def test_every_declared_stage_runs_as_webstone_track_stage(track, stage, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["caller"])
    with pytest.raises(SystemExit) as stopped:
        main([track, stage, "--help"])
    assert stopped.value.code == 0
    assert " ".join(capsys.readouterr().out.split()).startswith(f"usage: webstone {track} {stage} ")
    assert sys.argv == ["caller"]


def test_stage_reaches_its_registered_implementation(monkeypatch):
    from harness.adapters import TrackStages
    from harness.browser_use import cli

    calls = []
    monkeypatch.setattr(cli, "STAGES", TrackStages(
        package=lambda argv: calls.append(("package", sys.argv[0], argv)),
        extra={"visual": lambda argv: calls.append(("visual", sys.argv[0], argv))}))
    monkeypatch.setattr(sys, "argv", ["caller"])
    main(["browser-use", "package", "report", "--flag"])
    main(["browser-use", "visual"])
    assert calls == [("package", "webstone browser-use package", ["report", "--flag"]),
                     ("visual", "webstone browser-use visual", [])]
    assert sys.argv == ["caller"]
