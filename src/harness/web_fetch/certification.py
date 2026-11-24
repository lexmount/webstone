#!/usr/bin/env python3
"""Run one immutable benchmark cohort with mandatory pre/post reference checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from harness.distribution import repository_root
from harness.web_fetch import (
    benchmark_runner,
    browser_observations,
    evidence,
    reference_freeze,
    source_capture,
)
from harness.web_fetch.benchmarks import BENCHMARKS, FROZEN_INPUTS, LiveSuite
from harness.web_fetch.config import (
    add_run_arguments,
    check_run_arguments,
    resolve_provider_selection,
)
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    requires_browser_observation,
    requires_fresh_reference,
)
from harness.web_fetch.retrieval_hints import native_fetch_dataset, parse_retrieval_hints
from harness.web_fetch.risk_control import main as risk_control_main
from harness.web_fetch.webmainbench_correction import certify_case_correction as correction_main
from lexbench_webfetch.metrics import normalize_for_match
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
from lexbench_webfetch.versions import verify_release

ROOT = repository_root()


def population_selection(path: Path, suite: str, dataset: dict) -> tuple[list[str], dict, bytes]:
    """Bind one exported suite to an explicit, revision-bound population."""
    if path.is_symlink():
        raise ValueError("Population selection rejects symlinks")
    raw = path.read_bytes()
    spec = json.loads(raw)
    if not isinstance(spec, dict) or not isinstance(dataset, dict):
        raise ValueError("Population and exported dataset must be JSON objects")
    if not isinstance(dataset.get("comparison", {}), dict):
        raise ValueError("Exported dataset comparison must be an object")
    if spec.get("schema_version") != 1 or spec.get("kind") != "task_population_selection":
        raise ValueError("Unsupported population selection")
    from lexbench_webfetch.paths import safe_identifier

    selected = [(r["suite"], safe_identifier(r["id"])) for r in spec["cases"]]
    excluded = [(r["suite"], safe_identifier(r["id"])) for r in spec["excluded"]]
    if (len(set(selected)) != len(selected) or len(set(excluded)) != len(excluded)
            or set(selected) & set(excluded)
            or len(selected) != spec["task_count"]
            or dict(Counter(s for s, _ in selected)) != spec["suite_counts"]):
        raise ValueError("Population counts or identities do not match")
    if not suite or suite not in spec["suite_counts"]:
        raise ValueError("Choose a suite declared in the population")
    revision = dataset.get("definition_revision", {})
    bindings = spec["frozen_task_bindings"]
    if not isinstance(revision, dict) or not isinstance(bindings, dict):
        raise ValueError("Definition revision and frozen task bindings must be objects")
    suite_binding = bindings[suite]
    if not isinstance(suite_binding, dict):
        raise ValueError("Frozen suite binding must be an object")
    revision_sha = spec.get("revision_sha256", "")
    if (not isinstance(revision_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", revision_sha)
            or spec["id"] != spec["revision"]
            or revision.get("id") != spec["revision"]
            or revision.get("sha256") != revision_sha
            or revision.get("parent_tasks_sha256") != suite_binding["sha256"]):
        raise ValueError("Exported suite does not match the population's rules or frozen tasks")
    known = [safe_identifier(case["id"]) for case in dataset["cases"]]
    allowed = {cid for sid, cid in selected + excluded if sid == suite}
    if len(known) != len(set(known)) or set(known) != allowed:
        raise ValueError("Exported cases do not match the population's suite")
    from harness.record_io import canonical_json_sha256
    expected_cases = suite_binding.get("exported_cases_sha256")
    if (not isinstance(expected_cases, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_cases)
            or canonical_json_sha256(dataset["cases"]) != expected_cases):
        raise ValueError("Exported definitions do not match the population's bound task content")
    ids = [cid for sid, cid in selected if sid == suite]
    binding = {"path": "task-population.json", "sha256": hashlib.sha256(raw).hexdigest(),
               "id": spec["id"], "task_count": spec["task_count"], "suite": suite,
               "selected_case_ids": ids, "selected_case_count": len(ids),
               "definition_revision": revision.copy()}
    return ids, binding, raw


def browser_observation_cases(
    dataset: dict, captures: Path, selected_case_ids: list[str]
) -> list[str]:
    """Detect selected cases whose current independent capture needs a browser.

    Browser observations are required when the task contract declares a
    browser-DOM reference, or when the just-completed independent HTTP capture
    cannot supply the facts/reference file that the freezer consumes.
    Malformed or missing audit rows fail closed instead of being guessed at.
    """

    try:
        rows = json.loads((captures / "audit.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Independent capture audit is unavailable") from error
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Independent capture audit is malformed")
    by_id: dict[str, dict] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id in by_id:
            raise ValueError("Independent capture audit has missing or duplicate case IDs")
        by_id[case_id] = row

    cases = {case["id"]: case for case in dataset["cases"]}
    browser_cases = []
    for case_id in selected_case_ids:
        case = cases[case_id]
        if not requires_fresh_reference(case):
            continue
        row = by_id.get(case_id)
        if row is None:
            raise ValueError("Independent capture audit is missing case: " + case_id)
        reference = captures / case_id / "reference.txt"
        http_usable = row.get("status") == 200 and reference.is_file()
        if http_usable:
            try:
                normalized = normalize_for_match(reference.read_text(encoding="utf-8"))
            except OSError:
                http_usable = False
            else:
                http_usable = all(
                    any(normalize_for_match(value) in normalized for value in fact["alternatives"])
                    for fact in case.get("required_facts", [])
                )
        if requires_browser_observation(case) or not http_usable:
            browser_cases.append(case_id)
    return browser_cases


def validate_browser_observations(path: Path, required_case_ids: list[str]) -> None:
    """Reject partial or unsuccessful browser batches before lifecycle use."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Browser observation batch is unavailable") from error
    if not isinstance(rows, dict) or set(rows) != set(required_case_ids):
        raise ValueError("Browser observation batch does not match required case scope")
    failed = [
        case_id
        for case_id in required_case_ids
        if not isinstance(rows[case_id], dict)
        or rows[case_id].get("case_id") != case_id
        or rows[case_id].get("status") != "success"
        or rows[case_id].get("completion", {}).get("satisfied") is not True
    ]
    if failed:
        raise ValueError("Required browser observations failed: " + ", ".join(failed))


def run(stage: Callable[[list[str]], None], arguments: list[str]) -> None:
    """Execute a maintained stage with explicit arguments in the caller's environment."""
    stage(arguments)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["webmainbench-latency"]:
        from harness.web_fetch.webmainbench_latency_correction import certify

        certify(argv[1:])
        return
    if argv[:1] == ["risk-control"]:

        risk_control_main(argv[1:], certified=True)
        return
    if argv[:1] == ["webmainbench-correction"]:

        correction_main(argv[1:])
        return
    if argv[:1] == ["wcxb-latency"]:
        from harness.web_fetch.wcxb_latency import certify
        options = argv[1:]
        certify(options)
        return
    selector = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    selector.add_argument("--benchmark", choices=BENCHMARKS.names(), default=LiveSuite.name)
    selected, argv = selector.parse_known_args(argv)
    benchmark = BENCHMARKS.get(selected.benchmark)
    if benchmark.capabilities().has(FROZEN_INPUTS):
        benchmark.certify(argv)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=(LiveSuite.name,), default=LiveSuite.name,
                        help="Benchmark adapter; frozen-input benchmarks take their own certifier options")
    add_run_arguments(
        parser, "dataset", "output", "providers", "workers", "timeout", "max_failure_retries",
        "call_order", "env_file", "provider_config",
        dataset={"default": ROOT / "evaluation/dataset.json"},
        output={"required": True},
        providers={"required": True},
        workers={"help": "Bounded independent provider concurrency; paired latency needs 1"},
        provider_config={"help": "Instance-specific provider settings passed unchanged to the shared runner"},
    )
    parser.add_argument("--benchmark-release", type=Path,
                        help="Frozen benchmark edition; checked before any external calls")
    parser.add_argument("--work-root", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    add_run_arguments(selection, "case_ids", case_ids={"help": "Explicit legacy case selection"})
    selection.add_argument("--population", type=Path,
                           help="Revision-bound population JSON; selects all declared cases in one suite")
    parser.add_argument("--population-suite", help="Suite in --population, matching the exported dataset")
    parser.add_argument("--capture-workers", type=int, default=8)
    parser.add_argument("--browser-capture-attempts", type=int, default=2)
    parser.add_argument("--browser-binary")
    parser.add_argument("--reference-promotion", type=Path)
    parser.add_argument(
        "--retrieval-mode",
        choices=("declared", "native"),
        default="declared",
        help="Explicitly select ordinary native fetch instead of selector-guided dynamic execution",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "Retain an explicitly uncertified cohort when stable rules "
            "have no live source qualification"
        ),
    )
    args = parser.parse_args(argv)
    check_run_arguments(parser, args)
    try:
        args.providers = resolve_provider_selection(args.providers, args.provider_config)
    except ValueError as error:
        parser.error(str(error))
    if args.call_order == "case-alternating" and (args.workers != 1 or not args.provider_config):
        parser.error("Alternating case calls require --workers 1 and --provider-config")
    if args.browser_capture_attempts < 1:
        parser.error("--browser-capture-attempts must be at least 1")

    input_raw = read_dataset_bytes(args.dataset)
    dataset = json.loads(input_raw)
    population_raw = population_binding = None
    if args.population:
        try:
            case_ids, population_binding, population_raw = population_selection(
                args.population, args.population_suite, dataset)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error("Invalid population selection: " + str(error))
        population_binding["input_dataset_sha256"] = hashlib.sha256(input_raw).hexdigest()
        dataset.setdefault("comparison", {})["task_population"] = population_binding
    else:
        if args.population_suite:
            parser.error("--population-suite requires --population")
        case_ids = [value.strip() for value in args.case_ids.split(",") if value.strip()]
        if not case_ids or len(case_ids) != len(set(case_ids)):
            parser.error("--case-ids must contain nonempty unique IDs")
    known = {case["id"] for case in dataset["cases"]}
    if set(case_ids) - known:
        parser.error("Unknown case IDs: " + ", ".join(sorted(set(case_ids) - known)))
    unresolved = sorted(case["id"] for case in dataset["cases"]
                        if case["id"] in case_ids
                        and (case.get("rule_review") or {}).get("status") == "unresolved")
    if unresolved and not args.diagnostic:
        parser.error("Formal cohorts require resolved acceptance rules: " + ", ".join(unresolved))
    if args.output.exists() or args.work_root.exists():
        parser.error("Output and work root must both be new immutable paths")

    # Frozen answers are already evaluation inputs. Historical admission
    # evidence does not turn a version rerun into live source qualification.
    unqualified = []


    if args.retrieval_mode == "native":
        original_sha = hashlib.sha256(read_dataset_bytes(args.dataset)).hexdigest()
        dataset = native_fetch_dataset(dataset)
        dataset["comparison"]["input_dataset_sha256"] = original_sha
    if args.diagnostic:
        dataset.setdefault("comparison", {}).update(
            evidence_state="diagnostic",
            publication_state="diagnostic",
            unqualified_reference_cases=sorted(unqualified),
        )
    release_raw = None
    release_path = args.benchmark_release or args.dataset.parent / 'benchmark-release.json'
    if args.benchmark_release or release_path.exists():
        if release_path.is_symlink():
            parser.error('Benchmark release rejects symlinks')
        release_raw = release_path.read_bytes()
        verify_release(json.loads(release_raw), dataset, exact_dataset=False,
                       scoring_source_sha256=SCORING_SOURCE_SHA256)
    # Reject malformed execution contracts before doing any external captures.

    for case in dataset["cases"]:
        if case["id"] in case_ids and requires_fresh_reference(case):
            requires_browser_observation(case)
        parse_retrieval_hints(
            case.get("track", "web"), case.get("retrieval_hints"), identifier=case["id"]
        )
    source_ids = ",".join(case["id"] for case in dataset["cases"]
                          if case["id"] in case_ids and requires_fresh_reference(case))
    args.work_root.mkdir(parents=True)
    dataset.setdefault('comparison', {})['source_reference_policy'] = 'frozen_benchmark_inputs_with_explicit_live_exact_checks'
    args.dataset = args.work_root / "input-dataset.json"
    args.dataset.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    selected = ",".join(case_ids)
    pre_captures = args.work_root / "pre-captures"
    frozen = args.work_root / "frozen"
    pre_browser = args.work_root / "pre-browser.json"
    post_captures = args.work_root / "post-captures"
    post_browser = args.work_root / "post-browser.json"

    if source_ids:
        run(source_capture.main, [str(args.dataset), str(pre_captures),
            "--workers", str(args.capture_workers), "--only", source_ids])
    else:
        pre_captures.mkdir(parents=True)
        (pre_captures / 'audit.json').write_text('[]\n', encoding="utf-8")
    try:
        pre_browser_cases = browser_observation_cases(dataset, pre_captures, case_ids)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    if pre_browser_cases and not args.browser_binary:
        parser.error(
            "--browser-binary is required for cases needing independent browser "
            "observations after pre-capture: " + ", ".join(pre_browser_cases)
        )
    browser_args: list[str] = []
    if pre_browser_cases:
        run(
            browser_observations.main,
            [
                "--dataset",
                str(args.dataset),
                "--output",
                str(pre_browser),
                "--case-ids",
                ",".join(pre_browser_cases),
                "--browser-binary",
                args.browser_binary,
                "--attempts",
                str(args.browser_capture_attempts),
            ]
        )
        try:
            validate_browser_observations(pre_browser, pre_browser_cases)
        except ValueError as error:
            parser.error(str(error))
        browser_args = ["--browser-observations", str(pre_browser)]
    promotion_args = (
        ["--reference-promotion", str(args.reference_promotion)] if args.reference_promotion else []
    )
    run(
        reference_freeze.main,
        [
            "--captures",
            str(pre_captures),
            "--dataset",
            str(args.dataset),
            "--output",
            str(frozen),
            "--case-ids",
            selected,
            *browser_args,
            *promotion_args,
        ]
    )

    benchmark = [
        "--dataset",
        str(frozen / "dataset.json"),
        "--output",
        str(args.output),
        "--providers",
        args.providers,
        "--case-ids",
        selected,
        "--workers",
        str(args.workers),
        "--timeout",
        str(args.timeout),
        "--max-failure-retries",
        str(args.max_failure_retries),
        "--measure-only",
        "--call-order", args.call_order,
    ]
    if args.env_file:
        benchmark.extend(["--env-file", str(args.env_file)])
    if args.provider_config:
        benchmark.extend(["--provider-config", str(args.provider_config)])
    run(benchmark_runner.main, benchmark)
    if population_raw is not None:
        (args.output / "task-population.json").write_bytes(population_raw)
        execution_path = args.output / "execution.json"
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        execution["aggregate"]["task_population"] = population_binding
        execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if release_raw is not None:
        (args.output / 'benchmark-input-release.json').write_bytes(release_raw)
        execution_path = args.output / 'execution.json'
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        execution['aggregate']['benchmark_release'] = {
            'path': 'benchmark-input-release.json', 'sha256': hashlib.sha256(release_raw).hexdigest()}
        execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")

    # Re-capture HTTP evidence after every provider call has completed, then
    # choose browser fallbacks from that post-call evidence. Reusing the
    # pre-call browser inventory is unsafe: an endpoint can become unavailable
    # during a long cohort even though its frozen pre-capture was usable.
    if source_ids:
        run(source_capture.main, [str(args.output / "dataset.json"), str(post_captures),
            "--workers", str(args.capture_workers), "--only", source_ids])
    else:
        post_captures.mkdir(parents=True)
        (post_captures / 'audit.json').write_text('[]\n', encoding="utf-8")
    post_dataset = json.loads((args.output / "dataset.json").read_text(encoding="utf-8"))
    try:
        post_browser_cases = browser_observation_cases(post_dataset, post_captures, case_ids)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    if post_browser_cases and not args.browser_binary:
        parser.error(
            "--browser-binary is required for cases needing independent browser "
            "observations after post-capture: " + ", ".join(post_browser_cases)
        )
    post_browser_args: list[str] = []
    if post_browser_cases:
        run(
            browser_observations.main,
            [
                "--dataset",
                str(args.output / "dataset.json"),
                "--output",
                str(post_browser),
                "--case-ids",
                ",".join(post_browser_cases),
                "--browser-binary",
                args.browser_binary,
                "--attempts",
                str(args.browser_capture_attempts),
            ]
        )
        try:
            validate_browser_observations(post_browser, post_browser_cases)
        except ValueError as error:
            parser.error(str(error))
        post_browser_args = ["--browser-observations", str(post_browser)]
    if source_ids:
        run(reference_freeze.postcheck_main, ["--run-dir", str(args.output),
            "--captures", str(post_captures), "--resume", *post_browser_args])
    observations = post_captures / "observations.json"
    if not observations.exists():
        observations.parent.mkdir(parents=True, exist_ok=True)
        observations.write_text("[]\n", encoding="utf-8")
    run(
        evidence.finalize_main,
        [
            "--run-dir",
            str(args.output),
            "--observations",
            str(observations),
        ]
    )
    print(
        "Diagnostic benchmark cohort:" if args.diagnostic else "Certified benchmark cohort:",
        args.output,
    )
