"""Certify a complete WCXB method matrix declared by its profile."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.distribution import repository_root
from harness.record_io import load_jsonl
from harness.web_fetch.benchmarks import certify_methods
from harness.web_fetch.cli import run_in_process
from harness.web_fetch.wcxb_dataset import load_wcxb_source, selected_wcxb_input, validate_manifest
from harness.web_fetch.wcxb_scoring import (
    metric_implementation_binding,
    paired_latency_methods,
    participant_names,
)

ROOT = repository_root()


def main(argv: list[str]) -> None:
    """Certify a complete WCXB matrix, reusing unchanged certified methods."""
    parser = argparse.ArgumentParser(description="Certify WCXB test cohort")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--sample-manifest", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=(ROOT / ".env"))
    parser.add_argument(
        "--reuse-certified-root",
        type=Path,
        help="Reuse unchanged, hash-bound methods from a prior complete cohort",
    )
    parser.add_argument(
        "--replace-providers",
        default="",
        help="Comma-separated methods to rerun from the prior certified cohort",
    )
    args = parser.parse_args(argv)

    args.dataset, args.sample_manifest = selected_wcxb_input(args.dataset, args.sample_manifest, profile=args.profile)

    if args.output_root.exists():
        parser.error("WCXB output root must be new and immutable")
    source_records, before = load_wcxb_source(args.dataset, manifest=args.sample_manifest)

    manifest = json.loads(args.sample_manifest.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    metric_hash = metric_implementation_binding(profile)
    providers = participant_names(profile)
    if (
        hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest()
        != profile["sample_manifest_sha256"]
        or manifest.get("scope") != "qualified_test"
        or manifest.get("size") != profile["case_count"]
    ):
        raise ValueError("WCXB certified run must use the current qualified-case manifest")
    selected, _ = validate_manifest(source_records, before, manifest)
    expected_ids = {row["track_id"] for row in selected}
    sample_before = hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest()
    replace = set(filter(None, args.replace_providers.split(",")))
    if replace and not args.reuse_certified_root:
        parser.error("--replace-providers requires --reuse-certified-root")
    if not replace <= set(providers):
        parser.error("Unknown WCXB replacement provider")
    old_runs = []
    if args.reuse_certified_root:
        prior = args.reuse_certified_root.resolve()
        if prior == args.output_root.resolve():
            raise ValueError("WCXB prior and new evidence roots must differ")
        old_certificate = json.loads((prior / "certification.json").read_text(encoding="utf-8"))
        old_runs = old_certificate.get("providers", [])
        old_names = [item["provider"] for item in old_runs]
        if (
            not old_certificate.get("complete")
            or not old_names
            or old_names != providers[: len(old_names)]
            or (len(old_names) >= len(providers) and not replace)
            or old_certificate["source_sha256_before"] != before
            or old_certificate["source_sha256_after"] != before
            or old_certificate["sample_sha256_before"] != sample_before
            or old_certificate["sample_sha256_after"] != sample_before
        ):
            raise ValueError("WCXB reusable cohort is incomplete or differs from frozen inputs")
        for item in old_runs:
            provider = item["provider"]
            old_dir = prior / provider
            if (
                old_dir.is_symlink()
                or hashlib.sha256((old_dir / "results.jsonl").read_bytes()).hexdigest()
                != item["results_sha256"]
            ):
                raise ValueError("WCXB prior result identity changed: " + provider)
            if any(path.is_symlink() for path in old_dir.rglob("*")):
                raise ValueError("WCXB prior evidence contains a symlink: " + provider)
            execution = json.loads((old_dir / "execution.json").read_text(encoding="utf-8"))
            rows = load_jsonl(old_dir / "results.jsonl")
            if (
                execution["run_id"] != item["run_id"]
                or execution["implementation_identity"] != item["identity"]
                or execution["case_count"] != len(selected)
                or execution["source_dataset_sha256"] != before
                or execution["selection_manifest"]["sha256"] != sample_before
                or len(rows) != len(selected)
                or {row["track_id"] for row in rows} != expected_ids
            ):
                raise ValueError("WCXB reusable provider is incomplete: " + provider)
    if replace - {item["provider"] for item in old_runs}:
        parser.error("Replacement method is absent from the prior certified cohort")
    args.output_root.mkdir(parents=True)
    reusable = {item["provider"]: (args.reuse_certified_root / item["provider"], item)
                for item in old_runs if item["provider"] not in replace}

    def unchanged() -> None:
        if metric_implementation_binding(profile) != metric_hash:
            raise ValueError('WCXB metric implementation changed during certification')
        _, current = load_wcxb_source(args.dataset, manifest=args.sample_manifest)
        if current != before or hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest() != sample_before:
            raise ValueError("WCXB references changed during provider calls")

    def measure(provider: str, output: Path) -> dict:
        run_in_process(
            [
                "--benchmark",
                "wcxb",
                "--profile",
                str(args.profile),
                "--dataset",
                str(args.dataset),
                "--providers",
                provider,
                "--output",
                str(output),
                "--env-file",
                str(args.env_file),
                "--case-ids-file",
                str(args.sample_manifest),
                "--timeout",
                str(profile["timeout_seconds"]),
                "--max-failure-retries",
                str(profile["max_failure_retries"]),
            ]
        )
        execution = json.loads((output / "execution.json").read_text(encoding="utf-8"))
        if (
            execution["case_count"] != len(selected)
            or execution["source_dataset_sha256"] != before
            or execution["selection_manifest"]["sha256"] != sample_before
        ):
            raise ValueError("Incomplete or drifted WCXB provider cohort: " + provider)
        rows = load_jsonl(output / "results.jsonl")
        if len(rows) != len(selected) or {row["track_id"] for row in rows} != expected_ids:
            raise ValueError("Incomplete WCXB result matrix: " + provider)
        return {
            "provider": provider,
            "run_id": execution["run_id"],
            "identity": execution["implementation_identity"],
            "results_sha256": hashlib.sha256(
                (output / "results.jsonl").read_bytes()
            ).hexdigest(),
        }

    def verify(provider: str, output: Path, receipt: dict) -> None:
        if hashlib.sha256((output / "results.jsonl").read_bytes()).hexdigest() != receipt["results_sha256"]:
            raise ValueError("WCXB measured results changed before certification: " + provider)

    runs = certify_methods(args.output_root, providers, unchanged=unchanged, measure=measure,
                           verify=verify, reused=reusable)
    # Each build the profile pins is measured by exactly one participant.
    paired_latency_methods(profile, {"providers": runs})
    # certify_methods ended with unchanged(): every input still matches its preflight hash.
    after, sample_after = before, sample_before
    receipt = {
        "schema_version": 1,
        "benchmark": manifest["dataset_id"],
        "metric_implementation_sha256": metric_hash,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "sample_sha256_before": sample_before,
        "sample_sha256_after": sample_after,
        "providers": runs,
        "complete": True,
        "reused_provider_count": len(reusable),
    }
    (args.output_root / "certification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Certified WCXB cohort:", args.output_root)
