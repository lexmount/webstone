"""Build, check, package and reproduce the Lexbench comparison report."""
from __future__ import annotations

from collections.abc import Sequence

from harness.adapters import STAGES as FIXED_STAGES
from harness.adapters import TrackStages, owner_stage, stage_main

PROG = "python -m harness.lexbench"
COMMANDS = {
    "report": ("harness.lexbench.report", "Build a report from retained evidence"),
    "check": ("harness.lexbench.verification", "Validate a built report"),
    "package": ("harness.lexbench.pack", "Package a validated report"),
    "reproduce": ("harness.lexbench.reproduction", "Reproduce one frozen fixed-layout run"),
    "resource-profiles": ("harness.lexbench.resource_profiles",
                          "Execute the frozen Linux resource profile cohort"),
    "resource-matrix": ("harness.lexbench.resource_matrix",
                        "Execute the frozen resource matrix"),
    "resource-summary": ("harness.lexbench.resource_summary",
                         "Summarize completed Linux resource profiles"),
    "chart": ("harness.lexbench.charts", "Render one resource comparison chart"),
}


STAGES = TrackStages(
    **{name: owner_stage(module, "main", summary) for name, (module, summary) in COMMANDS.items()
       if name in FIXED_STAGES},
    extra={name: owner_stage(module, "main", summary) for name, (module, summary) in COMMANDS.items()
           if name not in FIXED_STAGES},
)


def main(argv: Sequence[str] | None = None, *, prog: str = PROG) -> None:
    # Owners take their usage line from sys.argv. The resource protocol and the public
    # bundle freeze the bytes of some of them, so they cannot name the command themselves.
    stage_main(STAGES, prog, __doc__, argv)
