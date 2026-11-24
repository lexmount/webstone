"""Build, check, review, judge and publish Web Search reports."""
from __future__ import annotations

from collections.abc import Sequence

from harness.adapters import STAGES as FIXED_STAGES
from harness.adapters import TrackStages, owner_stage, stage_main
from harness.results.export import export_stage

PROG = "python -m harness.web_search"
COMMANDS = {
    "run": ("harness.web_search.runner", "main", "Collect and score a profile-bound Web Search cohort"),
    "report": ("harness.web_search.report", "main",
               "Build, check, release-check or export a report"),
    "visual": ("harness.web_search.visual", "main",
               "Capture the reader evidence that a release check binds"),
    "judge": ("harness.web_search.relevance", "judge_main",
              "Run explicitly selected model relevance judgments on a frozen report"),
    "review": ("harness.web_search.review", "main",
               "Freeze, run and collect the independent result review"),
    "publish": ("harness.web_search.publication", "main",
                "Freeze and publish a checked report to its bound Yours site"),
}


def check(argv: list[str]) -> None:
    """Check a built report against its evidence"""
    from harness.web_search.report import main as report

    report(["--check", *argv])


def delivery(argv: list[str]) -> None:
    """Export a checked report into lossless case shards: delivery DESTINATION --output REPORT"""
    from harness.web_search.report import main as report

    report(argv if {"-h", "--help"} & set(argv) else ["--export-delivery", *argv])


STAGES = TrackStages(
    check=check,
    export=export_stage("harness.web_search.export", selects_benchmark=True),
    **{name: owner_stage(*owner, takes_argv=True) for name, owner in COMMANDS.items()
       if name in FIXED_STAGES},
    extra={name: owner_stage(*owner, takes_argv=True) for name, owner in COMMANDS.items()
           if name not in FIXED_STAGES} | {"delivery": delivery},
)


def main(argv: Sequence[str] | None = None, *, prog: str = PROG) -> None:
    stage_main(STAGES, prog, __doc__, argv)
