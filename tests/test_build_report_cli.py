import runpy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_report.py"


@pytest.mark.parametrize("arguments,message", [
    (["--variant", "local-audit"], "requires: --evidence, --output"),
    (["--variant", "local-audit", "--evidence", "/tmp/evidence", "--output", "/tmp/report",
      "--profile", "{profile}"], "The profile selects report variant certified, not local-audit"),
    *[(["--variant", "saved-matrix", "--input", "/tmp/spec.json", "--output", "/tmp/report", *options],
       "does not accept")
      for options in (["--evidence", "/tmp/evidence"], ["--wcxb-source", "/tmp/source"],
                      ["--presentation-binding", "/tmp/binding.json"], ["--include-english"])],
    (["--variant", "saved-matrix", "--input", "/tmp/spec.json", "--output", "/tmp/report",
      "--language", "en"], "Chinese output only"),
    (["--output", "/tmp/report"], "Select an external report instance with --profile"),
    (["--variant", "unregistered"], "invalid choice"),
])
def test_build_report_rejects_conflicting_modes_before_work(tmp_path, monkeypatch, capsys, arguments, message):
    monkeypatch.chdir(ROOT)
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    arguments = [str(profile) if item == "{profile}" else item for item in arguments]
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == 2
    assert message in capsys.readouterr().err
