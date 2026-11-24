"""The one Browser Use entry point reaches every owner through its public boundary."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.browser_use.cli import COMMANDS, PROG, main
from harness.reporting.templates import resolve_template

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"


def run_module(*arguments, cwd):
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-m", "harness.browser_use", *arguments], cwd=cwd,
                          env={**os.environ, "PYTHONPATH": path}, text=True, capture_output=True)


def test_top_level_help_lists_every_command(tmp_path):
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} ")
    listed = " ".join(result.stdout.split())
    for name, (_, _, summary) in COMMANDS.items():
        assert name in listed and summary in listed


def test_command_help_comes_from_its_owner_under_the_public_name(capsys):
    for command, option in (
        ("report", "--include-english"),
        ("check", "--require-complete"),
        ("visual", "--receipt"),
        ("review", "--selection"),
        ("package", "--visual-receipt"),
        ("publish", "--binding-journal"),
    ):
        with pytest.raises(SystemExit) as stopped:
            main([command, "--help"])
        assert stopped.value.code == 0
        out = capsys.readouterr().out
        assert out.startswith(f"usage: {PROG} {command} ")
        assert option in out


def test_missing_unknown_or_rejected_arguments_exit_with_usage(tmp_path, capsys):
    for arguments in ([], ["unknown"], ["visual", str(tmp_path), "--not-an-option"]):
        with pytest.raises(SystemExit) as stopped:
            main(arguments)
        assert stopped.value.code == 2
        err = capsys.readouterr().err
        assert f"usage: {PROG} " in err
        assert arguments[-1:] != ["--not-an-option"] or "--not-an-option" in err


def test_positional_report_argument_reaches_the_owner(tmp_path):
    # A linked report is rejected only once the checker has received its positional path.
    report = tmp_path / "source"
    report.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(report, target_is_directory=True)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        main(["check", str(linked), "--evidence", str(tmp_path)])


def test_review_command_binds_the_selection_and_restores_process_arguments(tmp_path, monkeypatch):
    # A manifest bound to another selection stops the review before anything is written.
    (tmp_path / "attempts.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "manifest.json").write_text('{"selection_sha256": "other"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["caller"])
    monkeypatch.chdir(ROOT)
    with pytest.raises(ValueError, match="pre-registered selection"):
        main(["review", "--evidence", str(tmp_path)])
    assert sys.argv == ["caller"]
    assert not (tmp_path / "result-review.json").exists()


def test_template_registry_names_the_entry_point_and_public_commands():
    template = resolve_template("browser-use")
    assert template["entrypoint"] == "src/harness/browser_use/cli.py"
    for key, command in (("build", "report"), ("check", "check"), ("package", "package")):
        assert template[key].startswith(f"uv run --frozen python -m harness.browser_use {command} ")
