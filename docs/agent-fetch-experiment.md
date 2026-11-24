# Agent fetch backend experiment

Compare the fetch backend behind Claude Code's `web-fetch` subagent: the built-in WebFetch, Lexmount `POST /v1/extract` and a local moli binary. The experiment runs the real `claude` CLI as a subprocess, so it works from any shell, CI job or session and never reads the session that starts it.

## What is compared

Claude Code delegates page reading to its stock `web-fetch` subagent, which calls the stock WebFetch tool. Every arm runs exactly that chain with the same command, flags, prompt and subagent; only the way WebFetch retrieves the page differs.

| Arm | How WebFetch gets the page | What the subagent receives |
| --- | --- | --- |
| `builtin` | itself, from this machine | WebFetch's normal result (the page text) |
| `lexmount` | the `web_fetch` MCP tool → Lexmount `/v1/extract` | a fixed one-line note; the page is saved to a file by code |
| `moli` | the `web_fetch` MCP tool → local `moli fetch` | the same note; text or binary output is saved to a file by code |

Claude Code's `CLAUDE_CODE_WEBFETCH_MCP_TOOL` setting (`{"server": ..., "tool": "web_fetch"}`) makes WebFetch retrieve pages through the named MCP tool; the model never sees that tool. The MCP server (`agent_backend/fetch_mcp.py`, standard library only) saves the backend's complete output and returns `NOTE`, so in these arms the page never enters a model's context and the subagent's tokens do not depend on page size. The built-in arm is left as Claude Code runs it: WebFetch hands the page text to the subagent. Its scored output is taken from the stream by code.

The task asks for the fetch status only, the same words in every arm, so no arm's report depends on page content:

```text
Fetch {url} once. Do not summarize, quote or describe the page. Reply with one line: whether the fetch succeeded, and if it failed, the HTTP status or error.
```

Each task starts one `claude -p` process per arm, at the same time, each with its own working directory (empty), MCP configuration and no saved session, user instructions, memory or skills. Tasks run one after another by default (`--parallel-tasks N` runs N at once); the arms of a task always share the same time window. The arm order inside a task rotates. `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` is set in every arm (a background subagent gives no usage report).

## Parameters

Parameters are generated per task family by `backend_params.py`, never per task, and match the Webstone provider configuration. moli: `fetch --dump markdown --wait-until done|domstable --block-private-networks` (the provider's native command). Lexmount: dynamic tasks request `engine_preference: lightmount_domstable`, other tasks send only the time limit. The built-in tool has no request parameters. Document tasks carry no input media declaration in the current edition, so moli's document family uses its automatic output (`fetch --wait-until done`), the form its document route uses; direct PDF/DOCX then arrive as original bytes, which, as in the benchmark, cannot pass a body contract on bytes alone. Every task of the edition is run for every arm; none is dropped because a backend handles it badly.

One fetch time limit applies to every arm and every task family: `--fetch-timeout SECONDS`, default the Webstone per-call budget `DEFAULT_CALL_TIMEOUT_SECONDS` (30 s, `harness/web_fetch/config.py`), recorded in `plan.json`. It is the hard stop of one fetch call:

| Arm | How the limit is applied |
| --- | --- |
| every arm | `CLAUDE_CODE_WEBFETCH_DEADLINE_MS` (otherwise WebFetch uses its own deadline: 300 s unless Claude Code's remote configuration sets another) |
| `lexmount` | also: the MCP server stops the request at the limit; `options.timeout_ms` gets the inner budget |
| `moli` | also: the MCP server stops the process at the limit; `--timeout-ms`, `--http-timeout` and `--http-connect-timeout` get the inner budget (otherwise moli's own 25 s readiness deadline) |

The inner budget is the one the Web Fetch providers derive from a call budget (`inner_timeout_budget`: the limit minus 10 %, at least 1 s and at most 5 s), so a backend reports its own timeout before the hard stop. A slower local network is handled by raising the one limit for every arm, for example `--fetch-timeout 45`.

## Prerequisites

- `claude` on `PATH` (or `CLAUDE_BIN`), signed in.
- `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID` in the environment; `LEXMOUNT_BASE_URL` is optional (default `https://api.lexmount.com`). `MOLI_BINARY` for the moli arm.
- The pinned LexBench-Web-Fetch snapshot (public definitions for planning, the scorable materials for scoring).

## Commands

```sh
uv run --frozen python scripts/agent_fetch_experiment.py check
uv run --frozen python scripts/agent_fetch_experiment.py plan  --per-suite 5          # write plan.json only
uv run --frozen python scripts/agent_fetch_experiment.py run   --per-suite 5 --retries 1 --run-id my-run
uv run --frozen python scripts/agent_fetch_experiment.py run   --limit 1000 --retries 1 --fetch-timeout 45 --per-suite-limit 30 --run-id full   # plan every task, run 30 per suite
uv run --frozen python scripts/agent_fetch_experiment.py resume --run <run dir>                            # then the rest
uv run --frozen python scripts/agent_fetch_experiment.py resume --run <run dir>
uv run --frozen python scripts/agent_fetch_experiment.py score  --run <run dir>       # no provider or model calls
```

`--per-suite N` takes N tasks per dataset suite; `--limit` caps the total (use a large value for every eligible task). Other options: `--reps`, `--exclude`, `--case-ids`, `--seed`, `--model`, `--price-per-1000`. Results go to `<data home>/experiments/agent-fetch/<run-id>/` (`WEBSTONE_DATA_HOME` or `~/.webstone`), never into Git. `plan.json` records the Claude Code, moli and Webstone versions and the dataset commit.

## What is scored

The pass verdict is the benchmark's own `score_attempt` and task predicate applied to the raw output each backend delivered, saved by code, never to anything a model wrote.

| Arm | Scored text |
| --- | --- |
| `builtin` | the tool result in the stream, without the tool's leading rules and tag (the model-written summary after a long page's verbatim part is dropped), plus the original bytes WebFetch saved for binary content (`saved_files/`, listed in Claude Code's harness note) |
| `lexmount` | the complete API response saved by the MCP server, projected exactly as the benchmark projects a Lexmount call: `main_text` declared plain text, title, final URL, links and images |
| `moli` | its standard output saved by the MCP server; binary output (an image, a PDF) as a delivered file |

Scoring goes through the benchmark's public boundary `lexbench_webfetch.evaluation.evaluate_response`; the harness never extracts text from delivered files or converts formats. The empty-output control must fail every selected task. A task's own reference text is not used as a control: many tasks have none, and the acceptance rules are defined on the whole page.

## Metrics

The primary time metric is the subagent's duration, from receiving the task to delivering its report, as Claude Code reports it; the fetch tool's own duration is reported beside it. In the built-in arm the page is WebFetch's tool result, so that subagent's tokens grow with the page; in the MCP arms they do not. `summary.json` gives subagent tokens over all valid runs, over each arm's fetched runs (`subagent_tokens_fetched`), and, as a diagnostic, over the tasks every arm fetched (`fetched_by_every_arm`; selected after the outcome, it never ranks the arms).

Per run: subagent tokens and duration exactly as Claude Code reports them (a run without the report is invalid, never filled in), fetch tool duration from the tool dispatch log (the same measurement point in every arm), backend calls, the route taken, and the fetch tool's raw result. Fetch API cost is calls × `--price-per-1000` / 1000; the subagent model cost is the haiku usage cost and includes small auxiliary calls of the main session, so it is an upper bound. `summary.json` also carries pass counts per task category.

## Retry and exclusion

`--retries N` (0 or 1, default 0) gives each run N declared retries after a protocol violation or a failed fetch call, with the same rule for every arm. The last attempt is the only result; earlier attempts stay on disk and are not part of the summary. A run is invalid when the subagent was not launched exactly once, there is no usage report, the expected fetch tool was not called or the process failed. An invalid final run counts as not passed for its arm; the task stays in every arm's denominator. Usage and timing statistics use valid runs only and report their count.

When Claude Code itself cannot serve a request (account usage limit, rate limit, overload) the run stops, deletes that task's attempts for every arm, and exits with code 3; `resume` repeats the task later.

## Add a backend

Add an entry to `ARMS` in `agent_backend/arms.py` and a branch in `fetch_mcp.py`. The tests in `tests/webfetch/test_agent_backend_experiment.py` run offline with a fake `claude`.
