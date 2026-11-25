# Agent fetch backend experiment

Compare ways of retrieving the page behind Claude Code's `web-fetch` subagent: the built-in WebFetch (a plain HTTP GET converted with Turndown) and a local moli binary (a headless browser engine). and Lexmount `POST /v1/extract` (a hosted extraction service). The default is all three (`--arms builtin,moli,lexmount`). The experiment runs the real `claude` CLI as a subprocess, so it works from any shell, CI job or session and never reads the session that starts it.

## What is compared

Claude Code delegates page reading to its stock `web-fetch` subagent, which calls the stock WebFetch tool. Every arm runs exactly that chain with the same command, flags, prompt and subagent. Only one step differs: how WebFetch retrieves the page.

| Arm | How WebFetch gets the page | Text WebFetch receives |
| --- | --- | --- |
| `builtin` | itself, from this machine (Anthropic domain check, HTTP GET, first 1 MiB of HTML converted to Markdown) | its own Markdown |
| `lexmount` | the `web_fetch` MCP tool → Lexmount `/v1/extract` | Lexmount's official agent Markdown (metadata, extraction quality, description, main text) |
| `moli` | the `web_fetch` MCP tool → local `moli fetch` | moli's standard output; for binary output a note, with the bytes saved as a file |

Claude Code's `CLAUDE_CODE_WEBFETCH_MCP_TOOL` setting (`{"server": ..., "tool": "web_fetch"}`) makes WebFetch retrieve pages through the named MCP tool; the model never sees that tool. Everything after retrieval is WebFetch's own and identical in every arm: the result wrapper and rules, the verbatim window (about 48,000 characters minus the URL line; above it, the first part verbatim and a model-written summary of up to 100,000 further characters), binary handling, and the subagent's report. This was checked on Claude Code 2.1.330 with an MCP tool returning 122,906 and 616,406 characters: WebFetch showed the first 39,966 verbatim and summarized the rest, as it does for a page it fetches itself.

The Lexmount text is rendered by Lexmount's official `webfetch-cli` (`LEXMOUNT_WEBFETCH_CLI`): the MCP server calls the API once with the harness's parameters and replays that response to the CLI on a local address, so the format is Lexmount's own and the API is not called twice. The MCP server (`agent_backend/fetch_mcp.py`, standard library only) also saves each backend's complete output as evidence.

Every arm gets the same task: what is wanted is at the end of the page, so the subagent has to notice that characters remain and keep reading until it reaches the last one (`--prompt`, recorded in `plan.json`):

```text
Read the complete text of {url}. What we need is at the end of the page. The fetch tool shows a long page in parts: when a result says the verbatim text stops at character N of a longer page, call the tool again with the same url and offset N, and repeat until you have read the page to its end. Make a single attempt per part; if a fetch fails, say so instead of trying other ways. Then reply with three lines: how many characters the page has and whether you read all of it; the last sentence of the page's main content (the article or body text, not navigation, related links or footer), copied exactly; the last 80 characters of the page text, copied exactly.
```

Two targets are checked. The page's own last characters differ between arms (each backend turns the page into its own text: Lexmount returns extracted main text, the others the whole page), so they measure only whether the subagent kept reading. The end of the page's main content is the same target in every arm: the last line of the task's human-reviewed `ground_truth_main_content` (275 of the 530 tasks have one). On the first 530-task run this check found the ending in every built-in output that passed the benchmark and was delivered whole (83 of 83), 129 of 134 for moli and 54 of 63 for Lexmount; the misses were real differences (a changed commit id on a live page, a line the extraction dropped).

A page that fits one call needs no further call. The stock subagent has at most 15 turns; when one subagent runs out on a very long page the main session may delegate again to read on. That is Claude Code's own behaviour and the same in every arm, so several launches are allowed and counted, provided each reports its usage.

WebFetch keeps the page text it retrieved in a per-process cache (15 minutes) and `offset` is a character position in that text, so reading on never contacts the site again and works the same whoever retrieved the page: the backend is called once per page. WebFetch only suggests an offset for text beyond what it summarized (about 140,000 characters in); the task therefore states the rule for the stretch in between.

Each task starts one `claude -p` process per arm, at the same time, each with its own working directory (empty), MCP configuration and no saved session, user instructions, memory or skills. Tasks run one after another by default (`--parallel-tasks N` runs N at once); the arms of a task always share the same time window. The arm order inside a task rotates. `CLAUDE_CODE_WEB_FETCH_AGENT=1` and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` are set in every arm: the first pins Claude Code's web-fetch-agent mode, which otherwise follows a remotely served feature flag (when it is off the main agent calls WebFetch itself, and WebFetch answers with a small model instead of returning the page); a background subagent gives no usage report. The task text is `--prompt` (default below), recorded in `plan.json`.

Differences that remain are the backends' own and are the point of the comparison: where the request leaves from (Lexmount's servers, or this machine), the built-in domain check, and how each backend turns a page into text, including how long that text is and so how much falls outside WebFetch's verbatim window.

## Parameters

Parameters are generated per task family by `backend_params.py`, never per task, and match the Webstone provider configuration. moli: `fetch --dump markdown --wait-until done|domstable --block-private-networks` (the provider's native command). Lexmount: dynamic tasks request `engine_preference: lightmount_domstable`, other tasks send only the time limit; every request sets the top-level `use_proxy: true` (fetch through the Lexmount proxy). The built-in tool has no request parameters. Document tasks carry no input media declaration in the current edition, so moli's document family uses its automatic output (`fetch --wait-until done`), the form its document route uses; direct PDF/DOCX then arrive as original bytes, which, as in the benchmark, cannot pass a body contract on bytes alone. Every task of the edition is run for every arm; none is dropped because a backend handles it badly.

One fetch time limit applies to every arm and every task family: `--fetch-timeout SECONDS`, default the Webstone per-call budget `DEFAULT_CALL_TIMEOUT_SECONDS` (30 s, `harness/web_fetch/config.py`), recorded in `plan.json`. It is the hard stop of one fetch call:

| Arm | How the limit is applied |
| --- | --- |
| every arm | `CLAUDE_CODE_WEBFETCH_DEADLINE_MS` (otherwise WebFetch uses its own deadline: 300 s unless Claude Code's remote configuration sets another) |
| `lexmount` | also: the MCP server stops the request at the limit; `options.timeout_ms` gets the inner budget |
| `moli` | also: the MCP server stops the process at the limit; `--timeout-ms`, `--http-timeout` and `--http-connect-timeout` get the inner budget (otherwise moli's own 25 s readiness deadline) |

The inner budget is the one the Web Fetch providers derive from a call budget (`inner_timeout_budget`: the limit minus 10 %, at least 1 s and at most 5 s), so a backend reports its own timeout before the hard stop. A slower local network is handled by raising the one limit for every arm, for example `--fetch-timeout 45`.

## Prerequisites

- `claude` on `PATH` (or `CLAUDE_BIN`), signed in.
- `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID` in the environment; `LEXMOUNT_BASE_URL` is optional (default `https://api.lexmount.com`). `LEXMOUNT_WEBFETCH_CLI`: Lexmount's official `webfetch-cli` (installed by its Agent Skill), whose version and hash are recorded in `plan.json`. `MOLI_BINARY` for the moli arm.
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

The pass verdict is the benchmark's own `score_attempt` and task predicate, applied to the same thing in every arm: the verbatim page text WebFetch delivered to the subagent, taken from the stream by code. WebFetch's leading line, rules and tags are removed, and so is every model-written summary. Two texts are scored for each run:

| File | What it is | Reported as |
| --- | --- | --- |
| `full_text.txt` | the verbatim parts of every read of the page, joined by offset (`assemble_page`); a stretch no call read verbatim stays missing | `passed`, the primary pass count |
| `markdown.txt` | the first read alone: what one ordinary call delivers (the first 40,000 characters or so of a long page) | `passed_first_read` |

Binary content is scored as the delivered file: the bytes WebFetch saved in the built-in arm (`saved_files/`, listed in Claude Code's harness note) and the bytes moli returned in the moli arm. Nothing a model wrote is scored, and the harness never extracts text from files or converts formats.

Scoring goes through the benchmark's public boundary `lexbench_webfetch.evaluation.evaluate_response`. The empty-output control must fail every selected task. The backends' complete outputs saved by the MCP server are diagnostic evidence; they are not scored.

Reading to the end is measured by code from the calls, never from what the model says it did:

| Field | Meaning |
| --- | --- |
| `page_chars` | the page length WebFetch stated ("N of T characters"); for a page that fits one call, the length of that call's text. Lengths and offsets are UTF-16 units, as WebFetch (JavaScript) counts them |
| `covered_chars` | characters delivered verbatim, from the offsets and lengths of the calls (overlaps counted once) |
| `reached_end` | a verbatim part ends at the page's last character |
| `read_complete` | `reached_end` and no gap: every character was delivered verbatim |
| `tail_quoted` | `reached_end`, and the subagent's report (the text it handed back, taken from the Agent result) contains the page's real ending: its last 24 letters and digits, compared without punctuation, spacing or case, with link and image markup either copied or rendered as its text |
| `content_end_delivered` | the end of the main-content reference is in the verbatim text delivered (all reads joined); `content_end_first_read`: in the first read alone |
| `content_end_reported` | the end of the main-content reference is in the subagent's report |
| `assembly_matches_backend` | moli and Lexmount arms: the text joined by offset equals the backend's own complete output, a check of the assembly |

`score` recomputes all of these, and each run's validity, from the saved stream with the current rules, so a corrected rule applies to finished runs without running them again. `summary.json` reports them per arm under `tail` and `content_end` (with `paired_content_end` per pair of arms), on every fetched page and on pages longer than one call (`long_pages`, where reading on is required), with fetch calls, page length, tokens and duration on those pages. `long_for_every_arm` repeats them on the tasks whose page was longer than one call in every arm, the like-for-like view of the reading question. Page lengths differ between arms because each backend turns the page into its own text; a longer text needs more calls, and that cost belongs to the backend.

## Metrics

The primary time metric is the subagent's duration, from receiving the task to delivering its report, as Claude Code reports it; the fetch tool's own duration is reported beside it. The subagent reads what WebFetch delivered, so its tokens grow with the delivered text in every arm, and every further read sends the earlier context again. Compare cost only where both arms passed, by page length. `summary.json` gives subagent tokens over all valid runs, over each arm's fetched runs (`subagent_tokens_fetched`), and, as a diagnostic, over the tasks every arm fetched (`fetched_by_every_arm`; selected after the outcome, it never ranks the arms).

Per run: subagent tokens and duration exactly as Claude Code reports them (a run without the report is invalid, never filled in), fetch tool duration from the tool dispatch log (the same measurement point in every arm), backend calls, the route taken, and the fetch tool's raw result. Fetch API cost is calls × `--price-per-1000` / 1000; the subagent model cost is the haiku usage cost and includes small auxiliary calls of the main session, so it is an upper bound. `summary.json` also carries pass counts per task category.

## Retry and exclusion

`--retries N` (0 or 1, default 0) gives each run N declared retries after a protocol violation or a failed fetch call, with the same rule for every arm. The last attempt is the only result; earlier attempts stay on disk and are not part of the summary. A run is invalid when the subagent was not launched exactly once, there is no usage report, the expected fetch tool was not called or the process failed. An invalid final run counts as not passed for its arm; the task stays in every arm's denominator. Usage and timing statistics use valid runs only and report their count.

When Claude Code itself cannot serve a request (account usage limit, rate limit, overload) the run stops, deletes that task's attempts for every arm, and exits with code 3; `resume` repeats the task later.

## Add a backend

Add an entry to `ARMS` in `agent_backend/arms.py` and a branch in `fetch_mcp.py`. The tests in `tests/webfetch/test_agent_backend_experiment.py` run offline with a fake `claude`.
