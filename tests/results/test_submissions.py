"""Public SDK/CLI controls use synthetic data and never call GitHub."""

import base64
import copy
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from harness.results import GhTransport, GitHubTransport, ResultsClient, SubmissionError
from harness.results.cli import main
from harness.results.client import GitHubError

CATALOG = {
    "providerUrls": {"fixture": "https://example.com/method"},
    "benchmarks": [
        {
            "id": "fixture-results",
            "definitions": [
                {
                    "id": "fixture-v1",
                    "metrics": [{"id": "quality", "unit": "percent", "direction": "desc"}],
                }
            ],
        }
    ],
}
REPORT = {
    "datasetDifference": "Synthetic fixture with two cases; no measured provider calls.",
    "source": {"kind": "report-page", "url": "https://example.com/evidence"},
    "benchmarks": [
        {
            "id": "fixture-results",
            "definitionId": "fixture-v1",
            "taskCount": 2,
            "providers": [
                {
                    "id": "fixture",
                    "name": "Fixture",
                    "description": "Synthetic fixture",
                    "mode": "Fixture",
                    "scores": {"quality": 50},
                    "passed": 1,
                }
            ],
            "notes": [],
        }
    ],
}


def encoded(value):
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(json.dumps(value).encode()).decode(),
    }


def test_preflight_rejects_private_evidence_and_project_credentials():
    from harness.results.client import preflight

    for host in ["localhost", "127.0.0.1", "192.168.1.1", "[::1]", "[fd00::1]", "reports.local"]:
        report = copy.deepcopy(REPORT)
        report["source"]["url"] = f"https://{host}/report"
        with pytest.raises(SubmissionError, match="public HTTPS"):
            preflight(report, CATALOG)
    with pytest.raises(SubmissionError, match="credential-like"):
        preflight(REPORT, CATALOG, {"fixture": "https://example.com/?token=ghp_" + "x" * 36})
    for prefix in ["\n", "\t"]:
        report = copy.deepcopy(REPORT)
        report["benchmarks"][0]["notes"] = [prefix + "ghp_" + "x" * 36]
        with pytest.raises(SubmissionError, match="credential-like"):
            preflight(report, CATALOG)
    for value in [
        ("ghp_" + "x" * 36,),
        ["-----BEGIN DSA PRIVATE KEY-----"],
        ["-----BEGIN ENCRYPTED PRIVATE KEY-----"],
    ]:
        report = copy.deepcopy(REPORT)
        report["benchmarks"][0]["notes"] = value
        with pytest.raises(SubmissionError, match="credential-like"):
            preflight(report, CATALOG)


class FakeGitHub:
    repository = "fixture-owner/fixture-site"
    fork = "fixture-user/fixture-site"

    def __init__(self):
        self.calls = []
        self.private = False
        self.catalog = copy.deepcopy(CATALOG)
        self.versions = [1]
        self.refs = {}
        self.prs = []
        self.base = "a" * 40
        self.saved_report = None
        self.fork_exists = True
        self.fail_pr_once = False
        self.fork_pending = 0
        self.proposed_catalog = None

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        parts = urlsplit(path)
        route, query = unquote(parts.path), parse_qs(parts.query)
        if method == "GET" and route == "/user":
            return {"login": "fixture-user"}
        upstream, fork = f"/repos/{self.repository}", f"/repos/{self.fork}"
        if route == upstream:
            return {"private": self.private, "default_branch": "main"}
        if method == "GET" and route == fork:
            if not self.fork_exists:
                raise GitHubError(404)
            return {"full_name": self.fork, "parent": {"full_name": self.repository}}
        if method == "POST" and route == upstream + "/forks":
            self.fork_exists = True
            return {"full_name": self.fork, "parent": {"full_name": self.repository}}
        prefix = fork if route.startswith(fork + "/") else upstream
        suffix = route[len(prefix) + 1 :]
        if method == "GET" and suffix == "git/ref/heads/main":
            return {"object": {"sha": self.base}}
        if method == "GET" and suffix.startswith("git/ref/heads/"):
            branch = suffix.removeprefix("git/ref/heads/")
            if branch not in self.refs:
                raise GitHubError(404)
            return {"object": {"sha": self.refs[branch]}}
        if method == "GET" and suffix.startswith("git/commits/"):
            if prefix == fork and self.fork_pending:
                self.fork_pending -= 1
                raise GitHubError(409)
            return {"tree": {"sha": "b" * 40}}
        if method == "GET" and suffix == "contents/benchmarks/catalog.json":
            if prefix == fork and self.proposed_catalog is not None:
                return encoded(self.proposed_catalog)
            return encoded(self.catalog)
        if method == "GET" and suffix == "contents/results/fixture-results":
            versions = self.versions
            if prefix == fork and self.saved_report:
                versions = [int(self.saved_report["version"][1:])]
            return [{"name": f"fixture-results-v{version}-report.json"} for version in versions]
        if method == "GET" and suffix.startswith("compare/"):
            return {"merge_base_commit": {"sha": "a" * 40}}
        if method == "GET" and suffix.startswith("contents/results/fixture-results/"):
            return encoded(self.saved_report)
        if method == "GET" and suffix == "pulls":
            return self.prs
        if method == "POST" and suffix == "git/trees":
            self.saved_report = json.loads(body["tree"][0]["content"])
            return {"sha": "c" * 40}
        if method == "POST" and suffix == "git/commits":
            return {"sha": "d" * 40}
        if method == "POST" and suffix == "git/refs":
            self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return {}
        if method == "POST" and suffix == "pulls":
            if self.fail_pr_once:
                self.fail_pr_once = False
                raise GitHubError(503)
            pr = {
                "number": 7,
                "html_url": f"https://github.com/{self.repository}/pull/7",
                "state": "open",
                "merged": False,
                "base": {"sha": self.base, "ref": "main"},
                "head": {
                    "sha": "d" * 40,
                    "ref": body["head"].split(":", 1)[1],
                    "repo": {"full_name": self.fork, "owner": {"login": "fixture-user"}},
                },
            }
            self.prs = [pr]
            return pr
        if method == "GET" and suffix == "pulls/7":
            return self.prs[0]
        if method == "GET" and suffix == "pulls/7/files":
            return [
                {
                    "filename": "results/fixture-results/fixture-results-v2-report.json",
                    "status": "added",
                }
            ]
        if method == "GET" and suffix == "pulls/7/reviews":
            return []
        if method == "PATCH" and suffix.startswith("git/refs/heads/"):
            return {}
        raise AssertionError((method, path, body, query))


@pytest.fixture
def setup_client():
    api = FakeGitHub()
    return api, ResultsClient(api, repository=api.repository, website="https://example.com")


def test_submit_uses_personal_fork_and_never_mutates_input_or_upstream_main(setup_client):
    api, client = setup_client
    source = copy.deepcopy(REPORT)
    result = client.submit(source)
    assert result["version"] == "v2"
    assert source == REPORT
    assert api.saved_report["id"] == "fixture-results-v2"
    assert api.saved_report["benchmarks"] == REPORT["benchmarks"]
    assert all(
        path.startswith(f"/repos/{api.fork}/") or path == f"/repos/{api.repository}/pulls"
        for method, path, _ in api.calls
        if method != "GET"
    )
    assert not any(method in {"PATCH", "PUT"} for method, _, _ in api.calls)


def test_dry_run_and_duplicate_submission_do_not_create_extra_prs(setup_client):
    api, client = setup_client
    preview = client.submit(REPORT, dry_run=True)
    assert preview["files"] == ["results/fixture-results/fixture-results-v2-report.json"]
    assert all(method == "GET" for method, _, _ in api.calls)
    first = client.submit(REPORT)
    writes = sum(method != "GET" for method, _, _ in api.calls)
    reordered = json.loads(json.dumps(REPORT, sort_keys=True))
    second = client.submit(reordered)
    assert second["existing"] is True and second["url"] == first["url"]
    assert sum(method != "GET" for method, _, _ in api.calls) == writes


def test_custom_repository_does_not_verify_against_an_unrelated_website(setup_client):
    api, _ = setup_client
    client = ResultsClient(api, repository=api.repository)
    with pytest.raises(SubmissionError, match="Specify website"):
        client.status(7, verify_published=True)
    assert not api.calls


def test_retry_after_pr_creation_failure_reuses_the_existing_data_branch(setup_client):
    api, client = setup_client
    api.fork_exists, api.fail_pr_once = False, True
    with pytest.raises(GitHubError):
        client.submit(REPORT)
    api.versions, api.base = [1, 2], "e" * 40
    result = client.submit(REPORT)
    assert result["number"] == 7
    assert result["version"] == "v2"
    assert (
        sum(method == "POST" and path.endswith("/git/commits") for method, path, _ in api.calls)
        == 1
    )


def test_async_fork_readiness_has_bounded_retry_before_writes(setup_client, monkeypatch):
    api, client = setup_client
    api.fork_exists, api.fork_pending = False, 2
    monkeypatch.setattr("harness.results.client.time.sleep", lambda seconds: None)
    assert client.submit(REPORT)["number"] == 7
    api = FakeGitHub()
    client = ResultsClient(api, repository=api.repository)
    api.fork_pending = 10
    with pytest.raises(SubmissionError, match="Retry the same"):
        client.submit(REPORT)
    assert all(method == "GET" for method, _, _ in api.calls)


def test_private_repository_and_invalid_public_payloads_fail_before_writes(setup_client):
    api, client = setup_client
    api.private = True
    with pytest.raises(SubmissionError, match="private"):
        client.submit(REPORT)
    assert all(method == "GET" for method, _, _ in api.calls)
    api.private = False
    for change in [
        lambda item: item.update(source=None),
        lambda item: item["benchmarks"][0].update(taskCount=True),
        lambda item: item["benchmarks"][0]["providers"][0]["scores"].update(quality=float("nan")),
        lambda item: item.update(benchmarks=[None]),
        lambda item: item["benchmarks"][0]["providers"][0]["scores"].update(quality=10**400),
    ]:
        data = copy.deepcopy(REPORT)
        change(data)
        with pytest.raises(SubmissionError):
            client.submit(data)


def test_new_provider_url_and_version_collision_rebase_preserve_measured_values(setup_client):
    api, client = setup_client
    source = copy.deepcopy(REPORT)
    source["benchmarks"][0]["providers"][0]["id"] = "new-fixture"
    client.submit(source, {"new-fixture": "https://example.com/new-fixture"})
    api.catalog["providerUrls"]["new-fixture"] = "https://example.com/new-fixture"
    api.versions, api.base = [1, 2], "e" * 40
    result = client.rebase(7)
    assert result["version"] == "v3"
    assert api.saved_report["benchmarks"] == source["benchmarks"]
    commits = [
        body
        for method, path, body in api.calls
        if method == "POST" and path.endswith("/git/commits")
    ]
    assert commits[-1]["parents"] == ["d" * 40, "e" * 40]
    update = [body for method, _, body in api.calls if method == "PATCH"][-1]
    assert update["force"] is False


def test_rebase_rejects_upstream_heads_and_unrelated_catalog_edits(setup_client):
    api, client = setup_client
    client.submit(REPORT)
    writes = sum(method != "GET" for method, _, _ in api.calls)
    api.prs[0]["head"]["repo"]["full_name"] = api.repository
    with pytest.raises(SubmissionError, match="own open"):
        client.rebase(7)
    api.prs[0]["head"]["repo"]["full_name"] = api.fork
    api.proposed_catalog = copy.deepcopy(api.catalog)
    api.proposed_catalog["benchmarks"][0]["definitions"][0]["metrics"][0]["unit"] = "ratio"
    with pytest.raises(SubmissionError, match="additive participant"):
        client.rebase(7)
    assert sum(method != "GET" for method, _, _ in api.calls) == writes


def test_catalog_uses_github_default_branch_without_assuming_main(setup_client):
    api, client = setup_client
    client.catalog()
    assert api.calls[-1][1].endswith("contents/benchmarks/catalog.json")


def test_transport_errors_never_repeat_credentials_and_sdk_rejects_other_hosts(monkeypatch):
    class Failure:
        returncode, stdout, stderr = 1, "", "secret-fixture-value (HTTP 403)"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Failure())
    with pytest.raises(SubmissionError) as error:
        GhTransport().request("GET", "/user")
    assert "secret-fixture-value" not in str(error.value)
    with pytest.raises(SubmissionError):
        GitHubTransport("synthetic-token").request("GET", "//example.com/user")


def test_publication_status_requires_exact_live_result_not_just_a_merge(setup_client, monkeypatch):
    api, client = setup_client
    client.submit(REPORT)
    api.prs[0].update(merged=True, merge_commit_sha="f" * 40)

    class PublicResponse:
        data = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps(self.data).encode()

    response = PublicResponse()
    response.data = api.saved_report

    def fetch(request, **kwargs):
        assert request.full_url.startswith("https://example.com/results/")
        return response

    monkeypatch.setattr("harness.results.client.urlopen", fetch)
    assert client.status(7, verify_published=True)["state"] == "published"
    response.data = {**api.saved_report, "datasetDifference": "A different result"}
    assert client.status(7, verify_published=True)["published"] is False


def test_cli_uses_shared_client_and_does_not_overwrite_templates(
    tmp_path, monkeypatch, capsys, setup_client
):
    api, client = setup_client
    monkeypatch.setattr("harness.results.cli.ResultsClient", lambda **kwargs: client)
    output = tmp_path / "template.json"
    main(["template", "--benchmark", "fixture-results", "--output", str(output)])
    template = json.loads(output.read_text(encoding="utf-8"))
    assert template["benchmarks"][0]["taskCount"] is None
    assert template["benchmarks"][0]["providers"][0]["scores"] == {"quality": None}
    with pytest.raises(SystemExit) as error:
        main(["template", "--benchmark", "fixture-results", "--output", str(output)])
    assert error.value.code == 2
    report = tmp_path / "report.json"
    report.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["validate", str(report)])
    assert error.value.code == 2
    report = tmp_path / "report.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    main(["submit", str(report), "--dry-run"])
    assert '"dry_run": true' in capsys.readouterr().out
    assert all(method == "GET" for method, _, _ in api.calls)
    link = tmp_path / "link.json"
    link.symlink_to(report)
    with pytest.raises(SystemExit):
        main(["validate", str(link)])
    report.write_bytes(b"\xff")
    with pytest.raises(SystemExit) as error:
        main(["validate", str(report)])
    assert error.value.code == 2
