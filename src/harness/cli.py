"""The ``webstone`` command and the certified runner, dispatched from one track registry.

``webstone <track> <stage>`` runs a stage the track registers in its ``STAGES``
(``harness.adapters.TrackStages``): the fixed verbs and the track's own extras. Web Fetch
is the default track, so its stages are also top-level commands. Each shared command is
one subcommand whose owner parses the remaining arguments. Owners are imported only
when selected.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from typing import NamedTuple

from harness.adapters import run_stage

PROG = "webstone"


class Track(NamedTuple):
    command: str  # root subcommand
    cli: str  # module with ``STAGES`` and ``main(argv, *, prog)``
    summary: str
    name: str  # reader-facing track name
    guide: str  # repository path of the track guide
    certified: tuple[str, str] | None = None  # certified collection (module, function) taking argv
    extra: str | None = None  # optional package extra the track needs
    experimental: bool = False


# Track name, as ``--track`` selects it -> entry points.
TRACKS = {
    "webfetch": Track("webfetch", "harness.web_fetch.cli",
                      "Run Web Fetch stages (also available as top-level commands)",
                      "Web Fetch", "src/harness/web_fetch/README.md",
                      ("harness.web_fetch.certification", "main")),
    "web-search": Track("web-search", "harness.web_search.cli", "Run Web Search stages",
                        "Web Search", "src/harness/web_search/README.md",
                        ("harness.web_search.runner", "certified_main")),
    "web-extract": Track("web-extract", "harness.web_extract.cli", "Run Web Extract stages",
                         "Web Extract", "examples/web_extract/README.md",
                         ("harness.web_extract.certification", "main"), "web-extract-eval"),
    "trajectory-replay": Track("trajectory-replay", "harness.trajectory_replay.cli",
                               "Run Browser Trajectory Replay stages",
                               "Browser Trajectory Replay", "examples/trajectory_replay/README.md",
                               extra="browser-use-eval"),
    "browser-use": Track("browser-use", "harness.browser_use.cli",
                         "Run experimental Browser Use stages",
                         "Browser Use", "examples/browser_use/README.md",
                         extra="browser-use-eval", experimental=True),
}
DEFAULT_TRACK = "webfetch"
# Commands that serve every track: name -> (module, function reading sys.argv, summary).
SHARED_COMMANDS = {
    "tracks": ("harness.agent_workflows", "tracks_main",
               "Discover tracks, installed capabilities and exact dataset selections as JSON"),
    "skills": ("harness.agent_workflows", "skills_main",
               "List or install the bundled Webstone agent skill"),
    "datasets": ("harness.agent_workflows", "datasets_main",
                 "List, fetch, propose or select dataset revisions through existing owners"),
    "results": ("harness.results.cli", "main",
                "Submit public benchmark results for review in webstone-site"),
}


def load(owner: tuple[str, str]) -> Callable:
    module, function = owner
    return getattr(importlib.import_module(module), function)


def root_commands() -> dict[str, tuple[str, str, str]]:
    tracks = {track.command: (track.cli, "main", track.summary) for track in TRACKS.values()}
    return {**SHARED_COMMANDS, **tracks}


def run_track(track: Track, arguments: list[str]) -> None:
    owner = importlib.import_module(track.cli)
    prog = f"{PROG} {track.command}"
    if arguments[:1] and arguments[0] in owner.STAGES.names():
        run_stage(owner.STAGES, arguments[0], arguments[1:], prog)
        return
    # Help and track-level options (such as Web Extract's --home) go to the track's parser.
    owner.main(arguments, prog=prog)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    tracks = {track.command: track for track in TRACKS.values()}
    if arguments[:1] and arguments[0] in tracks:
        run_track(tracks[arguments[0]], arguments[1:])
        return
    if arguments[:1] and arguments[0] in SHARED_COMMANDS:
        module, function, _ = SHARED_COMMANDS[arguments[0]]
        handler = load((module, function))
        invoked = sys.argv
        # Owners read sys.argv: several record their own source hash, so their mains stay unchanged.
        sys.argv = [f"{PROG} {arguments[0]}", *arguments[1:]]
        try:
            handler()
        finally:
            sys.argv = invoked
        return
    # The default track's parser owns top-level help, so it lists the root commands too.
    summaries = {name: summary for name, (_, _, summary) in root_commands().items()}
    load((TRACKS[DEFAULT_TRACK].cli, "main"))(arguments, root_commands=summaries)


def certified_main(argv: Sequence[str] | None = None):
    """Run certified collection through the selected track's maintained owner."""
    argv = list(sys.argv[1:] if argv is None else argv)
    certified = {name: track.certified for name, track in TRACKS.items() if track.certified}
    if argv[:1] and argv[0] in certified:
        if any(value.partition("=")[0] == "--track" for value in argv):
            raise ValueError("Select the certified track once")
        argv = ["--track", *argv]
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--track", choices=list(certified), default=DEFAULT_TRACK)
    args, remaining = parser.parse_known_args(argv)
    return load(certified[args.track])(remaining)


if __name__ == "__main__":
    main()
