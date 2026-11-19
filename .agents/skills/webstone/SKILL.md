---
name: webstone
description: Help an agent select Webstone tracks and benchmarks, prepare or resume evaluations, score saved evidence, build and share reports, submit leaderboard result PRs, or propose dataset updates. Use for the Webstone evaluation lifecycle; distinguish supported commands from stages requiring a source checkout or external services.
---

# Webstone workflow

Webstone is agent-oriented evaluation software. This skill plans the work; CLI commands execute existing deterministic owners. Do not invent a universal pipeline, infer scores from screenshots or substitute missing timing/cost with zero.

## Start and select

1. Inspect the user's goal and available evidence. Determine the Track, benchmark, exact dataset commit, provider/model configurations, run or resume, requested scope and deliverables. `webstone tracks` and `webstone datasets list` print JSON from the installed lock without downloads or calls. The website's result edition, dataset revision and software version are distinct identities.
2. Read the [workflow guide](https://github.com/lexmount/webstone/blob/main/docs/agent-workflows.md) and selected track guide returned by `webstone tracks`. For a checkout, read its AGENTS.md and the maintained benchmark-report skill before evaluation/report work. For a packaged installation, use a source checkout at the package's corresponding release tag when advanced code, profiles or reference assets are required; do not assume online main documentation matches an older installed version. Install only required optional extras and external browsers/sites. `--help` discovers the selected owner's actual flags; never guess a generic `--benchmark` or `--track` flag on a command that does not support it.
3. Freeze dataset/scorer IDs, task population, scopes, concurrency, retry policy, call budget, timers and billing units before expensive collection. Honor existing authorization; ask only for genuinely missing choices or permissions. New Web Fetch work selects full or main content explicitly; default noninteractive full. Preflight a bounded sample, then run the existing owner. Save evidence outside Git under WEBSTONE_DATA_HOME or ~/.webstone, and preserve resumed cohorts.

## Execute and score

- Web Fetch: `webstone validate`, `webstone run`, `webstone rescore`, `webstone report`; specialized native/WCXB/WebMainBench stages appear in `webstone --help`.
- Web Search: `webstone web-search run --help` selects a frozen external profile; report/judge/review/publish stages use the same track owner. Independent review is opt-in, not a default side effect of scoring.
- Web Extract: `webstone web-extract --help` exposes prepare/plan/preflight/reproduce/score/report and snapshot stages. It requires the web-extract-eval extra and its declared source inputs.
- Browser Trajectory Replay: `webstone trajectory-replay --help`; evaluate/replay/catalog-run have different task contracts. Do not substitute autonomous model actions for fixed recorded trajectories.
- Browser Use (experimental): `webstone browser-use run --help`; requires models, isolated sites and browser-use-eval dependencies. Full environment setup still belongs to the track guide.

A changed scorer or reference reuses saved calls where possible. Never select retries for higher scores, overwrite historical verdicts or merge incomplete populations into a certified result. Retain denominator and source hashes. Keep all-call latency separate from success-only latency; retain actual charges, quoted intervals and missing cost separately.

## Report and share

Build only when requested, through the selected track's existing report/check/package stages. Web Fetch `webstone report RUN` rebuilds saved fixture/generic evidence; specialized profiles use the guide's current owner. Inspect HTML, evidence links, language, units, cost and latency; do not claim unavailable measurements. Report sharing requires an authorized host bound in that report's profile. Use its existing publish/verify command; never borrow another report's destination. A build is not a deployment. Return the inspected report path or verified live link and exact source identity.

## Submit leaderboard results

Results are **PRs to lexmount/webstone-site**. Use that repository's [submit-webstone-result skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md) and current catalog/version guide. GitHub/Git and Node suffice; Webstone installation is not required to submit existing measurements. `webstone results --help` optionally automates template/validate/submit/status/rebase with the same contract. Never auto-map an arbitrary report into a benchmark definition, invent fields or copy an old result's scores. Propose only public data, preserve published files, follow review, and verify the live version after an authorized merge. Submission authorization does not authorize merging.

## Update datasets

`webstone datasets fetch --help` restores pinned inputs. `webstone datasets propose --help` delegates to the shared definition publisher with explicit checkout, revision, output and optional HF --create-pr. This builds a proposal, never merges it or tags dataset main. `webstone datasets pin --help` changes a Webstone checkout's selection only after the dataset input commit has been reviewed and merged; inspect the diff and propose a code PR. Explicitly select the target checkout with --root. Preserve immutable tags and frozen source/hash bindings. See [dataset revision rules](https://github.com/lexmount/webstone/blob/main/docs/benchmarks.md#historical-bindings-and-new-revisions). No automatic adoption of latest tags.

## Finish

Return selected inputs, run/scoring/report status, actual tests, PR/share URLs, review coverage and limitations. Distinguish planned, executed, reviewed, merged and deployed. Never create software tags or PyPI uploads from a dataset/result update; software release needs its separate maintainer-approved PR, tag and protected publishing workflow.
