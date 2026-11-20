---
name: webstone
description: Plan and run Webstone evaluations. Use to choose a Track, benchmark and participants, prepare the environment, collect and score evidence, build a report, and hand off optional sharing or leaderboard submission.
---

# Webstone evaluations

## Choose the run

1. Find out what the user wants to compare and what they need back. If they already have scores for the leaderboard, hand off to the [submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md). If they have saved attempts, start at scoring or report building.
2. Use `webstone tracks` and `webstone datasets list` to find the guides and pinned datasets. Select the benchmark and suite, then pin each participant's browser, tool, model and configuration as applicable. Use the [workflow guide](https://github.com/lexmount/webstone/blob/main/docs/agent-workflows.md) for installation and command discovery. When working from a checkout, read its AGENTS.md and benchmark-report skill. Use the matching software release checkout for source-only stages.
3. Before the full run, confirm the references and scorer, environment, timeouts, retries, concurrency and call budget. Run a small sample from the actual runner to check access, scoring and evidence capture. Reuse compatible evidence on resume and store new evidence outside Git under `WEBSTONE_DATA_HOME` or `~/.webstone`.

## Prepare and run by Track

| Track | Recommended workflow |
| --- | --- |
| Web Fetch | Choose live URLs, frozen pages or the offline fixture. Follow the [Fetch guide](https://github.com/lexmount/webstone/blob/main/src/harness/web_fetch/README.md) for tools, credentials and scope. For a new live run, ask for full or main content if the user hasn't chosen (full for unattended runs). Validate a sample, fetch with each configured method, then score the saved responses against the selected references. |
| Web Search | Pin the retrieval dataset and output layer, then configure the selected providers and credentials as in the [Search guide](https://github.com/lexmount/webstone/blob/main/src/harness/web_search/README.md). Preflight the selected profile, issue the same queries for each provider and score each selected output layer separately from saved responses. |
| Web Extract | Pin the inputs and field schema. For frozen pages used by remote methods, verify a matching public snapshot host, such as Cloudflare Pages or an existing HTTPS host, from the provider's network. Use original URLs for live-page suites. Follow the [Extract guide](https://github.com/lexmount/webstone/blob/main/examples/web_extract/README.md) to prepare inputs, plan and preflight, collect tool outputs, then score the extracted fields. |
| Browser Trajectory Replay | Select the recorded suite and browser versions. For WebArena-Verified, prepare reachable sites and test accounts, reset writable sites and check baseline readiness before replay. Use the guide for [WebArena](https://github.com/lexmount/webstone/blob/main/docs/webarena-fixed-trajectory-reproduction.md), [curated replay](https://github.com/lexmount/webstone/blob/main/examples/trajectory_replay/README.md), or [Headless Browser](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser#execute-with-the-original-runner). Replay the same actions in each browser and score the recorded outcome with the suite's evaluator. |
| Browser Use (experimental) | Select the task suite, agent, model and browser. Prepare isolated target sites with the accounts and evaluator in the [Browser Use guide](https://github.com/lexmount/webstone/blob/main/examples/browser_use/README.md). Preflight logins and evaluation, reset task state, let each agent choose its actions, then score its outcome. |

Use the selected guide and `--help` for the actual commands and required extras. Keep raw responses or traces, verdicts and measurements tied to the exact run. Preserve timing and billing units, and record missing cost as unavailable. Re-score saved attempts when only the scorer changes. Run independent review only when requested.

## Deliver what was requested

- **Local report.** Build or reuse the Track's report and check the summary and per-task evidence. Use saved attempts to rebuild. Return the report path and the scope actually evaluated.
- **Share a report.** When requested and authorized, use the report's configured host and publication command. Verify the published link and its access before returning it.
- **Submit leaderboard scores.** When requested, hand off measured scores, exact versions, methodology and public evidence to the [submission skill](https://github.com/lexmount/webstone-site/blob/main/.agents/skills/submit-webstone-result/SKILL.md). It owns result formatting, versioning, review and the GitHub PR. Submission works independently of the Webstone CLI.
- **Update a dataset.** Follow the [dataset revision guide](https://github.com/lexmount/webstone/blob/main/docs/benchmarks.md#historical-bindings-and-new-revisions). Propose a Hugging Face Dataset PR, then update the Webstone pin in a separate code PR after the dataset change is reviewed and merged.
