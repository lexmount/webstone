"""The one Web Search entry point reaches every owner through its public boundary."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.reporting.templates import resolve_template
from harness.web_search.cli import COMMANDS, PROG, main
from harness.web_search.example import example

SOURCE = Path(__file__).resolve().parents[2] / "src"


def run_module(*arguments, cwd):
    # Checkout code wins over any installed distribution.
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-m", "harness.web_search", *arguments], cwd=cwd,
                          env={**os.environ, "PYTHONPATH": path}, text=True, capture_output=True)


def test_top_level_help_lists_every_command(tmp_path):
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} ")
    listed = " ".join(result.stdout.split())
    for name, (_, _, summary) in COMMANDS.items():
        assert name in listed and summary in listed


def run_main(capsys, *arguments):
    with pytest.raises(SystemExit) as stopped:
        main(list(arguments))
    captured = capsys.readouterr()
    return stopped.value.code or 0, captured.out, captured.err


@pytest.mark.parametrize("arguments, option", [
    (("report", "--help"), "--release-check"),
    (("visual", "--help"), "--receipt"),
    (("judge", "--help"), "--reuse-evidence"),
    (("review", "--help"), "{freeze,run,collect}"),
    (("publish", "--help"), "--visual-receipt"),
    (("review", "collect", "--help"), "--require-complete"),
])
def test_command_help_comes_from_its_owner_under_the_public_name(capsys, arguments, option):
    code, out, _ = run_main(capsys, *arguments)
    assert code == 0
    assert out.startswith(f"usage: {PROG} {' '.join(arguments[:-1])} ")
    assert option in out


@pytest.mark.parametrize("arguments, message", [
    ([], f"usage: {PROG} "),
    (["unknown"], f"usage: {PROG} "),
    (["report", "--output", "out", "--not-an-option"], "--not-an-option"),
])
def test_missing_unknown_or_rejected_arguments_fail_with_usage(capsys, arguments, message):
    code, _, err = run_main(capsys, *arguments)
    assert code == 2
    assert message in err


def test_report_command_checks_a_generated_report_and_restores_process_arguments(
    tmp_path, capsys, monkeypatch
):
    example(tmp_path)
    monkeypatch.setattr(sys, "argv", ["caller"])
    main(["report", "--output", str(tmp_path / "report"), "--check"])
    assert "Web Search report checks passed" in capsys.readouterr().out
    assert sys.argv == ["caller"]


def test_template_registry_names_the_entry_point_and_public_commands():
    template = resolve_template("web-search")
    assert template["entrypoint"] == "src/harness/web_search/cli.py"
    for key in ("build", "check", "release_check"):
        assert template[key].startswith("uv run --frozen python -m harness.web_search report ")
