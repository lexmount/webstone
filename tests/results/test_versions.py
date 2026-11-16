"""Semantic result contract matches the website's numeric edition rules."""

import copy

import pytest

from harness.results import ResultsClient, SubmissionError
from harness.results.versions import canonical, identity, next_version, parts, release_info
from tests.results.test_submissions import REPORT, FakeGitHub


@pytest.mark.parametrize(
    ("previous", "kind", "expected"),
    [
        (None, "results", "v1.0.0"),
        ("v2", "results", "v2.1.0"),
        ("v2.10.9", "results", "v2.11.0"),
        ("v2.10.9", "metadata", "v2.10.10"),
        ("v2.10.9", "protocol", "v3.0.0"),
    ],
)
def test_version_transitions(previous, kind, expected):
    assert next_version(previous, kind) == expected
    assert canonical("v2") == "v2.0.0"
    assert parts("v2.10.0") > parts("v2.9.9")


@pytest.mark.parametrize("version", ["v01", "v1.01.0", "v0.1.0", "v1.0", "v1.0.0-beta", "v1\n"])
def test_invalid_versions(version):
    with pytest.raises(ValueError):
        parts(version)


def test_release_dates_and_retry_identity_are_separate_from_measurements():
    source = copy.deepcopy(REPORT)
    source["release"]["date"] = "2026-11-16"
    released = {**source, "id": "fixture-v1.1.0", "version": "v1.1.0"}
    released["release"] = release_info(source, "v1")
    assert released["release"]["previous"] == "v1.0.0"
    assert identity(released) == identity(source)
    assert "version" not in source
    for date in ["2026-02-30", "2099-01-01", "2026-1-1"]:
        source["release"]["date"] = date
        with pytest.raises(ValueError):
            release_info(source, "v1")


def test_cli_allocates_semantic_versions_with_honest_release_metadata():
    api = FakeGitHub()
    api.versions = ["v2.9.0", "v2.10.0", "v1"]
    client = ResultsClient(api, api.repository)
    source = copy.deepcopy(REPORT)
    source["release"]["date"] = "2026-11-16"
    result = client.submit(source)
    assert result["version"] == "v2.11.0"
    assert api.saved_report["release"] == {
        "kind": "results", "date": "2026-11-16", "previous": "v2.10.0",
        "summary": "Publish synthetic evaluation results.",
    }
    assert source["benchmarks"] == api.saved_report["benchmarks"]
    template = client.template("fixture-results", kind="metadata")
    assert template["release"] == {"kind": "metadata", "date": "", "summary": ""}
    source["release"]["summary"] = ""
    with pytest.raises(SubmissionError, match="release.summary"):
        client.check(source)
