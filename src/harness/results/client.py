"""One GitHub publication path for internal and external result contributors."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import subprocess
import time
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_REPOSITORY = "lexmount/webstone-site"
DEFAULT_WEBSITE = "https://webstone.dev.lexmount.net"
MAX_REPORT_BYTES = 1024 * 1024


class SubmissionError(ValueError):
    """An actionable public submission failure, without credential details."""


class GitHubError(SubmissionError):
    def __init__(self, status: int | None):
        self.status = status
        super().__init__(
            f"GitHub request failed (HTTP {status or 'unavailable'}). "
            "Check repository access and gh authentication."
        )


class Transport(Protocol):
    def request(self, method: str, path: str, body: Any = None) -> Any: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _reject_credentials(value: Any) -> None:
    if isinstance(value, str):
        if re.search(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|"
            r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})",
            value,
        ):
            raise SubmissionError("Remove credential-like values before submitting public results.")
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_credentials(key)
            _reject_credentials(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_credentials(child)


def _api_path(path: str) -> None:
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n"):
        raise SubmissionError("GitHub API paths must be relative to api.github.com.")


class GhTransport:
    """Reuse the contributor's GitHub CLI login; never read or print its token."""

    def request(self, method: str, path: str, body: Any = None) -> Any:
        _api_path(path)
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            "--header",
            "Accept: application/vnd.github+json",
            path,
        ]
        if body is not None:
            command.extend(["--input", "-"])
        try:
            result = subprocess.run(
                command,
                input=_json(body) if body is not None else None,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=60,
            )
        except FileNotFoundError as error:
            raise SubmissionError(
                "Install GitHub CLI from https://cli.github.com/ and run gh auth login."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise SubmissionError(
                "GitHub request timed out. No credentials are included in this error."
            ) from error
        if result.returncode:
            status = re.search(r"HTTP (\d{3})", result.stderr)
            raise GitHubError(int(status[1]) if status else None)
        return json.loads(result.stdout) if result.stdout.strip() else None


class _GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https" or urlsplit(newurl).netloc != "api.github.com":
            raise SubmissionError("Refusing a GitHub API redirect to another host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubTransport:
    """SDK transport using an explicitly supplied token, sent only to GitHub."""

    def __init__(self, token: str):
        if not token.strip():
            raise SubmissionError("An explicit GitHub token is required for SDK writes.")
        self._token = token
        self._opener = build_opener(_GitHubRedirect())

    def request(self, method: str, path: str, body: Any = None) -> Any:
        _api_path(path)
        request = Request(
            "https://api.github.com" + path,
            method=method,
            data=_json(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "Webstone-results",
            },
        )
        try:
            opener = self._opener
            with opener.open(request, timeout=60) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise SubmissionError("GitHub response exceeds the client limit.")
                return json.loads(raw) if raw else None
        except HTTPError as error:
            raise GitHubError(error.code) from error
        except URLError as error:
            raise SubmissionError(
                "Unable to contact GitHub. Check your network connection."
            ) from error


class _PublicationRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise SubmissionError("Publication verification refuses a non-HTTPS redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _publication_open(request: Request):
    opener = build_opener(_PublicationRedirect())
    return opener.open(request, timeout=20)


def _https(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def _public_evidence(value: Any) -> bool:
    if not _https(value):
        return False
    host = urlsplit(value).hostname.lower().rstrip(".")
    return (
        "." in host
        and ":" not in host
        and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host)
        and not re.search(r"(^|\.)(localhost|local|internal|test|invalid)$", host)
    )


def _read_json_file(record: dict) -> Any:
    if record.get("type") != "file" or record.get("encoding") != "base64":
        raise SubmissionError("Expected a regular JSON file in the website repository.")
    encoded = "".join(record["content"].split())
    try:
        return json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, KeyError) as error:
        raise SubmissionError("Invalid JSON file returned by GitHub.") from error


def preflight(report: dict, catalog: dict, provider_urls: dict[str, str] | None = None) -> dict:
    """Check common input mistakes; the trusted site CI remains authoritative."""
    try:
        encoded = _json(report)
    except (ValueError, TypeError, RecursionError) as error:
        raise SubmissionError("Results must be JSON with finite numeric values.") from error
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise SubmissionError("A report must not exceed 1 MiB. Keep raw logs outside Git.")
    _reject_credentials(report)
    _reject_credentials(provider_urls or {})
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("benchmarks"), list)
        or len(report["benchmarks"]) != 1
    ):
        raise SubmissionError("A result file must contain exactly one benchmark.")
    if (
        not isinstance(report.get("datasetDifference"), str)
        or not report["datasetDifference"].strip()
        or re.search(r"[\u3400-\u9fff]", report["datasetDifference"])
    ):
        raise SubmissionError(
            "Provide an English datasetDifference explaining the evaluated scope."
        )
    if not isinstance(report.get("source"), dict) or not _public_evidence(
        report["source"].get("url")
    ):
        raise SubmissionError(
            "Provide a public HTTPS evidence URL with a DNS hostname in source.url."
        )
    result = report["benchmarks"][0]
    if not isinstance(result, dict):
        raise SubmissionError("The benchmark result must be an object.")
    info = next((item for item in catalog["benchmarks"] if item["id"] == result.get("id")), None)
    definition = next(
        (
            item
            for item in (info or {}).get("definitions", [])
            if item["id"] == result.get("definitionId")
        ),
        None,
    )
    if not info or not definition:
        raise SubmissionError(
            "Select an existing benchmark and evaluation definition from the catalog."
        )
    if type(result.get("taskCount")) is not int or result["taskCount"] < 1:
        raise SubmissionError("taskCount must be the actual positive integer sample count.")
    providers = result.get("providers")
    if (
        not isinstance(providers, list)
        or not providers
        or any(not isinstance(item, dict) for item in providers)
    ):
        raise SubmissionError("Supply at least one measured method.")
    ids = [item.get("id") for item in providers]
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value)
        for value in ids
    ) or len(set(ids)) != len(ids):
        raise SubmissionError("Method IDs must be unique lowercase identifiers.")
    metrics = {item["id"] for item in definition["metrics"]}
    links = {**catalog["providerUrls"], **(provider_urls or {})}
    for provider in providers:
        if not _https(links.get(provider["id"])):
            raise SubmissionError(
                f"Supply --provider-url {provider['id']}=https://... for a new method."
            )
        scores = provider.get("scores")
        if not isinstance(scores, dict) or set(scores) != metrics:
            raise SubmissionError(
                "Include every metric in the definition; use null for undisclosed values."
            )
        if any(
            value is not None
            and (
                type(value) not in (int, float)
                or (isinstance(value, float) and not math.isfinite(value))
                or (type(value) is int and abs(value) > 2**53 - 1)
            )
            for value in scores.values()
        ):
            raise SubmissionError("Scores must be finite JSON numbers with safe integers, or null.")
    for provider, url in (provider_urls or {}).items():
        if provider not in ids or not _https(url):
            raise SubmissionError("Project URLs must be HTTPS and belong to a submitted method.")
        if provider in catalog["providerUrls"] and catalog["providerUrls"][provider] != url:
            raise SubmissionError(
                "A result submission cannot change an existing method's project URL."
            )
    return info


class ResultsClient:
    """Prepare, submit, rebase and inspect reviewed result pull requests."""

    def __init__(
        self,
        transport: Transport | None = None,
        repository: str = DEFAULT_REPOSITORY,
        *,
        website: str | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise SubmissionError("Repository must be OWNER/NAME on GitHub.")
        if any(part in {".", ".."} for part in repository.split("/")):
            raise SubmissionError("Invalid repository name.")
        self.transport = transport or GhTransport()
        self.repository = repository
        self.website = website or (DEFAULT_WEBSITE if repository == DEFAULT_REPOSITORY else None)
        if self.website and (
            not _public_evidence(self.website)
            or urlsplit(self.website).query
            or urlsplit(self.website).fragment
        ):
            raise SubmissionError("The publication website must be a public HTTPS base URL.")

    def _request(self, method: str, suffix: str, body: Any = None, repository: str | None = None):
        return self.transport.request(
            method, f"/repos/{repository or self.repository}/{suffix}", body
        )

    def _file(self, file: str, ref: str | None = None, repository: str | None = None):
        suffix = "?" + urlencode({"ref": ref}) if ref else ""
        return _read_json_file(
            self._request("GET", f"contents/{file}{suffix}", repository=repository)
        )

    def catalog(self) -> dict:
        return self._file("benchmarks/catalog.json")

    def check(self, report: dict, provider_urls: dict[str, str] | None = None) -> dict:
        info = preflight(report, self.catalog(), provider_urls)
        return {
            "benchmark": info["id"],
            "preflight": "passed",
            "note": "Trusted repository CI performs authoritative validation before approval.",
        }

    def template(self, benchmark: str, definition: str | None = None) -> dict:
        catalog = self.catalog()
        info = next((item for item in catalog["benchmarks"] if item["id"] == benchmark), None)
        if not info:
            raise SubmissionError("Unknown benchmark.")
        definitions = info["definitions"]
        selected = (
            next((item for item in definitions if item["id"] == definition), None)
            if definition
            else definitions[0]
            if len(definitions) == 1
            else None
        )
        if not selected:
            raise SubmissionError(
                "Choose --definition from: " + ", ".join(item["id"] for item in definitions)
            )
        provider = {
            "id": "your-method",
            "name": "Your method",
            "description": "Describe its version and configuration.",
            "mode": "Describe execution mode.",
            "scores": {item["id"]: None for item in selected["metrics"]},
            "passed": None,
        }
        result = {
            "id": benchmark,
            "definitionId": selected["id"],
            "taskCount": None,
            "notes": [],
            "reportUrl": None,
            "providers": [provider],
        }
        if selected.get("categories"):
            result["categoryTotals"] = {item["id"]: None for item in selected["categories"]}
            provider["categoryPassed"] = {item["id"]: None for item in selected["categories"]}
        return {
            "datasetDifference": "",
            "source": {
                "kind": "report-page",
                "url": "",
                "title": "",
                "method": "",
                "scope": "",
                "importedAt": datetime.now(UTC).isoformat(),
                "dateNote": "Template creation time, not evaluation time.",
            },
            "benchmarks": [result],
            "evidence": [],
        }

    def _prepare(self, report: dict, provider_urls: dict[str, str] | None):
        metadata = self.transport.request("GET", f"/repos/{self.repository}")
        if metadata.get("private"):
            raise SubmissionError(
                "The target repository is private; public result submissions are not enabled."
            )
        branch = metadata["default_branch"]
        base = self._request("GET", f"git/ref/heads/{quote(branch, safe='')}")["object"]["sha"]
        commit = self._request("GET", f"git/commits/{base}")
        catalog = self._file("benchmarks/catalog.json", base)
        info = preflight(report, catalog, provider_urls)
        benchmark = info["id"]
        try:
            files = self._request("GET", f"contents/results/{benchmark}?{urlencode({'ref': base})}")
        except GitHubError as error:
            if error.status != 404:
                raise
            files = []
        if len(files) >= 1000:
            raise SubmissionError(
                "The benchmark directory reached GitHub's listing limit. "
                "Submit a reviewed result PR manually; no version was allocated."
            )
        versions = [
            int(match[1])
            for file in files
            if (
                match := re.fullmatch(
                    re.escape(benchmark) + r"-v([1-9]\d*)-report\.json", file.get("name", "")
                )
            )
        ]
        version = f"v{max(versions, default=0) + 1}"
        normalized = copy.deepcopy(report)
        normalized.update(id=f"{benchmark}-{version}", version=version)
        content = _json(normalized)
        if len(content.encode("utf-8")) > MAX_REPORT_BYTES:
            raise SubmissionError("The versioned report exceeds 1 MiB. Reduce its public notes.")
        file = f"results/{benchmark}/{benchmark}-{version}-report.json"
        entries = [{"path": file, "mode": "100644", "type": "blob", "content": content}]
        additions = {
            key: value
            for key, value in (provider_urls or {}).items()
            if key not in catalog["providerUrls"]
        }
        if additions:
            catalog["providerUrls"].update(additions)
            entries.append(
                {
                    "path": "benchmarks/catalog.json",
                    "mode": "100644",
                    "type": "blob",
                    "content": _json(catalog),
                }
            )
        return {
            "base": base,
            "base_tree": commit["tree"]["sha"],
            "base_branch": branch,
            "benchmark": benchmark,
            "version": version,
            "file": file,
            "entries": entries,
        }

    def _commit(self, prepared: dict, fork: str, parents: list[str]) -> str:
        tree = self._request(
            "POST",
            "git/trees",
            {"base_tree": prepared["base_tree"], "tree": prepared["entries"]},
            fork,
        )
        commit = self._request(
            "POST",
            "git/commits",
            {
                "tree": tree["sha"],
                "parents": parents,
                "message": f"feat(results): publish {prepared['benchmark']} {prepared['version']}",
            },
            fork,
        )
        return commit["sha"]

    def _wait_for_fork(self, fork: str, base: str) -> None:
        for attempt in range(5):
            try:
                self._request("GET", f"git/commits/{base}", repository=fork)
                return
            except GitHubError as error:
                if error.status not in {404, 409}:
                    raise
                if attempt < 4:
                    time.sleep(0.5 * 2**attempt)
        raise SubmissionError("GitHub is still preparing your fork. Retry the same submit command.")

    def submit(
        self, report: dict, provider_urls: dict[str, str] | None = None, *, dry_run: bool = False
    ) -> dict:
        prepared = self._prepare(report, provider_urls)
        if dry_run:
            return {
                "dry_run": True,
                "base": prepared["base"],
                "version": prepared["version"],
                "files": [item["path"] for item in prepared["entries"]],
            }
        login = self.transport.request("GET", "/user")["login"]
        identity = copy.deepcopy(report)
        identity.pop("id", None)
        identity.pop("version", None)
        digest = hashlib.sha256(
            json.dumps(
                {"report": identity, "providerUrls": provider_urls or {}},
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()[:16]
        branch = f"webstone-results/{prepared['benchmark']}/{digest}"
        existing = self._request(
            "GET",
            "pulls?"
            + urlencode(
                {"state": "all", "head": f"{login}:{branch}", "base": prepared["base_branch"]}
            ),
        )
        if existing:
            return {
                "url": existing[0]["html_url"],
                "number": existing[0]["number"],
                "state": "merged" if existing[0].get("merged_at") else existing[0]["state"],
                "existing": True,
            }
        name = self.repository.split("/")[1]
        fork = f"{login}/{name}"
        try:
            fork_info = self.transport.request("GET", f"/repos/{fork}")
        except GitHubError as error:
            if error.status != 404:
                raise
            fork_info = self._request("POST", "forks", {"default_branch_only": True})
            fork = fork_info["full_name"]
        if fork_info.get("parent", {}).get("full_name") != self.repository:
            raise SubmissionError(
                "Your same-named repository is not a fork of the website. "
                "Resolve that name collision before submitting."
            )
        self._wait_for_fork(fork, prepared["base"])
        try:
            existing_ref = self._request(
                "GET", f"git/ref/heads/{quote(branch, safe='')}", repository=fork
            )
        except GitHubError as error:
            if error.status != 404:
                raise
            sha = self._commit(prepared, fork, [prepared["base"]])
            self._request("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": sha}, fork)
        else:
            saved_head = existing_ref["object"]["sha"]
            files = self._request(
                "GET",
                f"contents/results/{prepared['benchmark']}?{urlencode({'ref': saved_head})}",
                repository=fork,
            )
            names = [
                item["name"]
                for item in files
                if re.fullmatch(
                    re.escape(prepared["benchmark"]) + r"-v[1-9]\d*-report\.json", item["name"]
                )
            ]
            if not names:
                raise SubmissionError("The existing submission branch has no result report.")
            name = max(names, key=lambda value: int(value.rsplit("-v", 1)[1].split("-", 1)[0]))
            saved_file = f"results/{prepared['benchmark']}/{name}"
            saved = self._file(saved_file, saved_head, fork)
            saved_identity = copy.deepcopy(saved)
            saved_identity.pop("id", None)
            saved_identity.pop("version", None)
            if saved_identity != identity:
                raise SubmissionError(
                    "The existing submission branch changed. Inspect it before retrying."
                )
            prepared.update(file=saved_file, version=saved["version"])
        result = report["benchmarks"][0]
        pr = self._request(
            "POST",
            "pulls",
            {
                "base": prepared["base_branch"],
                "head": f"{login}:{branch}",
                "title": f"feat(results): publish {prepared['benchmark']} {prepared['version']}",
                "body": (
                    "Public benchmark result submission.\n\n"
                    f"- Definition: `{result['definitionId']}`\n"
                    f"- Cases: {result['taskCount']}\n- Methods: {len(result['providers'])}\n"
                    f"- Evidence: {report['source']['url']}\n\n{report['datasetDifference']}\n\n"
                    "Review evidence, denominators and configuration before approval. "
                    "Merging is not proof that deployment has completed."
                ),
            },
        )
        return {
            "url": pr["html_url"],
            "number": pr["number"],
            "state": "pending review",
            "version": prepared["version"],
            "file": prepared["file"],
        }

    def rebase(self, number: int) -> dict:
        pr = self._request("GET", f"pulls/{number}")
        login = self.transport.request("GET", "/user")["login"]
        head_repository = pr.get("head", {}).get("repo") or {}
        fork = head_repository.get("full_name")
        if (
            pr["state"] != "open"
            or head_repository.get("owner", {}).get("login") != login
            or not fork
            or fork == self.repository
            or not re.fullmatch(r"webstone-results/[a-z0-9-]+/[a-f0-9]{16}", pr["head"]["ref"])
        ):
            raise SubmissionError("Only rebase your own open result submission.")
        fork_info = self.transport.request("GET", f"/repos/{fork}")
        if fork_info.get("parent", {}).get("full_name") != self.repository:
            raise SubmissionError(
                "The result branch must belong to your personal fork of the target."
            )
        changes = self._request("GET", f"pulls/{number}/files?per_page=100")
        reports = [
            item
            for item in changes
            if re.fullmatch(
                r"results/[a-z0-9-]+/[a-z0-9-]+-v[1-9]\d*-report\.json", item["filename"]
            )
        ]
        if len(reports) != 1 or any(
            item["status"] != "added"
            or item["filename"] not in {reports[0]["filename"], "benchmarks/catalog.json"}
            for item in changes
            if item["filename"] != "benchmarks/catalog.json"
        ):
            raise SubmissionError(
                "Only a single data-only result submission can be rebased by this command."
            )
        fork, head = pr["head"]["repo"]["full_name"], pr["head"]["sha"]
        report = self._file(reports[0]["filename"], head, fork)
        proposed = self._file("benchmarks/catalog.json", head, fork)
        ids = {provider["id"] for provider in report["benchmarks"][0]["providers"]}
        original_base = self._request("GET", f"compare/{pr['base']['sha']}...{head}")[
            "merge_base_commit"
        ]["sha"]
        original_catalog = self._file("benchmarks/catalog.json", original_base)
        original_urls, proposed_urls = original_catalog["providerUrls"], proposed["providerUrls"]
        if (
            {key: value for key, value in proposed.items() if key != "providerUrls"}
            != {key: value for key, value in original_catalog.items() if key != "providerUrls"}
            or any(proposed_urls.get(key) != value for key, value in original_urls.items())
            or any(key not in original_urls and key not in ids for key in proposed_urls)
        ):
            raise SubmissionError(
                "Rebase only supports result data and additive participant links."
            )
        links = {key: value for key, value in proposed["providerUrls"].items() if key in ids}
        prepared = self._prepare(report, links)
        if pr["base"]["ref"] != prepared["base_branch"]:
            raise SubmissionError("The submission must target the repository default branch.")
        sha = self._commit(prepared, fork, list(dict.fromkeys([head, prepared["base"]])))
        self._request(
            "PATCH",
            f"git/refs/heads/{quote(pr['head']['ref'], safe='')}",
            {"sha": sha, "force": False},
            fork,
        )
        return {
            "url": pr["html_url"],
            "version": prepared["version"],
            "state": "updated; checks and approval required again",
        }

    def status(self, number: int, *, verify_published: bool = False) -> dict:
        if verify_published and not self.website:
            raise SubmissionError(
                "Specify website for publication verification of a custom repository."
            )
        pr = self._request("GET", f"pulls/{number}")
        reviews = self._request("GET", f"pulls/{number}/reviews?per_page=100")
        result = {
            "url": pr["html_url"],
            "number": number,
            "state": "merged" if pr.get("merged") else pr["state"],
            "reviews": [
                {"reviewer": item["user"]["login"], "state": item["state"]} for item in reviews
            ],
            "note": "A merged PR still needs deployment; verify its version on the website.",
        }
        if verify_published and pr.get("merged"):
            files = self._request("GET", f"pulls/{number}/files?per_page=100")
            paths = [
                item["filename"]
                for item in files
                if re.fullmatch(
                    r"results/[a-z0-9-]+/[a-z0-9-]+-v[1-9]\d*-report\.json", item["filename"]
                )
            ]
            published = bool(paths and pr.get("merge_commit_sha"))
            for path in paths if published else []:
                expected = self._file(path, pr["merge_commit_sha"])
                url = f"{self.website.rstrip('/')}/{path}?submission={pr['merge_commit_sha']}"
                request = Request(url, headers={"User-Agent": "Webstone-results"})
                try:
                    with _publication_open(request) as response:
                        actual = json.loads(response.read(MAX_REPORT_BYTES + 1))
                    published = published and actual == expected
                except (OSError, ValueError):
                    published = False
            result["published"] = published
            result["state"] = "published" if published else "merged; publication not verified"
        return result
