"""Thin command-line entry for the shared results SDK."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.results.client import (
    DEFAULT_REPOSITORY,
    MAX_REPORT_BYTES,
    ResultsClient,
    SubmissionError,
)


def _read_report(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SubmissionError("Select a regular result JSON file, not a symbolic link.")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise SubmissionError("A result file must not exceed 1 MiB.")

    def reject_constant(value: str):
        raise SubmissionError("Non-finite JSON numbers are not supported.")

    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except RecursionError as error:
        raise SubmissionError("The JSON nesting exceeds the supported input depth.") from error
    except ValueError as error:
        raise SubmissionError("Select a valid UTF-8 JSON report with supported numbers.") from error


def _provider_urls(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        key, separator, url = value.partition("=")
        if not separator or not key or key in result:
            raise SubmissionError("Use each --provider-url ID=https://... only once.")
        result[key] = url
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webstone results",
        description=(
            "Submit public results for review in webstone-site. No website account is required."
        ),
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPOSITORY, help="GitHub website repository (OWNER/NAME)"
    )
    parser.add_argument("--website", help="Public website base URL for a custom repository")
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser(
        "template", help="Create an unfilled result template from the current catalog"
    )
    template.add_argument("--benchmark", required=True)
    template.add_argument("--definition")
    template.add_argument("--output", type=Path, required=True)
    for name, help_text in [
        ("validate", "Run local preflight; repository CI provides authoritative validation"),
        ("submit", "Create a reviewed result PR from your personal fork"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("report", type=Path)
        command.add_argument("--provider-url", action="append", default=[], metavar="ID=HTTPS_URL")
        if name == "submit":
            command.add_argument(
                "--dry-run",
                action="store_true",
                help="Show the proposed version and files without GitHub writes",
            )
    for name, help_text in [
        ("status", "Show PR state and review history"),
        (
            "rebase",
            "Resolve a version collision in your open result PR; approval is required again",
        ),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("number", type=int, help="Result pull request number")
        if name == "status":
            command.add_argument(
                "--verify-published",
                action="store_true",
                help="Compare live JSON with the merged result",
            )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        client = ResultsClient(repository=args.repo, website=args.website)
        if args.command == "template":
            result = client.template(args.benchmark, args.definition)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            result = {
                "file": str(args.output),
                "note": (
                    "Fill actual measurements and public evidence before submission. "
                    "No scores have been generated."
                ),
            }
        elif args.command in {"validate", "submit"}:
            report, urls = _read_report(args.report), _provider_urls(args.provider_url)
            result = (
                client.check(report, urls)
                if args.command == "validate"
                else client.submit(report, urls, dry_run=args.dry_run)
            )
        elif args.command == "rebase":
            result = client.rebase(args.number)
        else:
            result = client.status(args.number, verify_published=args.verify_published)
    except (SubmissionError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
