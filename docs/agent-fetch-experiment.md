# Agent fetch backend experiment

Compare the fetch backend behind Claude Code's `web-fetch` subagent: the built-in WebFetch, Lexmount `POST /v1/extract` and a local moli binary. The experiment runs the real `claude` CLI as a subprocess, so it works from any shell, CI job or session and never reads the session that starts it.

## What is compared

Claude Code delegates page reading to a `web-fetch` subagent (model haiku). All arms keep that chain and change only the subagent's fetch tool.

| Arm | Subagent fetch tool | Where the page is fetched |
| --- | --- | --- |
| `builtin` | Claude Code's built-in WebFetch | this machine, inside Claude Code |
| `lexmount` | `web_fetch` MCP tool → Lexmount `/v1/extract`: `main_text` with the returned images and links | Lexmount service |
| `moli` | `web_fetch` MCP tool → local `moli fetch`; binary output is delivered as a file | this machine, moli subprocess |

The MCP tool is `agent_backend/fetch_mcp.py` (standard library only). The harness gives it the request parameters, so the subagent passes only a URL. The main agent can only call the Agent tool, so every request goes through the subagent; main-session figures are recorded but never compared.

Each task starts one `claude -p` process per arm, at the same time, each with its own working directory (empty), MCP server and no saved session, user instructions, memory or skills. Tasks run one after another by default (`--parallel-tasks N` runs N at once); the arms of a task always share the same time window. The arm order inside a task rotates.

Fixed environment (recorded in `plan.json`): `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` (a background subagent gives no usage report) and `MAX_MCP_OUTPUT_TOKENS=150000`; the MCP tool also declares `anthropic/maxResultSizeChars`. Without them Claude Code replaces a large MCP result with a pointer to a saved file and the main agent launches a second subagent to read it, which breaks the one-subagent protocol exactly for long pages.

## Parameters

Parameters are generated per task family by `backend_params.py`, never per task, and match the Webstone provider configuration. moli: `fetch --dump markdown --wait-until done|domstable --block-private-networks`; the readiness deadline is moli's native default (25 s). Lexmount: dynamic tasks request `engine_preference: lightmount_domstable` with the family timeout (45 s caller budget), other tasks send no options. The built-in tool has no parameters. Document tasks carry no input media declaration in the current edition, so moli's document family uses its automatic output (`fetch --wait-until done`), the form its document route uses; direct PDF/DOCX then arrive as original bytes, which, as in the benchmark, cannot pass a body contract on bytes alone. Every task of the edition is run for every arm; none is dropped because a backend handles it badly.

## Prerequisites

- `claude` on `PATH` (or `CLAUDE_BIN`), signed in.
- `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID` in the environment; `LEXMOUNT_BASE_URL` is optional (default `https://api.lexmount.com`). `MOLI_BINARY` for the moli arm.
- The pinned LexBench-Web-Fetch snapshot (public definitions for planning, the scorable materials for scoring).

## Commands

```sh
uv run --frozen python scripts/agent_fetch_experiment.py check
uv run --frozen python scripts/agent_fetch_experiment.py plan  --per-suite 5          # write plan.json only
uv run --frozen python scripts/agent_fetch_experiment.py run   --per-suite 5 --retries 1 --run-id my-run
uv run --frozen python scripts/agent_fetch_experiment.py run   --limit 1000 --retries 1 --run-id full   # every task
uv run --frozen python scripts/agent_fetch_experiment.py resume --run <run dir>
uv run --frozen python scripts/agent_fetch_experiment.py score  --run <run dir>       # no provider or model calls
```

`--per-suite N` takes N tasks per dataset suite; `--limit` caps the total (use a large value for every eligible task). Other options: `--reps`, `--exclude`, `--case-ids`, `--seed`, `--model`, `--price-per-1000`. Results go to `<data home>/experiments/agent-fetch/<run-id>/` (`WEBSTONE_DATA_HOME` or `~/.webstone`), never into Git. `plan.json` records the Claude Code, moli and Webstone versions and the dataset commit.

## What is scored

The pass verdict is the benchmark's own `score_attempt` and task predicate applied to the raw markdown the fetch tool returned, never to the subagent's report or the main agent's answer (the benchmark has no annotation for rewritten text).

| Arm | Scored text |
| --- | --- |
| `builtin` | the tool result in the stream, without the tool's leading rules and tag (the model-written summary after a long page's verbatim part is dropped), plus the original bytes WebFetch saved for binary content (`saved_files/`, listed in Claude Code's harness note) |
| `lexmount` | the API response saved by the MCP server, projected exactly as the benchmark projects a Lexmount call: `main_text` declared plain text, title, final URL, links and images |
| `moli` | its standard output saved by the MCP server; binary output (an image, a PDF) as a delivered file |

Scoring goes through the benchmark's public boundary `lexbench_webfetch.evaluation.evaluate_response`; the harness never extracts text from delivered files or converts formats. The empty-output control must fail every selected task. A task's own reference text is not used as a control: many tasks have none, and the acceptance rules are defined on the whole page.

## Metrics

Per run: subagent tokens and duration exactly as Claude Code reports them (a run without the report is invalid, never filled in), fetch tool duration from the tool dispatch log (the same measurement point in every arm), backend calls, the route taken, and the fetch tool's raw result. Fetch API cost is calls × `--price-per-1000` / 1000; the subagent model cost is the haiku usage cost and includes small auxiliary calls of the main session, so it is an upper bound. `summary.json` also carries pass counts per task category.

## Retry and exclusion

`--retries N` (0 or 1, default 0) gives each run N declared retries after a protocol violation or a failed fetch call, with the same rule for every arm. The last attempt is the only result; earlier attempts stay on disk and are not part of the summary. A run is invalid when the subagent was not launched exactly once, there is no usage report, the expected fetch tool was not called or the process failed. An invalid final run counts as not passed for its arm; the task stays in every arm's denominator. Usage and timing statistics use valid runs only and report their count.

When Claude Code itself cannot serve a request (account usage limit, rate limit, overload) the run stops, deletes that task's attempts for every arm, and exits with code 3; `resume` repeats the task later.

## Add a backend

Add an entry to `ARMS` in `agent_backend/arms.py` and, for a backend that is not built in, a branch in `fetch_mcp.py`. The tests in `tests/webfetch/test_agent_backend_experiment.py` run offline with a fake `claude`.
