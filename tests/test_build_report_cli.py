import runpy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_report.py"


@pytest.mark.parametrize("arguments,message", [
    (["--local-audit"], "requires both --evidence and --output"),
    (["--local-audit", "--evidence", "/tmp/evidence", "--output", "/tmp/report",
      "--profile", "evaluation/current-report.json"], "cannot be combined with a release profile"),
    *[(["--saved-matrix", "/tmp/spec.json", "--output", "/tmp/report", *options],
       "cannot be combined with other build modes")
      for options in (["--local-audit"], ["--wcxb-source", "/tmp/source"])],
    *[(["--check-saved-matrix", "/tmp/report", *options], "cannot be combined with build options")
      for options in (["--output", "/tmp/another"], ["--presentation-binding", "/tmp/binding.json"],
                      ["--include-english"], ["--wcxb-sample", "/tmp/sample"])],
])
def test_build_report_rejects_conflicting_modes_before_work(monkeypatch, capsys, arguments, message):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == 2
    assert message in capsys.readouterr().err
