# Agent fetch backend experiment

Compare the fetch backend behind Claude Code's `web-fetch` subagent: the built-in WebFetch against Lexmount `POST /v1/extract`. The experiment runs the real `claude` CLI as a subprocess, so it works from any shell, CI job or session and never reads the session that starts it.

## What is compared

Claude Code delegates page reading to a `web-fetch` subagent. Both arms keep that chain and change only the subagent's fetch tool.

| Arm | Subagent fetch tool |
| --- | --- |
| `builtin` | Claude Code's built-in WebFetch |
| `lexmount` | `web_fetch` MCP tool that calls Lexmount `/v1/extract` (`agent_backend/lexmount_mcp.py`) |

The main agent can only call the Agent tool, so every request goes through the subagent. Main-session figures are recorded but never compared. Each task and arm starts a new `claude -p` process with no saved session, an empty working directory, no user instructions, memory or skills, and MCP servers only from the run's own file. Runs are serial and the arm order rotates per task.

## Prerequisites

- `claude` on `PATH` (or `CLAUDE_BIN`), signed in.
- `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID` in the environment; `LEXMOUNT_BASE_URL` is optional (default `https://api.lexmount.com`). Claude Code passes its environment to the MCP server, so no secret is written to disk.
- The executable Web Fetch dataset with reference texts, resolved from `evaluation/dataset.json` as for `webstone run`, or `--dataset /absolute/path/dataset.json`.

## Commands

```sh
uv run --frozen python scripts/agent_fetch_experiment.py check            # prerequisites; two tiny live calls
uv run --frozen python scripts/agent_fetch_experiment.py plan  --limit 20 # write plan.json only
uv run --frozen python scripts/agent_fetch_experiment.py run   --limit 20 --include static-simple --run-id my-pilot
uv run --frozen python scripts/agent_fetch_experiment.py resume --run <run dir>
uv run --frozen python scripts/agent_fetch_experiment.py score  --run <run dir>   # no provider or model calls
```

Useful options: `--reps N`, `--exclude id,id` (recorded in `plan.json`), `--case-ids id,id`, `--seed`, `--model`, `--price-per-1000 0.75`. Results go to `<data home>/experiments/agent-fetch/<run-id>/` (`WEBSTONE_DATA_HOME` or `~/.webstone`), never into Git.

## Metrics

Per run: subagent tokens and duration exactly as Claude Code reports them (a run without the report is invalid, never filled in), fetch tool duration from the tool dispatch log (the same measurement point in every arm), backend calls, the route taken, and the fetch tool's raw result.

The pass verdict is the benchmark's own: the content the fetch tool returned is scored by the benchmark scorer against the task's acceptance rules. The agent's final answer is saved as evidence but never scored, because the benchmark has no annotation for rewritten text. Fetch API cost is calls × `--price-per-1000` / 1000; the subagent model cost is the haiku usage cost and includes small auxiliary calls of the main session, so it is an upper bound.

## Retry and exclusion

Each run gets one declared retry (`--max-attempts 2`) after a protocol violation or a failed fetch call, with the same rule for every arm. The last attempt is the only result; earlier attempts stay on disk and appear separately as retry cost. A run that is still invalid after the retry is excluded, for every arm, from the comparison of its task. The arms of a task run at the same time in separate processes (`--serial-arms` to disable).

## Controls

`score` and `check` run two controls on every selected task: empty text must fail, and the task's own reference text should pass. A reference that fails because a forbidden marker occurs inside it cannot be satisfied by any complete answer; such tasks are excluded from pass rates and listed. A reference that fails for another reason (plain-text reference with code split across lines) is reported as "control not applicable" and stays in the pass rates. The tool does not modify task definitions; repair them in the dataset.

## Add a backend

Add an entry to `ARMS` in `agent_backend/arms.py` and, for a backend that is not built in, an MCP server that exposes one fetch tool. The tests in `tests/webfetch/test_agent_backend_experiment.py` run offline with a fake `claude`.
