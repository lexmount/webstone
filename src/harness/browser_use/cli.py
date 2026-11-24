"""Build, check, review, package and publish Browser Use reports."""
from __future__ import annotations

from collections.abc import Sequence

from harness.adapters import STAGES as FIXED_STAGES
from harness.adapters import TrackStages, owner_stage, stage_main
from harness.results.export import export_stage

PROG = "python -m harness.browser_use"
COMMANDS = {
    "run": ("harness.browser_use.runner", "main",
            "Run a model with the selected isolated Browser Use task contract"),
    "report": ("harness.browser_use.report", "main",
               "Build a report from measured evidence"),
    "check": ("harness.browser_use.report_check", "main",
              "Check a built report against its profile and evidence"),
    "visual": ("harness.browser_use.report_visual", "main",
               "Run the visual gate and write its receipt"),
    "review": ("harness.browser_use.review", "main",
               "Write the deterministic per-attempt result review"),
    "package": ("harness.browser_use.report_package", "main",
                "Package a checked report for its Yours site"),
    "publish": ("harness.browser_use.report_publish", "cli",
                "Publish a packaged report to its bound Yours site"),
}


STAGES = TrackStages(
    export=export_stage("harness.browser_use.export"),
    **{name: owner_stage(*owner) for name, owner in COMMANDS.items() if name in FIXED_STAGES},
    extra={name: owner_stage(*owner) for name, owner in COMMANDS.items()
           if name not in FIXED_STAGES},
)


def main(argv: Sequence[str] | None = None, *, prog: str = PROG) -> None:
    # Owners read sys.argv: visual receipts hash report_visual.py, so its main cannot change.
    stage_main(STAGES, prog, __doc__, argv)
