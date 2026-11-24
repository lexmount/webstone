from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from harness.adapters import TrackStages, owner_stage, run_stage
from harness.data_home import data_home
from harness.report_languages import (
    ReportLanguageError,
    add_language_argument,
    resolve_report_language,
)
from harness.results.export import export_stage
from harness.runtime_env import load_runtime_env
from harness.web_fetch.accounts import print_account_checklist
from harness.web_fetch.benchmarks import BENCHMARKS, FROZEN_INPUTS, LiveSuite
from harness.web_fetch.config import (
    add_run_arguments,
    load_dataset,
    load_provider_config,
    resolve_provider_selection,
    select_diagnostic_scope,
)
from harness.web_fetch.providers import build_provider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.runner import rescore_evaluation, run_evaluation, run_frozen_cohort
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.models import TASK_KINDS

EXAMPLES = Path(__file__).parent / "data"
# Web Fetch stages whose owner parses its own arguments, including --help.
OWNER_COMMANDS = {
    "headless-run": ("harness.web_fetch.headless_runner", "main",
                     "Run a browser Markdown cohort with shared evidence checks"),
    "headless-observations": ("harness.web_fetch.browser_observations", "main",
                              "Capture independent browser observations"),
    "headless-assemble": ("harness.web_fetch.assembly", "main",
                          "Assemble exact headless measurements without selecting retries by score"),
    "headless-check": ("harness.web_fetch.headless_runner", "main_evidence_check",
                       "Audit complete measured evidence, including a publication-blocked cohort"),
    "headless-resources": ("harness.web_fetch.resource_cohort", "main",
                           "Build a bound client resource comparison from a reviewed cohort"),
    "review-headless": ("harness.web_fetch.review_execution", "review_main",
                        "Run an opt-in independent result review"),
    "review-anomalies": ("harness.web_fetch.review_execution", "anomaly_main",
                         "Investigate result anomalies through an opt-in independent review"),
    "review-merge": ("harness.web_fetch.result_review", "merge_main",
                     "Merge supplemental independent result reviews"),
    "review-rebase": ("harness.web_fetch.result_review", "rebase_main",
                      "Rebind completed independent reviews after score-only corrections"),
    "content-rescore": ("harness.web_fetch.rescoring", "main",
                        "Rescore an evidence directory offline"),
    "scoring-revision": ("harness.web_fetch.scoring_revision", "main",
                         "Apply a scoring revision to an evidence directory"),
    "reference-revision": ("harness.web_fetch.reference_revision", "main",
                           "Apply an offline reference revision to an evidence directory"),
    "reference-finalize": ("harness.web_fetch.cli", "finalize_reference_main",
                           "Finalize run-local reference checks from independent captures"),
    "dynamic-append": ("harness.web_fetch.dynamic_cohort", "main_append",
                       "Append measured dynamic content to current evidence"),
    "dynamic-merge": ("harness.web_fetch.dynamic_cohort", "main_merge",
                      "Merge a measured dynamic supplement"),
    "native-dns": ("harness.web_fetch.cli", "native_dns_main",
                   "Prepare the process-local native DNS bridge outside Git"),
    "report-revision-check": ("harness.web_fetch.report_check", "revision_main",
                              "Check report presentation and adoption"),
    "diagnostic-check": ("harness.web_fetch.cli", "diagnostic_check_main",
                         "Check a diagnostic edition without declaring it certified"),
    "site-bundle-check": ("harness.web_fetch.publication_check", "main",
                          "Validate a Web Fetch publication bundle"),
    "site-package": ("harness.web_fetch.report_package", "main",
                     "Package a Web Fetch report for Yours"),
    "site-publish": ("harness.web_fetch.report_publish", "main",
                     "Publish a profile-bound Web Fetch package"),
    "site-verify": ("harness.web_fetch.publication_verify", "main",
                    "Verify published report metadata and optionally its bytes"),
    "import-env": ("harness.web_fetch.accounts", "main_import_credentials",
                   "Import declared hosted credentials into a .env file"),
    "wcxb-select": ("harness.web_fetch.wcxb_dataset", "main",
                    "Freeze a WCXB sample manifest"),
    "wcxb-latency": ("harness.web_fetch.wcxb_latency", "main",
                     "Collect or certify (certify) paired WCXB timing"),
    "wcxb-visual": ("harness.web_fetch.wcxb_report_check", "visual_main",
                    "Check WCXB report interactions, themes and viewports"),
    "wcxb-publish": ("harness.web_fetch.wcxb_publication", "main",
                     "Package or publish a checked WCXB report"),
    "webmainbench-select": ("harness.web_fetch.public_benchmark_sample", "main",
                            "Freeze the WebMainBench-545 sample manifest"),
    "webmainbench-review": ("harness.web_fetch.webmainbench_diagnostics", "sample_review_main",
                            "Audit a declared sample without the official scorer"),
    "webmainbench-assemble": ("harness.web_fetch.webmainbench_assembly", "sample_main",
                              "Assemble a declared sample from reviewed shards"),
    "webmainbench-check": ("harness.web_fetch.webmainbench_report_check", "sample_main",
                           "Check a frozen sample report"),
    "webmainbench-audit": ("harness.web_fetch.webmainbench_diagnostics", "suspicion_main",
                           "Flag frozen evidence anomalies without changing scores"),
    "webmainbench-case-correction": ("harness.web_fetch.webmainbench_case_correction", "main",
                                     "Apply one-case replacement evidence to a new directory"),
    "webmainbench-case-rescore": ("harness.web_fetch.webmainbench_correction", "case_scoring_main",
                                  "Rescore a certified case correction offline"),
    "webmainbench-scoring-correction": ("harness.web_fetch.webmainbench_correction",
                                        "scoring_revision_main",
                                        "Apply a decision-authority manifest to the full population"),
    "webmainbench-teds-correction": ("harness.web_fetch.webmainbench_diagnostics", "correct_teds",
                                     "Apply an offline TEDS-only revision"),
    "webmainbench-teds-review": ("harness.web_fetch.webmainbench_diagnostics", "teds_review_main",
                                 "Verify the scope and unchanged evidence of a TEDS revision"),
    "webmainbench-latency-correction": ("harness.web_fetch.webmainbench_latency_correction", "main",
                                        "Apply a certified latency correction"),
    "webmainbench-content-review": ("harness.web_fetch.review_execution", "content_review_main",
                                    "Prepare or resume a source-grounded content review"),
    "webmainbench-full-assemble": ("harness.web_fetch.webmainbench_assembly", "main",
                                   "Verify and package the baseline and variant cohorts"),
    "webmainbench-full-check": ("harness.web_fetch.webmainbench_report_check", "main",
                                "Check a complete report"),
}

# Fixed stage verbs served by an owner command above. The owner names stay: bound report
# templates and guides name them.
STAGE_OWNERS = {"check": "report-revision-check", "package": "site-package", "publish": "site-publish"}
EXPORT = export_stage("harness.web_fetch.export")


def default_run_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return data_home() / "runs" / stamp


def add_run_stage_arguments(run: argparse.ArgumentParser) -> None:
    """``webstone run``: the shared run options plus benchmark selection."""
    add_language_argument(run)
    run.add_argument("--benchmark", choices=BENCHMARKS.names(), default=LiveSuite.name,
                     help="Benchmark adapter that selects, serves and scores the cases (default: %(default)s)")
    add_run_arguments(
        run, "dataset", "provider_config", "providers", "case_ids", "profile", "runs", "timeout",
        "workers", "env_file", "output", "no_report", "max_failure_retries",
        dataset={"help": "Dataset file; defaults to the selected benchmark's locked input"},
        provider_config={"help": "Provider configuration; defaults to the selected benchmark's configuration"},
        providers={"default": "fixture"},
        case_ids={"default": ""},
        profile={"help": "External run profile of a frozen-input benchmark"},
        timeout={"default": None,
                 "help": "Per-call deadline in seconds; defaults to the selected benchmark's deadline"},
        workers={"default": 4},
        no_report={"help": "Save scored results and evidence without building local HTML"},
    )
    run.add_argument("--content-scope", choices=("main", "full"), help="Acceptance and report scope when scoring both scopes")
    run.add_argument("--score-scope", choices=("main", "full", "both"), help="Compute only one scope or both (default for annotated tasks)")
    run.add_argument("--case-ids-file", type=Path,
                     help="Frozen case selection manifest of a frozen-input benchmark")
    run.add_argument("--expected-version", help="Exact release version required for the measured method")
    run.add_argument("--expected-dataset-sha256",
                     help="Frozen source SHA-256; explicit dataset files retain the original-corpus default")
    run.add_argument("--official-scorer-commit",
                     help="Scorer commit recorded with the run; scoring is performed separately")
    run.add_argument("--preserve-original-origin", action="store_true",
                     help="Serve frozen bytes through a loopback proxy while retaining each source URL")
    run.add_argument("--limit", type=int, help="Measure only the first N selected frozen cases")
    run.add_argument("--task-kind", choices=TASK_KINDS,
                     help="Select one task kind; intersects with --case-ids")
    run.add_argument("--delay", type=float, default=0.5)
    run.add_argument("--fail-on-failure", action="store_true")
    run.add_argument("--certified", action="store_true",
                     help="Run certified collection instead; the selected benchmark's certifier "
                          "parses the remaining options")


LIVE_ONLY = ("content_scope", "score_scope", "case_ids", "task_kind", "fail_on_failure")
FROZEN_ONLY = ("case_ids_file", "profile", "expected_version", "expected_dataset_sha256",
               "official_scorer_commit", "preserve_original_origin", "limit")


def command_run(args: argparse.Namespace) -> int:
    benchmark = BENCHMARKS.get(args.benchmark)
    frozen = benchmark.capabilities().has(FROZEN_INPUTS)
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be a positive number of cases")
    misplaced = [name for name in (LIVE_ONLY if frozen else FROZEN_ONLY) if getattr(args, name)]
    if misplaced or (frozen and args.runs != 1):
        names = ", ".join("--" + name.replace("_", "-") for name in misplaced or ["runs"])
        raise SystemExit(f"{names} does not apply to the {args.benchmark} benchmark")
    if args.provider_config is None:
        args.provider_config = benchmark.default_provider_config
    if args.timeout is None:
        args.timeout = benchmark.default_timeout
    if frozen:
        return command_run_frozen(benchmark, args)
    if args.dataset is None:
        args.dataset = benchmark.default_dataset
    return command_run_live(benchmark, args)


def run_in_process(argv: Sequence[str]) -> int:
    """Run the stage in-process for a certifying owner; returns the exit status."""
    parser = argparse.ArgumentParser(prog="webstone run")
    add_run_stage_arguments(parser)
    args = parser.parse_args(list(argv))
    if args.output is None:
        args.output = default_run_dir()
    return command_run(args)


def command_run_frozen(benchmark, args: argparse.Namespace) -> int:
    """Measure one method on frozen inputs; scoring and reports are later stages."""
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    if len(names) != 1:
        raise SystemExit("A frozen-input benchmark measures one method per run; pass one --providers name")
    args.provider = names[0]
    benchmark.resolve(args)
    run_frozen_cohort(benchmark, args)
    return 0


def command_run_live(benchmark, args: argparse.Namespace) -> int:
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("Output must be a new directory; existing evidence is immutable")
    load_runtime_env(args.env_file)
    language = None if args.no_report else resolve_report_language(args.output / "report", args.language)
    dataset_meta, cases = benchmark.cases({"dataset": args.dataset, "content_scope": args.content_scope,
                                           "scoring_scope": args.score_scope}).as_pair()
    provider_configs = load_provider_config(args.provider_config)
    selection = resolve_provider_selection(args.providers, args.provider_config)
    names = [name.strip() for name in selection.split(",") if name.strip()]
    unknown = [name for name in names if name not in provider_configs]
    if unknown:
        raise SystemExit(f"Unknown providers: {', '.join(unknown)}")
    try:
        dataset_meta, cases = select_diagnostic_scope(
            dataset_meta, cases, names, case_ids=args.case_ids, task_kind=args.task_kind)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    providers = [build_provider(name, provider_configs[name]) for name in names]
    records, _ = run_evaluation(
        output_dir=args.output,
        dataset_meta=dataset_meta,
        dataset_path=args.dataset,
        cases=cases,
        providers=providers,
        provider_config_path=args.provider_config,
        runs=args.runs,
        timeout=args.timeout,
        delay=args.delay,
        provider_concurrency=args.workers,
        max_failure_retries=args.max_failure_retries,
        benchmark=benchmark,
    )
    if args.no_report:
        print(f"Results: {(args.output / 'results.jsonl').resolve()}")
        print(f"Run data: {args.output.resolve()}")
        print("Report not built (--no-report).")
    else:
        report_path = build_report(args.output, args.output / 'report', diagnostic_preview=True,
                                   language=language,
                                   content_scope=(args.score_scope if args.score_scope in {'main', 'full'}
                                                  else args.content_scope))
        print(f"Report: {report_path.resolve()}")
    kinds={case.id:case.task_kind for case in cases}
    failures = [r for r in records if not task_passed(r['ok'],r['metrics'],kinds[r['case_id']])]
    return 2 if args.fail_on_failure and failures else 0


def command_report(args: argparse.Namespace) -> int:
    language = resolve_report_language(args.run_dir / 'report', args.language)
    report_path = build_report(args.run_dir, args.run_dir / 'report', diagnostic_preview=True,
                               language=language, content_scope=args.content_scope)
    print(f"Report: {report_path.resolve()}")
    return 0


def command_rescore(args: argparse.Namespace) -> int:
    if args.certified:
        raise SystemExit(f"The {args.benchmark} benchmark scores each run as it is recorded; "
                         "score has no separate certified mode")
    language = None if args.no_report else resolve_report_language(args.run_dir / "report", args.language)
    saved = json.loads((args.run_dir / 'dataset.json').read_text(encoding='utf-8'))
    manifest_path = args.run_dir/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
    acquisition = manifest.get('acquisition') or {}
    saved_scope = acquisition.get('content_scope', saved.get('content_scope'))
    task_scopes = acquisition.get('task_content_scopes') or {case['id']:case.get('content_scope') for case in saved.get('cases',[])}
    executed_scopes=set(task_scopes.values()) or {saved_scope}
    if args.content_scope is not None and executed_scopes != {args.content_scope}:
        raise ValueError('Rescore cannot change the saved content scope')
    scoring_scope = args.score_scope or saved.get('scoring_scope')
    if args.score_scope is None and args.content_scope is not None and scoring_scope in {'main', 'full'}:
        scoring_scope = args.content_scope
    dataset, cases = load_dataset(args.dataset, content_scope=(None if scoring_scope in {'main', 'full'} else saved_scope),case_scopes=task_scopes,
                                  scoring_scope=scoring_scope)
    if saved.get('content_scope') is None:
        dataset.pop('content_scope',None)
    records, _ = rescore_evaluation(run_dir=args.run_dir, dataset_meta=dataset, dataset_path=args.dataset, cases=cases)
    if args.no_report:
        print(f"Rescored {len(records)} persisted attempts; results: {(args.run_dir / 'results.jsonl').resolve()}")
        print("Report not rebuilt (--no-report). Any existing HTML is unchanged.")
    else:
        report_path = build_report(args.run_dir, args.run_dir / 'report', diagnostic_preview=True,
                                   language=language,
                                   content_scope=(args.score_scope if args.score_scope in {'main', 'full'}
                                                  else args.content_scope))
        print(f"Rescored {len(records)} persisted attempts; report: {report_path.resolve()}")
    return 0


def command_compose(args: argparse.Namespace) -> int:
    from harness.web_fetch.saved_matrix_compose import compose_saved_matrix

    try:
        path = compose_saved_matrix(args.run_dirs, args.output, title=args.title, by_suite=args.by_suite)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"Saved-matrix specification: {path}")
    print(f"Build: uv run --frozen python scripts/build_report.py --variant saved-matrix --input {path} --output NEW_REPORT")
    return 0


REVISE_HELP = """Rescore a saved run directory offline without provider calls.

The configuration is a JSON object:
  cases                       replacement case definitions (existing case IDs); required, may be []
  authority.description       why the revision is authorized; required
  regression_ids              scoring-regression control IDs proving the change; required
  rule_revision               true when any case acceptance contract changes
  excluded_cases              case IDs excluded symmetrically across every method
  rescore_attempt_ids         additional attempt IDs to rescore unchanged
  predecessor_revision        path/hash binding of the previous revision receipt
  input_verdicts              path/hash binding of frozen pre-revision verdicts
  native_answer_corrections   per-attempt native answer projections to replay
  native_context_corrections  per-attempt native context projections to replay
  superseded_controls         regression controls this revision supersedes

The receipt is printed as JSON; the revision is kept under
SOURCE/local-evidence/offline-revisions/."""


def command_revise(args: argparse.Namespace) -> int:
    from harness.web_fetch.local_revision import revise_local_snapshot

    try:
        receipt = revise_local_snapshot(args.source, args.config.resolve())
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    _, cases = load_dataset(args.dataset, content_scope=args.content_scope)
    configs = load_provider_config(args.provider_config)
    selection = resolve_provider_selection(args.providers, args.provider_config)
    names = [name.strip() for name in selection.split(",") if name.strip()]
    errors = []
    for name in names:
        if name not in configs:
            errors.append(f"{name}: not defined")
            continue
        provider = build_provider(name, configs[name])
        missing = provider.validate()
        state = "OK" if not missing else f"missing {', '.join(missing)}"
        print(f"{name}: type={provider.type} {state}")
        if missing:
            errors.append(f"{name}: {state}")
    print(f"dataset: {len(cases)} cases OK")
    return 1 if errors else 0


def command_accounts(args: argparse.Namespace) -> int:
    load_runtime_env(args.env_file)
    print_account_checklist()
    return 0


def finalize_reference_main() -> None:
    """Finalize run-local body reference checks from independent offline JSON captures.

    Observations are a list, or {"observations": [...]}. Each entry requires case_id,
    independent: true, content_text, timezone-aware checked_at, scope_identity copied
    exactly from manifest.reference_checks.cases[case_id], and completion containing
    satisfied: true, elapsed_ms and timeout_ms. No network or browser work is done.
    """
    from harness.web_fetch.reference_lifecycle import finalize_reference_checks

    parser = argparse.ArgumentParser(description=finalize_reference_main.__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args()
    result = finalize_reference_checks(args.run_dir, args.observations)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "verified" else 1)


def native_dns_main() -> None:
    """Prepare the process-local native DNS bridge outside Git."""
    from harness.web_fetch.network_dns import build_native_dns_library

    parser = argparse.ArgumentParser(description=native_dns_main.__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    path, manifest = build_native_dns_library(args.destination)
    print(f"{path} {manifest['library_sha256']}")


def diagnostic_check_main() -> None:
    """Check a WebFetch diagnostic edition without declaring it certified."""
    from harness.web_fetch.publication import verify_published_tree
    from harness.web_fetch.report_diagnostic import check_diagnostic_report

    parser = argparse.ArgumentParser(description=diagnostic_check_main.__doc__)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--output", dest="named_output", type=Path)
    parser.add_argument("--profile", type=Path, help="Independently planned cohort/case/version scope")
    parser.add_argument("--deployed-archive", type=Path, help="Exported deployed tree to compare byte-for-byte")
    args = parser.parse_args()
    if bool(args.output) == bool(args.named_output):
        parser.error("provide one output directory, positional or --output")
    output = args.output or args.named_output
    result = check_diagnostic_report(output, profile=args.profile)
    if args.deployed_archive:
        result["verified_files"] = verify_published_tree(output, args.deployed_archive)
    print(json.dumps(result))


def build_parser(root_commands: Mapping[str, str] | None = None,
                 prog: str = "webstone") -> argparse.ArgumentParser:
    """Parse Web Fetch stages; ``harness.cli`` passes its own commands so help lists them."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Web Fetch evaluations for Webstone. Run retrieval tasks and build reports from saved evidence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run providers and generate a multi-page report")
    add_run_stage_arguments(run)
    run.set_defaults(handler=command_run)

    report = sub.add_parser("report", help="Rebuild HTML from an existing run directory")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--content-scope", choices=("main", "full"), help="Report one saved score set (default: full; main-only runs retain main)")
    add_language_argument(report)
    report.set_defaults(handler=command_report)

    score = sub.add_parser(
        "score", help="Score saved outputs; a frozen-input benchmark's scorer parses the remaining options")
    score.add_argument("--benchmark", choices=BENCHMARKS.names(), default=LiveSuite.name,
                       help="Benchmark whose scorer reads the saved outputs (default: %(default)s)")
    score.add_argument("--certified", action="store_true",
                       help="Score a certified cohort with the benchmark's certifying scorer")
    score.add_argument("run_dir", type=Path)
    score.add_argument("--dataset", type=Path, required=True)
    score.add_argument("--content-scope", choices=("main", "full"))
    score.add_argument("--score-scope", choices=("main", "full", "both"))
    score.add_argument("--no-report", action="store_true",
                         help="Rescore saved data without rebuilding HTML; existing reports are unchanged")
    add_language_argument(score)
    score.set_defaults(handler=command_rescore)

    compose = sub.add_parser(
        "compose", help="Write a saved-matrix specification comparing several run directories")
    compose.add_argument("run_dirs", nargs="+", type=Path, metavar="RUN_DIR")
    compose.add_argument("--output", type=Path, required=True, help="New specification file")
    compose.add_argument("--title", help="Report title (default: dataset names)")
    compose.add_argument("--by-suite", action="store_true",
                         help="Compare runs of different frozen datasets as separate suites")
    compose.set_defaults(handler=command_compose)

    revise = sub.add_parser("revise", help="Rescore a saved run offline from a revision configuration",
                            description=REVISE_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    revise.add_argument("source", type=Path, help="Saved run directory to revise")
    revise.add_argument("--config", type=Path, required=True, help="Revision configuration JSON")
    revise.set_defaults(handler=command_revise)

    validate = sub.add_parser("validate", help="Validate dataset and provider credentials without network calls")
    validate.add_argument("--dataset", type=Path, default=EXAMPLES / "fixture-dataset.json")
    validate.add_argument("--content-scope", choices=("main", "full"))
    validate.add_argument("--provider-config", type=Path, default=EXAMPLES / "fixture-providers.json")
    validate.add_argument("--providers", default="fixture")
    validate.add_argument("--env-file", type=Path)
    validate.set_defaults(handler=command_validate)

    accounts = sub.add_parser(
        "accounts", help="Print registration links, key locations and a dotenv template"
    )
    accounts.add_argument("--env-file", type=Path)
    accounts.set_defaults(handler=command_accounts)
    summaries = {**(root_commands or {}),
                 **{name: OWNER_COMMANDS[owner][2] for name, owner in STAGE_OWNERS.items()},
                 "export": EXPORT.__doc__,
                 **{name: summary for name, (_, _, summary) in OWNER_COMMANDS.items()}}
    for name, summary in summaries.items():
        sub.add_parser(name, help=summary, add_help=False)
    return parser


def main(argv: Sequence[str] | None = None, *,
         root_commands: Mapping[str, str] | None = None, prog: str = "webstone") -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(root_commands, prog)
    if arguments[:1] == ["run"] and "--certified" in arguments[1:]:
        from harness.web_fetch.certification import main as certify

        certify([value for value in arguments[1:] if value != "--certified"])
        return
    if arguments[:1] == ["score"]:
        selector = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        selector.add_argument("--benchmark", choices=BENCHMARKS.names(), default=LiveSuite.name)
        selector.add_argument("--certified", action="store_true")
        selected, rest = selector.parse_known_args(arguments[1:])
        benchmark = BENCHMARKS.get(selected.benchmark)
        if benchmark.capabilities().has(FROZEN_INPUTS):
            invoked = sys.argv
            mode = " --certified" if selected.certified else ""
            sys.argv = [f"{parser.prog} score --benchmark {selected.benchmark}{mode}", *invoked[1:]]
            try:
                benchmark.score_stage(rest, certified=selected.certified)
            finally:
                sys.argv = invoked
            return
    if arguments[:1] and arguments[0] in {*OWNER_COMMANDS, *STAGE_OWNERS, "export"}:
        run_stage(STAGES, arguments[0], arguments[1:], parser.prog)
        return
    args = parser.parse_args(arguments)
    if getattr(args, "output", "sentinel") is None:
        args.output = default_run_dir()
    try:
        result = args.handler(args)
    except ReportLanguageError as error:
        parser.error(str(error))
    raise SystemExit(result)


def _parsed(name: str):
    """A stage this module's own parser defines."""
    def stage(argv: list[str]) -> None:
        main([name, *argv], prog=sys.argv[0].removesuffix(" " + name))

    return stage


def _owner(name: str):
    module, function, summary = OWNER_COMMANDS[name]
    return owner_stage(module, function, summary)


STAGES = TrackStages(
    run=_parsed("run"),
    score=_parsed("score"),
    report=_parsed("report"),
    **{stage: _owner(owner) for stage, owner in STAGE_OWNERS.items()},
    export=EXPORT,
    extra={**{name: _parsed(name) for name in ("validate", "accounts", "compose", "revise")},
           **{name: _owner(name) for name in OWNER_COMMANDS}},
)
