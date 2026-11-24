from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena import release
from harness.trajectory_replay.public_webarena.publication import (
    build,
    check_report_bundle,
    stage_report,
)
from harness.trajectory_replay.public_webarena.report import METRICS
from tests.public_webarena_fixture import write_report_fixture


def test_stage_builds_report_without_mutating_source_evidence(tmp_path, monkeypatch, capsys):
    source, profile = write_report_fixture(tmp_path)
    before = (source / "run/summary.json").read_bytes()

    staged = tmp_path / "staged"
    stage_report(source, staged)

    assert (staged / "reports/webarena-trajectory-replay-latest.html").is_file()
    assert (staged / "reports/webarena-trajectory-replay-latest-results.html").is_file()
    method = staged / "reports/webarena-trajectory-replay-latest-method.html"
    assert method.is_file()
    assert "核验结论" in method.read_text(encoding="utf-8")
    assert check_report_bundle(staged) == {
        "pages": 6,
        "metrics": sum(len(metrics) for _, metrics in METRICS),
        "ratio_modules": 0,
    }
    assert (source / "run/summary.json").read_bytes() == before

    authority = json.loads(profile.read_text(encoding="utf-8"))
    archive, receipt = tmp_path / "report.zip", tmp_path / "attestation.json"
    result = build(staged, archive, receipt, authority=profile)
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("index.html") == bundle.read("webarena-trajectory-replay-latest.html")
    assert result["results"] == {
        engine: {"tasks": 1, "passes": 1} for engine in ("chromium", "moli")
    }
    monkeypatch.setattr(
        sys, "argv", ["release", "--profile", str(profile), "--formal-root", str(source)]
    )
    release.main(tmp_path)
    printed = capsys.readouterr().out
    assert f"`make public-webarena-report-audit PROFILE={profile}`" in printed
    assert f"`make public-webarena-report-release PROFILE={profile}`" in printed
    assert (tmp_path / "publish/webarena-fixed-trajectory.zip").is_file()
    assert json.loads(profile.read_text(encoding="utf-8")) == authority
    authority["suite_sha256"] = "wrong"
    profile.write_text(json.dumps(authority), encoding="utf-8")
    rejected = tmp_path / "rejected.zip"
    with pytest.raises(ValueError, match="current report authority mismatch: suite hash"):
        build(staged, rejected, receipt, authority=profile)
    assert not rejected.exists()

    homepage = staged / "reports/webarena-trajectory-replay-latest.html"
    broken = homepage.read_text(encoding="utf-8").replace(
        'data-preference="neutral">', 'data-preference="neutral"><b class="better">wrong</b>', 1
    )
    homepage.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="neutral metric must not declare a winner"):
        check_report_bundle(staged)


def test_profile_is_updated_only_with_verified_publication_values(tmp_path):
    profile = tmp_path / "current-report.json"
    profile.write_text(
        json.dumps({"publication": {"version_id": "old", "submitted_files_verified": 1}}),
        encoding="utf-8"
    )
    from harness.reporting.publication import bind_publication_files

    attestation = tmp_path / "attestation.json"
    attestation.write_text("{}", encoding="utf-8")
    bind_publication_files(
        profile,
        attestation,
        tmp_path / "binding.json",
        publication={"version_id": "ver_new", "submitted_files_verified": 71},
        schema="public_webarena_binding/1",
    )
    assert (
        json.loads(attestation.read_text(encoding="utf-8"))["profile_sha256"]
        == hashlib.sha256(profile.read_bytes()).hexdigest()
    )

    current = json.loads(profile.read_text(encoding="utf-8"))
    assert current["publication"] == {
        "version_id": "ver_new",
        "submitted_files_verified": 71,
    }


@pytest.fixture(scope="module")
def _built_publication(tmp_path_factory):
    from harness.trajectory_replay.public_webarena import publication

    root = tmp_path_factory.mktemp("publication")
    source, profile = write_report_fixture(root)
    document = json.loads(profile.read_text(encoding="utf-8"))
    document["publication"] = {
        "base_url": "https://reports.example",
        "site_slug": "site-one",
        "version_id": "ver_old",
        "share_url": "https://reports.example/v/token",
    }
    profile.write_text(json.dumps(document), encoding="utf-8")
    publication.stage_report(source, root / "staged")
    publication.build(root / "staged", root / "report.zip", root / "proof.json", authority=profile)
    (root / "receipt.json").write_text(
        json.dumps(
            {"slug": "site-one", "versionId": "ver_old", "base_url": "https://reports.example"}
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def publication_files(_built_publication, tmp_path):
    shutil.copytree(_built_publication, tmp_path, dirs_exist_ok=True)
    return (
        tmp_path / "source",
        tmp_path / "evaluation/browser-use-public/current-report.json",
        tmp_path / "staged",
        tmp_path / "report.zip",
        tmp_path / "proof.json",
        tmp_path / "receipt.json",
        tmp_path / "operation.json",
    )


@pytest.mark.parametrize("failure", ["upload-response", "share"])
def test_public_replay_submission_recovers_without_second_activation(publication_files, failure):
    from harness.trajectory_replay.public_webarena.publication import publish_current
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_files
    service = Service(failure)
    before = profile.read_bytes()
    with pytest.raises(OSError, match="simulated"):
        publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    result = publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    assert result["slug"] == "site-one" and result["version"] == "ver_new"
    assert len(service.uploads) == 1
    assert not journal.exists()
    assert "private-secret" not in receipt.read_text(encoding="utf-8")
    assert profile.read_bytes() == before


@pytest.mark.parametrize("saved_submission", [False, True])
def test_public_replay_publication_stops_before_requests_while_a_site_login_is_unset(
    publication_files, tmp_path, monkeypatch, saved_submission
):
    from harness import env_file
    from harness.trajectory_replay.public_webarena.publication import publish_current
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_files
    if saved_submission:
        receipt.write_text(
            json.dumps(
                {
                    **json.loads(receipt.read_bytes()),
                    "submission_state": "submitted_unverified",
                    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    before = receipt.read_bytes()
    monkeypatch.setattr(env_file, "_repository_credentials_file", tmp_path / "absent.env")
    service = Service("")

    with pytest.raises(ValueError, match="WEBARENA_"):
        publish_current(profile, staged, archive, proof, receipt, journal, transport=service)

    assert not service.requests
    assert receipt.read_bytes() == before
    assert not journal.exists()


@pytest.mark.parametrize("damage", ["site", "share", "proof", "package"])
def test_public_replay_preflight_rejects_wrong_authority_or_package_before_requests(
    publication_files, damage
):
    from harness.trajectory_replay.public_webarena.publication import publish_current
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_files
    if damage == "site":
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["slug"] = "other-site"
        receipt.write_text(json.dumps(document), encoding="utf-8")
    elif damage == "share":
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["share_url"] = "https://reports.example/v/other-report"
        receipt.write_text(json.dumps(document), encoding="utf-8")
    elif damage == "proof":
        document = json.loads(proof.read_text(encoding="utf-8"))
        document["profile_sha256"] = "0" * 64
        proof.write_text(json.dumps(document), encoding="utf-8")
    else:
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("undeclared.txt", "not part of the bound report")
        document = json.loads(proof.read_text(encoding="utf-8"))
        document["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        proof.write_text(json.dumps(document), encoding="utf-8")
    service = Service()
    with pytest.raises(ValueError):
        publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    assert service.requests == []


def test_release_retains_submission_when_export_verification_is_interrupted(
    publication_files, tmp_path, monkeypatch
):
    from harness.trajectory_replay.public_webarena import publication
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_files
    target = tmp_path / "publish/webarena-fixed-trajectory-site-receipt.json"
    target.parent.mkdir()
    target.write_bytes(receipt.read_bytes())
    service = Service()
    interrupted = [False]

    def command(args, root):
        if "--slug" in args:
            if not interrupted[0]:
                interrupted[0] = True
                raise RuntimeError("synthetic export verification interruption")
            return {
                "version": "ver_new",
                "files": json.loads(proof.read_text(encoding="utf-8"))["files"],
            }

        def get(name):
            return Path(args[args.index("--" + name) + 1])

        return publication.publish_current(
            get("profile"),
            get("formal-root"),
            get("archive"),
            get("attestation"),
            get("receipt"),
            get("journal"),
            transport=service,
        )

    monkeypatch.setattr(release, "_run_json", command)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release", "--profile", str(profile), "--formal-root", str(source), "--publish"],
    )
    with pytest.raises(RuntimeError, match="synthetic export"):
        release.main(tmp_path)
    assert (
        json.loads(target.read_text(encoding="utf-8"))["submission_state"] == "submitted_unverified"
    )
    assert json.loads(profile.read_text(encoding="utf-8"))["publication"]["version_id"] == "ver_old"
    release.main(tmp_path)
    assert len(service.uploads) == 1
    assert json.loads(target.read_text(encoding="utf-8"))["submission_state"] == "verified"
    assert json.loads(profile.read_text(encoding="utf-8"))["publication"]["version_id"] == "ver_new"
