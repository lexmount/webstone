# Repository specification

This page is the standard that Webstone code, documentation and reviews follow.
[Architecture](architecture.md) says where the code is today; this page says what
must hold when it changes. The two agree on ownership, and where existing code
does not yet meet a rule here, [Migration](#migration) applies.

[Model](#model) · [Stages](#stages-and-artifacts) · [One pipeline per track](#one-pipeline-per-track) · [Extension points](#extension-points) · [Report instances](#report-instances) · [Report templates](#report-templates) · [Commands](#command-surface) · [Rules](#rules) · [Where a change goes](#where-a-change-goes) · [Enforcement](#enforcement) · [Migration](#migration)

## Model

Webstone evaluates Web tools along one pipeline. An evaluation is a combination
of four things, and each of them is data or an adapter. None of them is a
branch in shared code.

| Term | Meaning | Examples |
| --- | --- | --- |
| Track | A kind of task with its own pipeline package | Web Fetch, Web Search, Web Extract, Browser Trajectory Replay, Browser Use |
| Benchmark | A task population with its inputs, references and acceptance rules | `content-scopes-530`, WCXB, WebMainBench, SimpleQA Verified, WebArena |
| Method | The thing being measured: a provider, a browser, or a model-by-browser configuration | a hosted fetch API, a browser engine, a Codex CLI mode |
| Report instance | One declared comparison: benchmarks × methods, their versions, scope and publication target | one published report |

The pipeline is shared. A new benchmark, method or report instance adds an
adapter and data. It does not add a runner, a renderer, a checker or a
conditional in a shared module.

Shared code is expected to change. It grows when an instance needs something
the interfaces cannot yet express: a new hook, capability, schema field or
slot that any instance can use. What a shared module must not contain is
knowledge of one instance, such as `if provider == ...`, a benchmark's size or
a report's sentence. The test for a shared-code change is whether it reads the
same with every instance name removed.

**Figure 1. A report instance is data that selects adapters; the pipeline does not change.**

```mermaid
flowchart TB
    subgraph Instance["Report instance (data)"]
        P["Profile<br/>benchmarks, methods, versions,<br/>scope, publication target"]
        C["Bound content<br/>prose, figures, release history"]
    end
    subgraph Adapters["Adapters (code, one per benchmark or method)"]
        MA["Method adapters"]
        BA["Benchmark adapters"]
    end
    subgraph Shared["Shared pipeline (code, one implementation per stage)"]
        direction LR
        S1["Run"] --> S2["Score"] --> S3["Build report"] --> S4["Check and package"] --> S5["Publish"]
        S2 --> S6["Export result"]
    end
    P -- selects --> MA
    P -- selects --> BA
    MA -- plugs into --> Shared
    BA -- plugs into --> Shared
    C -- rendered by --> S3
```

## Stages and artifacts

Stages communicate only through persisted artifacts. A stage reads the artifact
of the previous stage and writes its own. It never reaches into another stage's
memory, private files or code, so each stage can be rerun or replaced alone.

**Figure 2. Stages and the artifacts they exchange.**

```mermaid
flowchart LR
    PR[/"Profile"/] --> I["Prepare inputs"]
    I --> DS[/"Frozen dataset<br/>cases, references, hashes"/]
    DS --> R["Run"]
    R --> CR[/"Call records<br/>one per physical call,<br/>plus raw evidence"/]
    CR --> S["Score"]
    DS --> S
    S --> SR[/"Scored results<br/>one logical result per<br/>method and task"/]
    SR --> B["Build report"]
    RC[/"Bound report content"/] --> B
    B --> RD[/"Report directory<br/>report model and rendered pages"/]
    RD --> K["Check and package"]
    K --> PK[/"Package<br/>archive, inventory, receipts"/]
    PK --> PB["Publish to Yours<br/>(optional share)"]
    PB --> RCPT[/"Publication receipt"/]
    SR --> X["Export result"]
    RCPT -.-> X
    X --> RJ[/"Result submission"/]
    RJ --> U["Submit to webstone-site"]
```

| Stage | Reads | Writes | Shared owner | Must not |
| --- | --- | --- | --- | --- |
| Prepare inputs | Profile, data lock | Frozen dataset | `src/harness/benchmarks/` and the benchmark adapter | Read measured outputs |
| Run | Frozen dataset, method adapters | Call records and raw evidence | `src/harness/benchmark_execution.py` and the track runner | Score, or drop a physical call |
| Score | Call records, frozen dataset | Scored results | The benchmark's scorer (`src/lexbench_*` or the pinned upstream evaluator) | Call a method, or select rules by method name |
| Build report | Run directory; optionally review records and bound content | Report directory | `src/harness/reporting/` and the track's report module | Call a method, rescore, or embed instance content |
| Check and package | Report directory | Package | `src/harness/reporting/` | Change scores or evidence |
| Publish | Package, publication target | Publication receipt | `src/harness/reporting/publish.py` | Rebuild the report |
| Export result | Run directory; the publication receipt when the report was published | Result submission | `src/harness/results/` | Require hand-entered measurements |

Artifacts travel as a **run directory**: one directory that contains a frozen
copy of the profile, the frozen dataset, the call records with their raw
evidence, the scored results and a manifest. Every reference between them is a path relative to that directory
plus a hash. A run directory copied to another machine is complete: a stage
resolves each reference inside it, verifies the hash, and fails when a file is
missing or differs. It does not look elsewhere or fall back to another copy.
Scored results also carry the method identities, so the report stage needs the
run directory and nothing else; the profile it uses is the frozen copy inside
it. An export without a publication receipt records the result as unpublished. Review records and bound content are optional
inputs, passed explicitly and bound the same way.

Every artifact has a named, versioned schema, and the stage that reads it
validates it. A reader can therefore take an artifact produced on another
machine, by another person, and continue from that point.

## One pipeline per track

Benchmarks in the same track run through the same pipeline from input
preparation to report. The track owns the skeleton: the order of steps, the call
boundary, deadlines and retries, record writing, certification, aggregation and
the report build. A benchmark supplies only the steps that differ, through its
adapter.

**Figure 3. The track owns the skeleton; a benchmark fills the marked steps.**

```mermaid
flowchart LR
    subgraph Track["Track pipeline (one implementation)"]
        direction LR
        A["Load cases"] --> B["Prepare input environment"] --> C["Check method identity"] --> D["Call method<br/>per case"] --> E["Write call records"] --> F["Score"] --> G["Aggregate populations"] --> H["Build report"]
    end
    BA["Benchmark adapter"] -. "cases" .-> A
    BA -. "inputs: live site, served fixture,<br/>deployed environment" .-> B
    BA -. "scorer" .-> F
    BA -. "populations, metrics,<br/>expected matrix" .-> G
    BA -. "views" .-> H
    MA["Method adapter"] -. "identity" .-> C
    MA -. "call, projection" .-> D
```

A benchmark that serves frozen pages from a local server, one that calls live
sites and one that needs a deployed environment differ in the input environment
step, not in the runner. A benchmark with its own metric differs in the scorer
and the populations, not in the certifier or the report builder. A benchmark
does not get its own run loop, record files, certifier or generator.

Two tracks may share steps through the shared packages, but they are not
required to share one skeleton: a fetch call and an agent episode have different
shapes.

## Extension points

These are the only places where code specific to one method, benchmark or track
may live. When an adapter cannot express what an instance needs, extend the
interface for every adapter in the same change; do not branch around it.

| Extension point | Declares | Added when |
| --- | --- | --- |
| Method adapter | Identity and version, capabilities, how to make one call, how to turn its native output into the track's call record, failure classification, cost | A provider, browser or model configuration is added |
| Benchmark adapter | How to load and serve cases, the scorer, populations and denominators, metric set, expected counts, review prompts | A dataset or task family is added |
| Environment adapter | How to deploy, reset, log in to and label a site or runtime | A stateful target (for example a WebArena site) is added |
| Track registration | The track's stage implementations, schemas and example, keyed by the fixed stage names | A track is added |
| Track template | Which base-template pages and slots the track fills, with its metric set and result-detail layout | A track is added |
| Report section hook | A section or detail view that a benchmark or track contributes to a template slot | A report needs a view the shared sections do not have |
| Release rule | An additional check a track contributes to the shared release gate | A track has a presentation or evidence rule of its own |
| Publication target | Host, site, expected version, visibility and share setting | A report instance is published |
| Result exporter | How a track's scored results map to a result submission | A track's results go to webstone-site |

Adapters register themselves in one registry per extension point. Shared code
obtains them from the registry, and command choices, validation sets and tests
derive from the registry too. A hardcoded list of adapter names anywhere else is
a second registry and is not allowed.

The shapes below fix the responsibilities. Field names converge as tracks
migrate; the split of responsibilities does not.

```python
class MethodAdapter(Protocol):
    name: str
    capabilities: Capabilities            # declared, never inferred from the name

    def identity(self) -> MethodIdentity: ...          # version, binary hash, settings
    def preflight(self, cases) -> list[str]: ...       # missing requirements
    def call(self, case, deadline) -> CallRecord: ...  # one physical call, raw evidence kept
    def project(self, raw) -> ScoredInput: ...         # native output -> what the scorer reads


class BenchmarkAdapter(Protocol):
    name: str

    def cases(self, lock, profile) -> Iterable[Case]: ...
    def inputs(self, cases) -> ContextManager[InputEnvironment]: ...  # live, fixture or deployed
    def score(self, case, scored_input) -> Score: ...  # task rules come from case data
    def populations(self) -> list[Population]: ...     # denominators, families, metric sets
    def views(self) -> list[SectionHook]: ...          # optional report sections


class TrackStages(Protocol):          # fixed keys; a track supplies each or declares it absent
    run: Stage; score: Stage; report: Stage; check: Stage
    package: Stage; publish: Stage; export: Stage
```

## Report instances

A report instance is described entirely by data: a **profile** and its **bound
content**.

- The profile names the benchmarks, methods, exact versions, scope, expected
  counts, publication target and language. It lives beside the external
  evidence. The run stage receives it explicitly and freezes a copy into the
  run directory; later stages read that copy.
- Bound content holds everything written for that one report: interpretation
  prose, figures, labels that differ from the catalog, release history, dated
  notes and any sentence that quotes a measured value, in every language the
  report ships.
- Shared code holds none of this. A renderer, checker, translation map or
  message catalog must not contain a method's measured numbers, a benchmark's
  size, a version string, a campaign name or prose about one report.

Combining results is a pipeline operation. Separately produced run directories
for the same frozen dataset combine into one comparison through a documented
command that discovers the runs, verifies their hashes and writes the build
input.

People author declarations: the profile and the bound content. Programs derive
everything else. A stage must not depend on a hand-written file that restates
derived data, such as computed hashes, enumerated case identifiers or copied
measurements. Authored files are validated and hash-bound when a stage reads
them.

Customisation remains possible and expected. It goes into the profile, the bound
content or an adapter, never into the stage.

## Report templates

Reports come from templates, in three layers. The layers work like a base class
and its subclasses: each one fills slots the layer above declares and adds no
page structure of its own.

**Figure 4. Template layers and what each may decide.**

```mermaid
flowchart TB
    BT["Base template (shared)<br/>page roles, navigation, section order,<br/>components, languages, themes"]
    TT["Track template (one per track)<br/>metric set, comparison columns,<br/>result-detail layout, dataset view"]
    BV["Benchmark views (optional hooks)<br/>extra sections for one task family"]
    IC["Instance content (data)<br/>interpretation, figures, notes"]
    BT -- "declares slots for" --> TT
    TT -- "declares slots for" --> BV
    BT -- "declares content slots for" --> IC
    TT --> OUT["Report"]
    BV --> OUT
    IC --> OUT
```

The base template fixes the page roles every report has and the order of
sections on each. A track fills a role or declares it absent; it does not add a
role of its own.

| Page role | Answers | Generated from |
| --- | --- | --- |
| Home | Who leads, on what population, under which rules | Scored results: the core comparison comes first |
| Datasets | What was asked, and how a task passes | Frozen dataset; no measured scores |
| Methodology | How calls were made and scored | Profile, method identities, metric guide |
| Results | Every logical result, filterable, with a detail view and evidence | Scored results and evidence bindings |
| Interpretation | What the results mean | Generated summary by default; authored content when bound |
| Review | What was independently checked | Review records |
| Reproduce | How to run this again | Profile and the documented commands |
| Changes | What changed between releases | Bound release history |

Defaults:

- **A report builds from the run directory alone.** With no authored
  content, every page role renders from data: rankings, counts, failure
  distributions, metric definitions and reproduction commands. A build never
  requires hand-written prose, and a missing authored slot renders as its
  generated default. When no review record exists, the Review page states that
  no independent review is recorded; when no release history is bound, Changes
  lists the current build only.
- **The default report of a track and benchmark is always the same shape.** Two
  runs of the same track differ in data, never in page set, section order or
  components.
- **A variant is data.** Certified, diagnostic, audit and similar variants are
  registered selections of slots and labels over the same template, each with a
  runnable example. A variant is not a separate generator.
- **The report records its template.** The report manifest names the base
  template, track template, variant and their versions.

Free-form work stays possible when it is asked for. Authored interpretation,
figures and extra sections go into the content slots of the instance. A view
that the slots cannot hold becomes a section hook that every later report can
use. A request for one report does not create a new template, a copied renderer
or a one-off generator.

## Command surface

Every track exposes the same stage verbs through one root command, and every
verb takes the profile or the previous stage's artifact:

```text
webstone <track> run | score | report | check | package | publish | export
```

A track may omit a stage it does not have and may add preparation commands of
its own. It may not offer a second command for a stage that already has a verb,
nor a benchmark-specific variant of a verb. Mode selection comes from the
profile. Files under `scripts/` and Make targets stay thin wrappers over these
verbs.

Another person must be able to reproduce an evaluation from a clean checkout,
their own credentials and the documented commands alone: prepare inputs, run,
score, build the report, and optionally publish. Nothing may depend on the
author's working session.

## Rules

1. **No instance in shared code.** Shared modules do not name a method, benchmark,
   site, dataset size, version, campaign or report. They read capabilities and
   declarations from adapters and profiles.
2. **Dependencies point inward.** Shared packages import no track. A track imports
   shared packages and its own adapters, not another track. Adapters import the
   interface they implement.
3. **One pipeline per track.** A track has one runner, one scorer entry, one
   report builder, one release gate and one publisher. Every benchmark and
   method in the track reuses them through an adapter.
4. **Artifacts are the only coupling.** Stage inputs and outputs are persisted,
   schema-versioned and validated on read. No stage passes state through
   globals, context variables or undeclared keys.
5. **Derived inputs are generated.** People author the profile and bound content;
   no stage needs a hand-written file with computed hashes, enumerated case
   identifiers or copied measurements.
6. **Interfaces are explicit.** Every hook that shared code calls is declared on
   the interface. Shared code does not probe adapters with `getattr` or by type
   name.
7. **Extend the interface, not the caller.** When an instance does not fit, change
   the shared code so that any adapter can use the new hook, capability or
   field. A conditional on one adapter's name in shared code is the signal that
   an interface is missing.
8. **Instance content is bound data.** Prose, numbers and labels for one report
   live in its bound content and are rendered from there in each language.
9. **Reproduction is a command sequence.** The documented commands, a clean
   checkout and credentials are sufficient.
10. **Default reports are templated.** With no further instruction, a track's
    report is its registered template rendered from data. Authored content fills
    declared slots and never changes page structure.
11. **History stays verifiable.** Frozen evidence, published reports and
    hash-bound sources named by a current authority keep their bytes. Rules 1–10
    govern current code paths, and historical bytes are data read through them.

## Where a change goes

**Figure 5. Deciding where a change belongs.**

```mermaid
flowchart TD
    A["What is being added or changed?"] --> B{"A provider, browser<br/>or model configuration?"}
    B -- yes --> B1["Method adapter + registry entry<br/>+ example config"]
    B -- no --> C{"A dataset, task family<br/>or acceptance rule?"}
    C -- yes --> C1["Benchmark adapter<br/>and case data"]
    C -- no --> D{"A new comparison<br/>or report?"}
    D -- yes --> D1["Profile + bound content<br/>No code"]
    D -- no --> E{"A new view, metric<br/>or check in reports?"}
    E -- yes --> E1["Section hook or release rule,<br/>available to every instance"]
    E -- no --> F{"Behaviour every instance needs?"}
    F -- yes --> F1["Shared stage or interface,<br/>all adapters updated together"]
    F -- no --> G["Stop: it is instance-specific.<br/>Find the adapter or data that owns it"]
```

| Change | Instance-specific part lives in | Shared code changes only to |
| --- | --- | --- |
| Add a method | One adapter module, its registry entry, example config, pricing or catalog data, its tests | Add a hook or capability that any method can declare |
| Add a benchmark | One benchmark adapter, data lock entry, case data, its tests | Add a pipeline step or adapter hook that any benchmark can fill |
| Add a report instance | Profile and bound content outside Git | Nothing |
| Add a track | A track package implementing the stage registration, an example, tests | Register the track; move a step two tracks now share into a shared package |
| Add a report view | A section hook and its release rule | Add a slot that any track can fill |
| Add a report variant | A registered slot selection with an example and tests | Add a slot or label option that any variant can select |

A change to shared code states in its description which interface it extends
and why every adapter can use it. A shared-code change that names one instance,
or that only one instance can ever use, is a violation.

## Enforcement

Reviews apply this page to every change. Three mechanical checks back them up.
They are introduced by the tasks in the [convergence task](https://github.com/lexmount/webstone/issues/336);
until a check exists, review is the enforcement.

- **Layering check.** A test fails when a shared package imports a track, or a
  track imports another track.
- **Instance-literal check.** A test fails when a shared module names a registered
  method, benchmark or site, or matches a measured-value pattern in a renderer,
  checker or translation map. Existing occurrences are listed in an allowlist
  that may only shrink.
- **Conformance example.** Each track runs a synthetic method and a synthetic
  benchmark, registered only through adapters, through every stage it supports.
  This proves that an instance can be added without editing shared code.

## Migration

Existing code predates this page. New code conforms from now on, and so does
any function or module a change rewrites or moves. Existing violations are inventoried in the
[convergence task](https://github.com/lexmount/webstone/issues/336) and removed
by its tasks; once the checks above exist, their allowlists become the
inventory. A change may leave a violation in code it does not rewrite, and may
not add one anywhere. A migration preserves frozen evidence and published
reports, moves callers atomically, and removes the code it replaces in the same
change.
