<div align="center">

<img src="docs/assets/overview.png" alt="Webstone: the touchstone for your agent's web tools." width="640">

**English** · [简体中文](docs/README.zh-CN.md)

[![CI](https://github.com/lexmount/webstone/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/webstone/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](pyproject.toml)

[Quickstart](#quickstart) · [Tracks](#evaluation-tracks) · [Submit results](#submit-results) · [Leaderboard](https://webstone.dev.lexmount.net/)

</div>

Webstone benchmarks browsers, search services, and extraction tools for AI agents. Compare correctness, latency, resource use, and recorded cost.

- **Run an evaluation.** Use the [Webstone evaluation skill](.agents/skills/webstone/SKILL.md) to select the Track, benchmark, and methods, prepare their environment, and run the evaluation with the CLI.
- **Already have results?** Open a PR in [webstone-site](https://github.com/lexmount/webstone-site). The [submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md) guides that workflow. No Webstone CLI required.

## Quickstart

In a Python 3.11+ virtual environment, install Webstone and run the offline example.

```sh
pip install --upgrade webstone
webstone validate
webstone run --delay 0
```

Open the report path printed in the terminal. Expect one pass and one failure. The example needs no browser, Docker, or API key.

Install the evaluation skill for Codex. For another agent, use its skills directory.

```sh
webstone skills install --directory "$HOME/.codex/skills"
```

For a source install or a release that lacks the `skills` command, see the [installation guide](docs/agent-workflows.md#install).

## Evaluation tracks

| Track | Benchmarks and task suites | Guide |
| --- | --- | --- |
| Web Fetch | [LexBench-Web-Fetch](https://huggingface.co/datasets/Lexmount/LexBench-Web-Fetch), [LexBench-WebMainBench](https://huggingface.co/datasets/Lexmount/LexBench-WebMainBench), [LexBench-WCXB](https://huggingface.co/datasets/Lexmount/LexBench-WCXB) | [Local example](examples/web_fetch/README.md) · [Full guide](src/harness/web_fetch/README.md) |
| Web Search | [LexBench-Web-Search](https://huggingface.co/datasets/Lexmount/LexBench-Web-Search) · [LexBench-SimpleQA-Verified](https://huggingface.co/datasets/Lexmount/LexBench-SimpleQA-Verified) retrieval tasks | [Local example](examples/web_search/README.md) · [Full guide](src/harness/web_search/README.md) |
| Web Extract | [Zyte Product Extraction](https://huggingface.co/datasets/Lexmount/LexBench-Zyte-Product-Extraction) · [SWDE](examples/web_extract/README.md#swde-multi-domain-extraction) · [Expanded SWDE](https://huggingface.co/datasets/Lexmount/LexBench-Expanded-SWDE), [NewsXLM](https://huggingface.co/datasets/Lexmount/LexBench-NewsXLM) and [LiveWeb-IE](https://huggingface.co/datasets/Lexmount/LexBench-LiveWeb-IE) | [Structured field extraction](examples/web_extract/README.md) |
| Browser Trajectory Replay | [LexBench-Headless-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser) scenarios; [curated fixed-action tasks](https://huggingface.co/datasets/Lexmount/LexBench-Trajectory-Replay) with explicit checks; [WebArena replay](https://huggingface.co/datasets/Lexmount/LexBench-WebArena-Replay) with the official evaluator | [Headless inputs and upstream runner](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser#execute-with-the-original-runner) · [Curated suite](examples/trajectory_replay/README.md) · [WebArena replay](examples/trajectory_replay/public_webarena/README.md) |

Experimental [Browser Use](examples/browser_use/README.md) lets an agent choose browser actions and checks the outcome with the task evaluator.

[Recommended workflows](.agents/skills/webstone/SKILL.md) · [Evaluation design](docs/benchmarks.md#evaluation-design) · [Reports](docs/report-hosting.md)

## Submit results

Add a new result edition in a personal fork of `webstone-site` and open a PR with the measured scores and public evidence. Keep published editions intact. See the [submission guide](https://github.com/lexmount/webstone-site/blob/main/docs/submitting-results.md) for the format, version rules, and post-merge checks.

## Project links

[Dependencies and installation](docs/dependencies.md) · [Datasets and custom tasks](docs/benchmarks.md) · [Adapter contract](docs/provider-contract.md) · [Architecture](docs/architecture.md) · [Versions and releases](docs/releases.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [License](LICENSE)

## Cite Webstone

[CITATION.cff](CITATION.cff)

```bibtex
@software{webstone_2026,
  author = {{Webstone contributors}},
  title = {Webstone: Benchmarking Browsers and Web Tools for AI Agents},
  note = {Evaluation harness},
  year = {2026},
  url = {https://github.com/lexmount/webstone}
}
```
