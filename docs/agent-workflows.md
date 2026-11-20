# Agent workflows

Use the [Webstone evaluation skill](../.agents/skills/webstone/SKILL.md) for the recommended workflow by Track. It covers choosing a benchmark and participants, preparing the environment, collecting and scoring evidence, and delivering the requested output.

## Install

Use a Python 3.11+ virtual environment. Install the CLI and copy the bundled skill to your agent's skills directory. This example uses Codex.

```sh
pip install --upgrade webstone
webstone skills install --directory "$HOME/.codex/skills"
```

The installer leaves an existing installation intact. To use the skill from source when your package lacks the installer, run:

```sh
git clone https://github.com/lexmount/webstone.git
cd webstone
uv sync --frozen
uv run --frozen webstone skills install --directory "$HOME/.codex/skills"
```

Some track stages need a source checkout, optional extras or external sites and credentials. Use the source tag matching your installed version when following its track guides. See [dependency options](dependencies.md#install-webstone).

## CLI entry points

| Task | Start here |
| --- | --- |
| Choose a Track or benchmark | `webstone tracks`, `webstone datasets list` |
| Restore pinned inputs | `webstone datasets fetch --help` |
| Web Fetch | `webstone validate`, `webstone run`, `webstone rescore`, `webstone report`. Find specialized cohort commands in `webstone --help`. |
| Web Search | `webstone web-search --help` |
| Web Extract | `webstone web-extract --help` |
| Browser Trajectory Replay | `webstone trajectory-replay --help` |
| Browser Use (experimental) | `webstone browser-use --help` |
| Propose or adopt a dataset revision | `webstone datasets propose --help`, `webstone datasets pin --help` |

Try the [offline quickstart](../README.md#quickstart) before setting up a live run. For reports, use the selected Track's rebuild and publication commands. See the [report guide](report-hosting.md) for sharing.

## Submit measured results

Open a result PR in [webstone-site](https://github.com/lexmount/webstone-site) using its [submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md). Git, GitHub and Node.js are enough. Existing integrations using `webstone results` can refer to the [CLI reference](result-submissions.md).
