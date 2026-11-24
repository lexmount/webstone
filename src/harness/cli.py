"""The ``webstone`` command and the certified runner, dispatched from one track registry.

Web Fetch is the default track, so its stages stay top-level commands. Every other
track and each shared command is one subcommand whose owner parses the remaining
arguments. Owners are imported only when selected.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from typing import NamedTuple

PROG = "webstone"


class Track(NamedTuple):
    command: str | None  # root subcommand; None for the default track, whose stages are top-level
    cli: tuple[str, str]  # (module, function) taking argv
    summary: str
    certified: tuple[str, str] | None = None  # certified collection (module, function) taking argv


# Track name, as ``--track`` selects it -> entry points.
TRACKS = {
    "webfetch": Track(None, ("harness.web_fetch.cli", "main"), "Run Web Fetch stages",
                      ("harness.web_fetch.certification", "main")),
    "web-search": Track("web-search", ("harness.web_search.cli", "main"), "Run Web Search stages",
                        ("harness.web_search.runner", "certified_main")),
    "web-extract": Track("web-extract", ("harness.web_extract.cli", "main"),
                         "Run Web Extract stages", ("harness.web_extract.certification", "main")),
    "trajectory-replay": Track("trajectory-replay", ("harness.trajectory_replay.cli", "main"),
                               "Run Browser Trajectory Replay stages"),
    "browser-use": Track("browser-use", ("harness.browser_use.cli", "main"),
                         "Run experimental Browser Use stages"),
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
    tracks = {track.command: (*track.cli, track.summary)
              for track in TRACKS.values() if track.command}
    return {**SHARED_COMMANDS, **tracks}


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = root_commands()
    if arguments[:1] and arguments[0] in commands:
        module, function, _ = commands[arguments[0]]
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
    summaries = {name: summary for name, (_, _, summary) in commands.items()}
    load(TRACKS[DEFAULT_TRACK].cli)(arguments, root_commands=summaries)


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
