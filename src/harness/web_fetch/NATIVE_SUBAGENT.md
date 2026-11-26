# Native Codex subagent collection

The native single-fetch and autonomous multiple-fetch methods use the Codex
agent's built-in `collaboration.spawn_agent` capability. Each case starts with
`fork_turns: "none"`. Native dispatch is performed by the parent agent using that
tool. Capture and offline trajectory projections live in
`harness.web_fetch.native_subagent`; scoring and reporting keep their existing
Web Fetch owners.

## Inputs and dispatch

Keep the case roster, prompts, acquisition contract, execution plan, source
hashes and publication profile outside Git under `WEBSTONE_DATA_HOME`. Freeze
the selected dataset and scorer before collection. Follow
[`docs/benchmark-execution.md`](../../../docs/benchmark-execution.md) for preflight,
resource admission and resume. The parent must own one admission scope, count
every dispatched unfinished child against `max_in_flight`, and interrupt expired
children before freeing their slots. Resume from retained dispatch and terminal
receipts before selecting another case. A tool timeout does not release a slot
while its child remains active.

Pass the frozen prompt plus only the target URL and a unique delivery path to
each child. Reference answers, acceptance contracts and sibling results stay in
the parent. Use a new task name and directory for each physical call; an
unfinished reservation is reconciled before reuse. Never start an unrecorded
replacement because an answer is missing or a score is low.

The parent supplies these arguments directly to `collaboration.spawn_agent`:

```javascript
{
  task_name: slot,
  fork_turns: "none",
  model: contract.model,
  reasoning_effort: contract.reasoning_effort,
  message: contract.prompt_template + "\nURL: " + targetUrl
    + "\n" + contract.delivery_label + ": " + answerPath
}
```

The prompt and delivery label are external frozen inputs, including their
original language. Keep their hashes in the contract. Set the fetch budget to
one for single-fetch and to unlimited for autonomous multiple-fetch. The latter
leaves the fetch tools and methods to each child. The historical prompts
prohibited mixing
Browser Use click, scroll, input and other interactive actions into fetching.
That historical policy is not a new requirement: for unrestricted native fetch,
freeze only the requested single/unlimited budget and delivery boundary. Do not
force `openPage`, a particular route or the historical browser restriction.
The child chooses retrieval tools and parameters from receipt metadata. Keep
every fetched body in the execution runtime and save it there; return only
status, resource handles and coverage metadata needed to choose another fetch.
Fetched bodies must never be printed into the child context, including
intermediate returns. Tool choice remains unrestricted by the capture layer.
The common initial tool manifest belongs in the frozen runtime context. Keep
any execution-side discovery inside the pending fetch invocation; do not print
full tool catalogs, unrelated capabilities or helper source into the child.
This does not restrict the selected fetch tool or its native internal actions.
The original comparison used a 300-second case deadline, zero retries, two
concurrent children and full-content scoring. These are instance settings.

Retain the parent spawn call and child ID, raw child trajectory, delivery bytes,
terminal state, actual duration and final cumulative token receipt.
`dispatch_checks` compares retained spawn arguments against the exact expected
message, model, effort, task name and clean-history setting. It returns `None`
for plaintext equality when the parent log contains an encrypted message;
other checks still run. A false result fails the dispatch check. An unknown
result remains unknown in the retained proof.

## Single-fetch native capture

The original single-fetch collection saved the untouched native tool return
without asking the child to rewrite its text. Load the following capture
functions execution-side in the child's
`functions.exec` invocation. The helper source stays in the execution runtime;
do not print it to the child or transcribe it into another model-generated call.
Bind `executionRoot`,
`directory` and `fetchArguments` from the admitted contract before use. The
directory must already exist and belong exclusively to that case.

```javascript
const helper = await tools.exec_command({
  cmd: "uv run --frozen --no-cache --no-sync python -m harness.web_fetch.native_subagent --capture-script",
  workdir: executionRoot, max_output_tokens: 4000
});
if (helper.exit_code !== 0) throw new Error("Capture helper unavailable");
const {captureNative} = new Function(
  "tools", "notify", "executionRoot",
  helper.output + "\nreturn {captureNative};"
)(tools, notify, executionRoot);
```

Keep helper loading and use in the same `functions.exec` invocation. This
runtime discards local variables between invocations. The child chooses and
completes its retrieval steps before deciding what to deliver. Keep the selected
actual delivery in the execution runtime, for example with
`store("final_delivery", value)`. Then save it once:

```javascript
await captureNative(directory, chosenToolName, chosenArguments,
  load("final_delivery"));
```

This save example prescribes no acquisition tool, route or number of fetches.
The fetch budget belongs to the admitted contract. The wrapper awaits preparation
and persistence; do not omit the outer `await`. Saving an already retained value
performs no new fetch and leaves `fetch_ms` unavailable. The helper also supports
an acquisition callback, but that shortcut must not become the default retrieval
strategy in an autonomous collection prompt. The wrapper emits only the save
receipt. End the child after delivery and retain its actual terminal state.
Exclusive save claims, raw JSON, ordered text, binary blocks, hashes and timers
keep their existing owner.

Autonomous multiple-fetch children may inspect intermediate receipt metadata
and choose how to fetch. Wrap each chosen acquisition and save in the same
execution-side invocation so its body never enters a model tool result.
Once the final delivery is saved, return only a small save receipt
(status, path, byte count and hash). Never read the delivered body back for
confirmation or print it again. The same rule applies to single-fetch.
The final save must use `await captureNative(...)` above (with a string delivery or the
untouched selected native envelope); its execution-side writer returns only the
receipt. Do not append `cat`, `head`, `tail`, previews or model summaries to that
save. File writing runs in the execution layer; the model need not reproduce
bytes already held there.
Use the same capture wrapper in a distinct per-call directory when preserving
multiple raw returns. Preserve the selected delivery and all raw calls; leave
absent delivery absent. Capture does not transform a model-written delivery into
a synthetic native response.

Do not add child progress messages, process polling or a post-save sleep. A
pending execution is awaited by the runtime/parent, without a new child model
turn to ask whether it finished. After the save receipt, end the child; retain
any unavoidable terminal status separately. The parent reclaims the child slot
only after its terminal state is observed. Do not instruct the child to stay
pending until interrupted just to suppress a final status.

Freeze the delivery boundary before calls: session start through the execution
layer's successful save receipt is delivery latency; session start through
termination is total lifecycle latency. Keep the post-delivery interval separate.
`save_native_response` now retains `saved_at` in its receipt. `delivery_timing`
projects these intervals and leaves a missing receipt unavailable. Historical
receipts without timestamps may use a hash-bound successful save-command
completion from the runtime trace, labeled as that boundary; never substitute
filesystem mtime or the parent's next poll. Keep the original timing unchanged.
Retain all actual child tokens. Do not subtract estimated confirmation/polling
cost to present a contaminated execution as a clean one.

## Offline audit, usage and scoring

Read the saved JSONL trajectory and first verify its SHA256 against the terminal
receipt. Extract `response_item` payloads of type `function_call` or
`custom_tool_call` for `assess_protocol`. This preserves namespaces when the
short tool name is just `js`, separates inventory probes from browser-control
attempts and identifies Lexmount tool attempts. Retain the flags with their raw
call IDs; do not remove flagged cases or change their answers.

`audit_single_open` checks the original native open routes using runtime
`item_completed` events. An MCP call with its nested native extension is one
logical invocation. It rejects multiple opens, changed URLs and unexpected
request arguments. Mixed MCP/extension events require an explicit `parent_id`
association; matching URL text alone does not establish nesting. This historical
open-route audit is not a restriction on future native fetch tool selection.
Retain and inspect shell commands separately.

`trace_telemetry` projects whole-tool call durations and the last cumulative
`token_count.info.total_token_usage`, leaving unmatched durations and missing
usage unavailable. It does not sum intermediate cumulative receipts. Store the
returned `terminal_usage` in result `provider_metadata`; the existing
`report_usage.token_usage` validates and aggregates it. Cached input is already
part of input, and reasoning output is already part of output.

Before admitting a preflight or scoring a receipt-only cohort, run
`receipt_only_trace_issues` on each complete child trace. It checks model-visible
tool outputs and assistant messages, without restricting the chosen fetch tools.
Reject nonreceipt outputs, unmatched calls and nonempty assistant messages from
clean token comparisons. Keep their original attempts and actual token receipts;
do not subtract estimated overhead or silently retry them. Inspect nested calls
and retained file hashes separately: a clean visible trace alone does not prove
the selected program, single-fetch budget or saved artifact is correct.

Score the preserved delivery with the frozen `lexbench_webfetch.scoring.score_attempt`
and `lexbench_webfetch.metrics.task_passed` / `task_measured` APIs. Bind the dataset,
reference and scorer hashes; retain missing-file and deadline states in the
record. The scorer receives full scope and the actual `ok` state. Reuse the
saved-matrix composition, report checks and package/publish commands documented
in [README.md](README.md). The external report profile selects frozen records;
rebuilding a report does not acquire or rescore provider responses.

## Source provenance and offline controls

The capture functions were extracted from the frozen provider source with
SHA256 `d6d198f453bb02ab43413b67355e3885047d63eebfac189103da14865b8abfdb`.
The open audit and namespace-aware protocol audit were moved from their
run-local scripts into parameterized functions. The telemetry projection keeps
the existing whole-call timing and cumulative-usage conventions. Original
source snapshots remain bound by each external experiment contract.

Run the synthetic capture, dispatch, audit and usage controls without any
provider calls:

```sh
uv run --frozen python -m pytest tests/benchmarks/webfetch/test_native_subagent.py
```

## Metric meanings

Keep task acceptance separate from delivery completion and origin HTTP success.
A completed child with a saved error explanation establishes a delivery, not a
successful HTTP request. Missing HTTP status stays unavailable. Do not turn
`terminal_state == completed` or a save receipt into an HTTP-success claim.
The existing saved report's `ok`/normal-return metric retains its adapter-defined
meaning; it does not measure origin HTTP success for these native children.

Saved native deliveries use `project_native_delivery` for offline answer selection.
It accepts the capture writer's text/command/MCP answer fields and original binary
blocks. It removes recognized numbered web-tool framing, including a serialized
web-text return, but preserves plain selected text. Do not apply the historical
single-open-only `_project_open_outputs` reader to arbitrary multiple-fetch
deliveries: valid retained text need not carry numbered web lines. Bind the
projection source hash and executable `native-delivery-*` controls in correction
receipts; preserve the prior raw outputs and scores.
