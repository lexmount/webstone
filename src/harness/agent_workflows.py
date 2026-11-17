"""Discovery and skill distribution; execution stays with each existing stage owner."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from pathlib import Path

from harness.distribution import bundled_data_path

SKILL = ".agents/skills/webstone/SKILL.md"
ROOT_URL = "https://github.com/lexmount/webstone/blob/main/"
# This registry describes routing, not scoring, task populations or dataset versions.
TRACKS = (
    ("web-fetch", "Web Fetch", "webstone", "src/harness/web_fetch/README.md", None),
    ("web-search", "Web Search", "webstone web-search", "src/harness/web_search/README.md", None),
    ("web-extract", "Web Extract", "webstone web-extract", 
     "examples/web_extract/README.md", "web-extract-eval"),
    ("trajectory-replay", "Browser Trajectory Replay", "webstone trajectory-replay",
     "examples/trajectory_replay/README.md", "browser-use-eval"),
    ("browser-use", "Browser Use", "webstone browser-use",
     "examples/browser_use/README.md", "browser-use-eval"),
)


def dataset_inventory() -> dict:
    from importlib.resources import files

    # Read the package's authoritative lock without downloading datasets or changing caches.
    lock = json.loads((files("harness.benchmarks") / "data-lock.json").read_text(encoding="utf-8"))
    return {name: {key: item[key] for key in ("repository", "revision", "datasets") if key in item}
            for name, item in lock["datasets"].items()}


def tracks_main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover Webstone workflows as JSON; no calls or writes")
    parser.parse_args()
    print(json.dumps({"tracks": [
        {"id": key, "name": name, "command": command, "guide": ROOT_URL + guide,
         "extra": extra, "status": "experimental" if key == "browser-use" else "available",
         "note": "Read the track guide for required external inputs and stage-specific options."}
        for key, name, command, guide, extra in TRACKS],
        "datasets": dataset_inventory(),
        "workflow_guide": ROOT_URL + "docs/agent-workflows.md",
        "submission": "https://github.com/lexmount/webstone-site/pulls",
        "limits": ["No universal run-all or automatic evidence-to-leaderboard conversion.",
                   "Sharing, result PRs, dataset PRs and software releases are separate actions."]},
        indent=2))


def install_skill(directory: Path) -> Path:
    """Install only the maintained bundled skill; never overwrite user instructions."""
    source = bundled_data_path(SKILL)
    parent = directory.expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / "webstone"
    # Exclusive directory creation rejects files, symlinks and an existing user's skill.
    destination.mkdir()
    try:
        shutil.copyfile(source, destination / "SKILL.md")
    except OSError:
        shutil.rmtree(destination)
        raise
    return destination / "SKILL.md"


def skills_main() -> None:
    parser = argparse.ArgumentParser(
        description="Install the packaged Webstone workflow skill offline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    install = commands.add_parser("install")
    install.add_argument("--directory", type=Path, required=True,
                         help="Your agent's skills root, for example ~/.codex/skills")
    args = parser.parse_args()
    try:
        result = ({"name": "webstone", "source": str(bundled_data_path(SKILL))}
                  if args.command == "list" else {"installed": str(install_skill(args.directory))})
    except OSError as error:
        parser.exit(2, f"Skill installation failed; inspect the existing destination: {error}\n")
    print(json.dumps(result, indent=2))


def datasets_main() -> None:
    parser = argparse.ArgumentParser(
        description="Use the shared dataset owners; proposals never merge PRs")
    owners = {"fetch": "data", "propose": "publication", "pin": "pinning"}
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for name in owners:
        commands.add_parser(name, add_help=False)
    args, remaining = parser.parse_known_args()
    if args.command == "list":
        if remaining:
            parser.error("unrecognized arguments: " + " ".join(remaining))
        print(json.dumps(dataset_inventory(), indent=2))
        return
    has_root = any(value.partition("=")[0] == "--root" for value in remaining)
    wants_help = bool({"-h", "--help"}.intersection(remaining))
    if args.command == "pin" and not has_root and not wants_help:
        parser.error("pin requires an explicit --root Webstone source checkout")
    module = importlib.import_module("harness.benchmarks." + owners[args.command])
    invoked = sys.argv
    sys.argv = [f"{parser.prog} {args.command}", *remaining]
    try:
        module.main()
    finally:
        sys.argv = invoked
