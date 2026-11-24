# Reviewed result submissions

`webstone results` and `harness.results.ResultsClient` share one publication path. Internal and external contributors submit public data as pull requests to `lexmount/webstone-site`; neither can write results directly to main through this interface. The website requires no separate account and no submission database.

## CLI

Install [GitHub CLI](https://cli.github.com/) and Webstone, then sign in to GitHub:

```sh
pip install webstone
gh auth login
webstone <track> export RUN_DIR --receipt publish/receipt.json --output report.json
webstone results validate report.json --provider-url your-method=https://example.com/project
webstone results submit report.json --provider-url your-method=https://example.com/project --dry-run
webstone results submit report.json --provider-url your-method=https://example.com/project
webstone results status 123 --verify-published
```

### Export a scored run

`webstone <track> export` is the last pipeline stage. It reads the scored results of a run directory and writes the submission, so no measurement is typed by hand. `--receipt` names the publication receipt that the track's `publish` stage wrote; its public report URL becomes `source.url` and `reportUrl`. Without a receipt the submission is marked unpublished (`source.kind: unpublished-run`, no URL). `validate` and `submit` reject it because it has no public evidence: publish the report, then export again with the receipt.

| Track | `RUN_DIR` | Submission benchmark |
| --- | --- | --- |
| Web Fetch (`webstone export`) | A live run with `results.jsonl` and `dataset.json` | `lexbench-webfetch`, definition `split-media` |
| Web Search | A run with `profile.json`, `results.jsonl` and `receipt.json` | `lexbench-websearch` (`title-snippet-v2`) or `simpleqa-verified` (`source-match-v2`); a run with both takes `--benchmark` |
| Web Extract | `<home>/runs/<id>` with `contract.json` and its call records | `lexbench-swde` or `lexbench-zyte-product-extraction`, from the dataset manifest |
| Browser Use | An evidence directory with `attempts.jsonl` and `manifest.json` | `webarena-verified` (`standard`) |
| Browser Trajectory Replay | A directory with the effective `results.json` | `webarena-verified` (`standard`) |

Each exporter validates the scored records it reads against a versioned schema that binds the common roles in `src/harness/record_schema.py` (method, case, elapsed time, evidence and verdict). A metric the run does not measure is `null`: Web Search scenario relevance comes from the separate relevance review, prices are disclosed only when quoted in USD, and a Web Extract method that skipped part of the population has no quality scores. Method IDs are the run's method names in lowercase hyphen form. Read the exported notes, edit `release.summary` if the change needs a better description, and add the provider URL for a new method at submission.

`webstone results template --benchmark <id> --output report.json` still writes an unfilled template for results produced outside Webstone.

`validate` performs preflight against the current catalog. The website's trusted validator performs authoritative metric, category, denominator and version checks in CI. A successful check does not prove a measurement is true: maintainers must review its evidence and methodology. Every new submission supplies a public HTTPS evidence URL, actual sample population, method versions/configuration and an English `datasetDifference`. Use `null` for undisclosed values, and preserve genuine zeros and original units. Participant IDs need official HTTPS product/GitHub links; `--provider-url` proposes only additive links for the submitted methods.

The CLI reads an exact main commit, allocates the next version per benchmark, and commits a single JSON report plus any required participant links to a personal fork. It never edits upstream main. `--dry-run` does not write to GitHub. Repeated identical submissions return the existing PR; retries after PR-creation failure reuse the already-created result branch. Measurements and source provenance are copied, not rewritten.

If another result is published before this PR, rebase its version without changing measured values:

```sh
webstone results rebase 123
```

The command only handles the current contributor's own open single-result data PR. It adds a normal merge commit containing current main and the versioned report; it does not force-push. Checks and approval must run again. Corrections and rollbacks are new result submissions with an explicit explanation in result notes, never edits to an already-published file.

`status` reports GitHub PR state and review history. `--verify-published` additionally compares live result JSON with the approved file at the merged commit. A mismatch or unavailable site is reported as publication not verified, never as success.

For a custom repository, set the matching deployment explicitly with `webstone results --repo OWNER/NAME --website https://your-public-site.example status 123 --verify-published`, or `ResultsClient(..., repository="OWNER/NAME", website="https://your-public-site.example")`. A custom repository is never verified against the default Webstone deployment.

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

The base package and results CLI do not need the optional WebArena evaluator. PyPI metadata pins that evaluator by package version; `tool.uv.sources` and `uv.lock` retain its exact Git fork and commit for source environments. Until that fork version is independently published, users who need the optional Browser Use evaluator must install it explicitly before installing the extra:

```sh
pip install "git+https://github.com/lexmount/webarena-verified.git@da7b992eb10de78000f11a7f34fd4590c171fbd6"
pip install "webstone[browser-use-eval]"
```

Do not substitute the upstream registry evaluator: its source and scoring may differ. `uv sync --frozen --extra browser-use-eval` from the source checkout continues to use the pinned fork.

Maintainers publish the wheel and source distribution through GitHub Actions and PyPI Trusted Publishing. See the [publishing setup](pypi-publishing-setup.md) and [software release policy](releases.md).

## Result versions and dates

Result editions use independent `vMAJOR.MINOR.PATCH` versions, separate from Webstone software releases. `protocol` bumps MAJOR for changed populations/reference answers/scoring; `results` bumps MINOR for methods/configurations/measurements under the same protocol; `metadata` bumps PATCH for wording/evidence/links only, with unchanged measured data and configurations. The first edition is v1.0.0. Published files are immutable; every update is reviewed again.

`webstone results template --benchmark <id> --kind results --output result.json` includes `release.kind`, a blank `release.date` and `release.summary`. Fill an English summary of the actual change. The CLI allocates the next version and `release.previous`, proposing today's UTC date if blank. Reviewers confirm the actual publication date; it is not the execution date. Future dates are rejected. After a version collision, `rebase` allocates again and requires renewed review.

Historical vN editions remain unchanged on disk and resolve as vN.0.0 in the website. Their recorded source import dates are labelled Imported; no historical publication date is invented. See the website's [versioning guide](https://webstone.dev.lexmount.net/versioning).
