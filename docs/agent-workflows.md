# Agent workflows

Webstone is designed for agents working on a user's behalf. The **skill plans the task and enforces evidence boundaries**; the **CLI executes maintained stage owners**. There is no universal run-all command and no automatic conversion of arbitrary reports into leaderboard scores. Choose the Track and benchmark contract first.

## Install software and skill

In a Python 3.11+ virtual environment, install the CLI and copy the skill to your agent’s skills directory:

```sh
pip install --upgrade webstone
webstone skills install --directory "$HOME/.codex/skills"
```

`webstone` is the CLI shipped by the Python package. The skill installer copies the packaged, version-matched skill offline. It refuses an existing `webstone` destination instead of overwriting user instructions. Inspect/remove or back up an older installation deliberately before upgrading. Other agents can select their supported skills root with `--directory`.

If your installed release predates these commands, use a source checkout that contains the skill:

```sh
git clone https://github.com/lexmount/webstone.git
cd webstone
uv sync --frozen
uv run --frozen webstone skills install --directory "$HOME/.codex/skills"
uv run --frozen webstone tracks
```

Ask the installed **webstone** skill to select and perform the desired workflow. In a checkout, use `uv run --frozen` for repository Python, and read `AGENTS.md` plus the maintained `benchmark-report` skill. Advanced profiles, examples, environment setup and some external reference assets still require a source checkout at the corresponding software release tag. Installing a wheel does not install browsers, Docker sites, provider accounts or evaluation data. Do not use newer main instructions as evidence that an older package supports a stage.

## Supported entry points

All routes below reuse existing implementations. `--help` is the argument authority. Optional extras are installed with `pip install 'webstone[EXTRA]'` after publication or `uv sync --frozen --extra EXTRA` in a checkout; each selected provider may need additional dependencies.

| Task | CLI | Boundary |
| --- | --- | --- |
| Discover Tracks and benchmarks | `webstone tracks` | JSON capability/guide index plus exact dataset pins and suites; no calls or downloads |
| Inspect pinned dataset selections | `webstone datasets list` | Local package lock; readable tags never override exact commits |
| Fetch/repair selected inputs | `webstone datasets fetch --help` | Existing hash-verified dataset cache owner; downloads only on invocation |
| Web Fetch | `webstone validate`, `run`, `rescore`, `report` | Generic fixture/native workflow; specialized cohorts remain explicit commands in root help |
| Web Search | `webstone web-search run`, `report`, `judge`, `review`, `publish` | Profile-bound collection/scoring; judge/review are explicit operations, not hidden automatic review |
| Web Extract | `webstone web-extract --help` | Prepare/plan/preflight/reproduce/score/report/host stages; `web-extract-eval` extra and declared references |
| Browser Trajectory Replay | `webstone trajectory-replay --help` | Evaluate/replay/catalog-run plus report/check/package/publish; fixed trajectories stay model-free |
| Browser Use | `webstone browser-use run`, `report`, `check`, `package`, `publish` | Experimental; model, isolated sites and `browser-use-eval` dependencies required |
| Local report rebuild | Selected track's report/check commands | Reuses saved evidence; does not execute provider calls or share publicly |
| Share a report | Selected track's publish/verify commands | Requires an explicitly selected, authorized host/profile and live verification |
| Leaderboard results | [Site submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md) | Open a result PR with GitHub and Node.js |
| Dataset proposal | `webstone datasets propose --help` | Existing dataset publisher; explicit source checkout/revision; `dataset-publish` extra for HF PR upload; never merges PRs |
| Adopt a dataset commit | `webstone datasets pin --help` | Explicit `--root` Webstone checkout, inspected diff and code PR after data review |

The default root `webstone run` remains Web Fetch for compatibility. Track-prefixed routes do not reinterpret existing flags. `tracks` lists independently selected datasets, not a claim that every stage is fully automated or available in the base package.

## A bounded first run

The offline Web Fetch fixture has one expected pass and one expected failure, and needs no service credentials:

```sh
webstone validate
webstone run --delay 0 --output /tmp/webstone-fixture
webstone report /tmp/webstone-fixture
```

Use a new output path; never overwrite existing evidence. Synthetic fixture results are not leaderboard submissions. For real work, select the track guide returned by `webstone tracks`, freeze the task population, providers/models, timer boundaries, retries, concurrency, input/scorer hashes and cost source, and validate a bounded sample before expensive collection. Preserve failed-call timing and unavailable costs. Keep data under `WEBSTONE_DATA_HOME` or `~/.webstone`, outside Git. A report or scoring revision reuses saved evidence whenever possible; never collect new calls just to improve a rank.

## Submit and maintain

Use the [site submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md) to prepare a result edition from the current catalog and open a fork PR with public evidence. Git, GitHub, and Node.js are enough. Existing `webstone results` integrations can use the [CLI reference](result-submissions.md).

Dataset definitions belong in Hugging Face PRs. `datasets propose` may build a local bundle or open an HF PR with `--create-pr`; it cannot merge it or release tags. Review and merge data first, then propose a new exact pin through a Webstone code PR. Software, dataset, result and report versions remain independent. No result/dataset operation authorizes a software release.

## Release and installation boundary

Prepare and review code first. After merge and full CI on that exact main commit, a maintainer creates the approved annotated `vMAJOR.MINOR.PATCH` tag. Pushing that tag starts [publish-pypi.yml](../.github/workflows/publish-pypi.yml). Metadata/tag/main-CI gates run before the protected `pypi` environment and OIDC upload. Configure [Trusted Publishing](pypi-publishing-setup.md), approve the protected deployment, then verify the PyPI files, attestations and clean installation. The PyPI owner may be a personal account; author/repository remain Lexmount. Never claim upload succeeded from a wheel build alone.
