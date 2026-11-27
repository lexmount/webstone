from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from harness.record_io import file_sha256
from harness.reporting import publish as shared
from harness.reporting.publication import (
    PublicationOperation,
    public_share_context,
    response_json,
    write_receipt,
)
from harness.trajectory_replay import webarena_report_package as packager
from harness.trajectory_replay import webarena_report_publish as publisher


@pytest.fixture(autouse=True)
def private_publication_files(monkeypatch, tmp_path):
    for key in ("ATTESTATION", "PROFILE", "REVIEW"):
        path = tmp_path / (key.lower() + "-default.json")
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(publisher, key, path)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "binding-pending.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_public_share_must_be_same_origin_and_supported_share_route() -> None:
    base = "https://reports.example"
    assert public_share_context(base, base + "/v/token") == (
        "/v/token",
        "token",
    )
    assert public_share_context(base, base + "/s/slug") == ("/s/slug", "")
    assert public_share_context(base, base + "/s/slug") == ("/s/slug", "")
    for url in (
        "https://other.example/v/token",
        base + "/v/",
        base + "/v/token/extra",
        base + "/v/token%2Fextra",
        base + "/v/token?owner=true",
    ):
        try:
            public_share_context(base, url)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe public share URL must be rejected")


def test_preview_marker_accepts_each_localized_home_title() -> None:
    assert publisher.preview_has_report_marker(b"<title>Browser Trajectory Replay</title>")
    assert publisher.preview_has_report_marker("<title>浏览器轨迹重放</title>".encode())
    assert not publisher.preview_has_report_marker(b"<title>Unrelated site</title>")


def test_receipt_replace_interruption_preserves_previous_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    receipt = tmp_path / "receipt.json"
    original = b'{"slug":"site-1"}\n'
    receipt.write_bytes(original)

    def interrupt_replace(_source, _destination) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", interrupt_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        write_receipt(receipt, {"slug": "site-2"})

    assert receipt.read_bytes() == original


def _identity(archive: Path, *, slug: str = "", expected_version: str = "") -> dict:
    return shared.operation_identity(
        "lex_webarena_replay_artifact_operation/1",
        base="https://app.example",
        title="Replay",
        archive_sha256=file_sha256(archive),
        slug=slug,
        expected_version=expected_version,
    )


def test_running_operation_does_not_resubmit_upload(tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    operation_journal = tmp_path / "operation-pending.json"
    operations = PublicationOperation(operation_journal, "trajectory-replay-")
    identity = _identity(archive)
    operations.pending(identity, allow_create=True)
    requests = []

    def fake_request(path, *_args, **_kwargs):
        requests.append(path)
        return 200, b'{"status":"running"}'

    with pytest.raises(RuntimeError, match="still running"):
        operations.upload(
            "/api/sites", b"payload", "multipart/form-data; boundary=fixed", identity,
            allow_create=False, request=fake_request, decode=response_json,
        )

    assert requests == [
        f"/api/operations/{json.loads(operation_journal.read_text(encoding='utf-8'))['key']}"
    ]


def test_package_binds_report_evidence_and_release_gate(monkeypatch, tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    evidence = tmp_path / "effective-results.json"
    evidence.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    dataset = tmp_path / "dataset.json"
    review = tmp_path / "review.json"
    dataset.write_text("{}", encoding="utf-8")
    profile.write_text(
        json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}),
        encoding="utf-8",
    )
    review.write_text("{}", encoding="utf-8")
    (report / "report-manifest.json").write_text(json.dumps({
        "dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
    }), encoding="utf-8")
    output = tmp_path / "report.zip"
    attestation = tmp_path / "attestation.json"
    monkeypatch.setattr(packager, "PROFILE", profile)
    monkeypatch.setattr(packager, "REVIEW", review)
    monkeypatch.setattr(packager, "ATTESTATION", attestation)
    monkeypatch.setattr(
        packager,
        "validate_release",
        lambda _report, _receipt, **_kwargs: {"html_pages": 1, "checked_links": 0},
    )

    result = packager.package(report, evidence, output, tmp_path / "visual.json")

    assert result["archive_sha256"] == _sha256(output)
    assert result["evidence_sha256"] == _sha256(evidence)
    assert result["file_count"] == 2
    profile.write_text(json.dumps({
        "dataset": {"path": str(dataset), "sha256": _sha256(dataset)},
        "scoring": {"require_all_reference_validity": True},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="registered methods and the declared task set"):
        packager.package(report, evidence, output, tmp_path / "visual.json")


def test_package_reruns_private_preview_gate_for_old_receipt(monkeypatch, tmp_path: Path) -> None:
    old_schema = "lex_webarena_replay_visual_qa/3"
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("<html></html>", encoding="utf-8")
    receipt = tmp_path / "visual.json"
    receipt.write_text(json.dumps({
        "schema": old_schema,
        "report_tree_sha256": packager.tree_sha256(report),
        "states_checked": 48,
        "failures": [],
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(packager, "check_report", lambda _report, **_kwargs: {"html_pages": 1})
    monkeypatch.setattr(packager, "check_visual", lambda *_args: calls.append("private preview"))

    packager.validate_release(report, receipt)

    assert calls == ["private preview"]


def test_package_rejects_diagnostic_appendix_and_mixed_dataset(
    monkeypatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    dataset = tmp_path / "corrected.json"
    dataset.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}),
        encoding="utf-8",
    )
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(packager, "PROFILE", profile)
    monkeypatch.setattr(packager, "REVIEW", review)
    monkeypatch.setattr(packager, "ATTESTATION", tmp_path / "attestation.json")
    monkeypatch.setattr(packager, "validate_release", lambda *_args, **_kwargs: {})
    manifest = {"dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
                "diagnostic_closure_sha256": "a" * 64}
    (report / "report-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="diagnostic-only"):
        packager.package(report, evidence, tmp_path / "report.zip", tmp_path / "visual.json")
    manifest.pop("diagnostic_closure_sha256")
    manifest["dataset_sha256"] = "0" * 64
    (report / "report-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="different result or dataset"):
        packager.package(report, evidence, tmp_path / "report.zip", tmp_path / "visual.json")


def test_publication_rejects_diagnostic_appendix_and_stale_corrected_dataset(
    monkeypatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"package")
    dataset = tmp_path / "corrected.json"
    dataset.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}),
        encoding="utf-8",
    )
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "REVIEW", review)
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    (report / "report-manifest.json").write_text(json.dumps({
        "dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
    }), encoding="utf-8")
    attestation = {
        "schema": "lex_webarena_replay_publication_attestation/1",
        "archive_sha256": _sha256(archive),
        "report": str(report),
        "report_tree_sha256": publisher.tree_sha256(report),
        "profile_sha256": _sha256(profile),
        "dataset_sha256": _sha256(dataset),
        "review_sha256": _sha256(review),
        "evidence": str(evidence),
        "evidence_sha256": _sha256(evidence),
    }
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation_path)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "pending.json")
    assert publisher.validate_package()["dataset_sha256"] == _sha256(dataset)
    profile.write_text(json.dumps({
        "dataset": {"path": str(dataset), "sha256": _sha256(dataset)},
        "scoring": {"require_all_reference_validity": True},
    }), encoding="utf-8")
    attestation["profile_sha256"] = _sha256(profile)
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(ValueError, match="registered methods and the declared task set"):
        publisher.validate_package()
    profile.write_text(
        json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}),
        encoding="utf-8",
    )
    attestation["profile_sha256"] = _sha256(profile)
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    attestation["diagnostic_closure_sha256"] = "a" * 64
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(ValueError, match="no_diagnostic_closure"):
        publisher.validate_package()
    attestation.pop("diagnostic_closure_sha256")
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    dataset.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="dataset"):
        publisher.validate_package()
