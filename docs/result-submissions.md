# Reviewed result submissions

`webstone results` and `harness.results.ResultsClient` share one publication path. Internal and external contributors submit public data as pull requests to `lexmount/webstone-site`; neither can write results directly to main through this interface. The website requires no separate account and no submission database.

## CLI

Install GitHub CLI, authenticate with your own GitHub account, and use Webstone from the maintained source until a PyPI release containing these commands is available:

```sh
pip install "git+https://github.com/lexmount/webstone.git"
gh auth login
webstone results template --benchmark lexbench-webfetch --definition split-media --output report.json
# Fill actual measurements and public evidence; templates intentionally contain placeholders.
webstone results validate report.json
webstone results submit report.json --provider-url your-method=https://example.com/project --dry-run
webstone results submit report.json --provider-url your-method=https://example.com/project
webstone results status 123 --verify-published
```

`validate` performs preflight against the current catalog. The website's trusted validator performs authoritative metric, category, denominator and version checks in CI. A successful check does not prove a measurement is true: maintainers must review its evidence and methodology. Every new submission supplies a public HTTPS evidence URL, actual sample population, method versions/configuration and an English `datasetDifference`. Use `null` for undisclosed values, and preserve genuine zeros and original units. Participant IDs need official HTTPS product/GitHub links; `--provider-url` proposes only additive links for the submitted methods.

The CLI reads an exact main commit, allocates the next version per benchmark, and commits a single JSON report plus any required participant links to a personal fork. It never edits upstream main. `--dry-run` does not write to GitHub. Repeated identical submissions return the existing PR; retries after PR-creation failure reuse the already-created result branch. Measurements and source provenance are copied, not rewritten.

If another result is published before this PR, rebase its version without changing measured values:

```sh
webstone results rebase 123
```

The command only handles the current contributor's own open single-result data PR. It adds a normal merge commit containing current main and the versioned report; it does not force-push. Checks and approval must run again. Corrections and rollbacks are new result submissions with an explicit explanation in result notes, never edits to an already-published file.

`status` reports GitHub PR state and review history. `--verify-published` additionally compares live result JSON with the approved file at the merged commit. A mismatch or unavailable site is reported as publication not verified, never as success.

## Python SDK

```python
import json
from pathlib import Path
from harness.results import GhTransport, ResultsClient

report = json.loads(Path("report.json").read_text(encoding="utf-8"))
client = ResultsClient(GhTransport())
client.check(report)
preview = client.submit(report, dry_run=True)
submission = client.submit(report)
print(submission["url"])
```

For a non-interactive integration, `GitHubTransport(token)` accepts an explicitly supplied GitHub token. It sends credentials only to GitHub, and does not load evaluation `.env` files or borrow deployment credentials. Prefer the contributor's existing `gh` login for interactive use. New participants pass `provider_urls={"method-id": "https://official.example/product"}` to `check` and `submit`.

All uploaded files, including pending PRs, are public. Publish only non-sensitive result summaries, configuration and public evidence links. Keep credentials, private raw evidence and large artifacts outside Git. Each submitted report is limited to 1 MiB.

The [site's submission guide](https://github.com/lexmount/webstone-site/blob/main/docs/submitting-results.md) owns the accepted website format, required checks and approval settings. New benchmark definitions are maintained separately from result submissions. The personal-fork workflow requires that the website repository be public; the CLI rejects a private target rather than introducing a privileged internal bypass.

## PyPI publication

The package already declares the `webstone` command and an installable wheel/sdist configuration. The PyPI workflow uses Trusted Publishing, not a stored PyPI token. An account owner must configure a publisher for:

- PyPI project: `webstone`
- GitHub owner/repository: `lexmount/webstone`
- Workflow: `publish-pypi.yml`
- GitHub environment: `pypi`

Configure a pending Trusted Publisher if the PyPI project does not yet exist. The `pypi` environment should require maintainer approval. Organization ownership is optional for publishing; a commercial PyPI organization needs separate application, billing and administrator approval.

Publishing requires a separately approved software version and immutable `vMAJOR.MINOR.PATCH` tag, matching `pyproject.toml` and `uv.lock`, successful full CI on that exact commit, and a published GitHub software release. The workflow verifies these boundaries, builds both distributions, checks metadata and publishes with attestations. Adding this workflow does not itself bump a version, create a tag, publish a package or certify benchmark numbers. Follow [Software releases](releases.md).
