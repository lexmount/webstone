#!/usr/bin/env python3
"""Create or atomically update the browser-use report on Yours."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.record_io import file_sha256
from harness.reporting.publication import PublicationClient
from harness.reporting.publish import BoundTrack, publish
from harness.reporting.visual import tree_sha256 as _tree_sha256
from harness.safety import reject_evidence_symlinks

ARCHIVE = Path("publish/browser-use-sites.zip")
ATTESTATION = Path("publish/browser-use-sites.attestation.json")
RECEIPT = Path("publish/browser-use-artifact-site-receipt.json")
PROFILE: Path | None = None
OPERATION_JOURNAL = Path("publish/browser-use-artifact-site-operation-pending.json")
BINDING_JOURNAL = Path("publish/browser-use-artifact-site-binding-pending.json")
PUBLIC_REPORT_MARKER = "<title>首页 · Browser Use 评测</title>"
CLIENT = PublicationClient("Browser-Use-Report-Publisher/1.0", return_http_errors=True)
PUBLIC_CLIENT = PublicationClient("Browser-Use-Report-Publisher/1.0", timeout=60)


def tree_sha256(root: Path) -> str:
    return _tree_sha256(root, framed_paths=True)


def validate_package() -> None:
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated Browser Use package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    if attestation.get("schema") != "lex_browser_use_publication_attestation/2":
        raise ValueError("publication attestation is not the current multi-evidence schema")
    report = Path(str(attestation.get("report") or ""))
    evidence_paths = [Path(str(path)) for path in attestation.get("evidence") or []]
    expected_evidence = attestation.get("evidence_files") or {}
    required = {"manifest.json", "run-config.json", "attempts.jsonl", "result-review.json"}
    for path in evidence_paths:
        reject_evidence_symlinks(path)
    checks = {
        "archive": file_sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": file_sha256(PROFILE) == attestation.get("profile_sha256"),
        "evidence": bool(evidence_paths)
        and set(expected_evidence) == {str(path) for path in evidence_paths}
        and all(
            path.is_dir()
            and set(expected_evidence[str(path)]) == required
            and all(
                (path / name).is_file() and file_sha256(path / name) == digest
                for name, digest in expected_evidence[str(path)].items()
            )
            for path in evidence_paths
        ),
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError(
            "publication package is stale; rebuild before publishing: " + ", ".join(failed)
        )


def validate_public_preview(status: int, body: str) -> None:
    """Verify the stable report shell instead of a revision-specific headline."""

    if status != 200 or PUBLIC_REPORT_MARKER not in body:
        raise RuntimeError("public preview is unavailable or missing the report shell")


def track() -> BoundTrack:
    return BoundTrack(
        label="Browser Use",
        default_title="Browser Use 评测",
        archive=ARCHIVE,
        attestation=ATTESTATION,
        profile=PROFILE,
        receipt=RECEIPT,
        operation_journal=OPERATION_JOURNAL,
        binding_journal=BINDING_JOURNAL,
        inputs=(ATTESTATION, PROFILE),
        operation_prefix="browser-use-",
        operation_schema="lex_browser_use_artifact_operation/1",
        binding_schema="lex_browser_use_publication_binding/1",
        version_field="versionId",
        client=CLIENT,
        public_client=PUBLIC_CLIENT,
        upload_boundary="----browseruse",
        upload_filename="browser-use.zip",
        validate_package=validate_package,
        check_preview=lambda status, body: validate_public_preview(
            status, body.decode("utf-8", errors="replace")
        ),
        receipt_required=True,
        update_title=True,
    )


def main(*, create: bool = False, transport=None) -> None:
    publish(track(), "create" if create else "update", transport=transport)


def cli() -> None:
    global ARCHIVE, ATTESTATION, PROFILE, RECEIPT, BINDING_JOURNAL, OPERATION_JOURNAL
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create",
        action="store_true",
        help="explicitly create a new owned site when the bound site cannot be updated",
    )
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--binding-journal", type=Path, default=BINDING_JOURNAL)
    parser.add_argument("--operation-journal", type=Path, default=OPERATION_JOURNAL)
    args = parser.parse_args()
    OPERATION_JOURNAL = args.operation_journal
    BINDING_JOURNAL = args.binding_journal
    ARCHIVE, ATTESTATION, PROFILE, RECEIPT = (
        args.archive,
        args.attestation,
        args.profile,
        args.receipt,
    )
    main(create=args.create)
