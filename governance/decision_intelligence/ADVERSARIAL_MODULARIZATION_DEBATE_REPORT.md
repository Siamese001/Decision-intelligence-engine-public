# Adversarial 3-Agent Debate: Does Further Modularization Derisk the Codebase?

> **Evaluation Scope:** Forensic Analysis of Issues Encountered Across Last 150 PRs  
> **Model Tier:** Frontier Reasoning (`gpt-5.6-sol` with `xhigh` reasoning)  
> **Date:** 09/20/2026 12:55:58 UTC  
> **Execution Mode:** Operator-Directed Live Execution (`allow_mock=False`, SSOT `env_di`)  

---

## Agent 1 (The Structural Purist): Case for Further Modularization

# Position: Further Semantic Modularization Is Mandatory

The codebase is not “already modular” merely because it contains 25+ `*_core/` packages. A 4,138-line `query_core/engine.py` is a monolith wearing a package namespace.

The evidence from the last 150 commits is decisive:

- **80 of 150 commits—53.3%—were spent on prompt/judge drift and promotion governance.**
- **71 oversized files remain.**
- `query.py` and `validate_dual_pipeline_contracts.py` have **Churn × Complexity risk scores above 3,600** and **no direct unit-test suites**.
- Multiple RCAs originated where important contracts were implicit in control flow, formatting, defaults, or token-budget assumptions.

This is not ordinary maintenance churn. It is the signature of a system whose behavioral boundaries are weaker than its directory boundaries.

The answer is not less modularization. The answer is to stop performing **mechanical file splitting** and complete the **semantic modularization** that should have happened in the first place.

## 1. The 71 Oversized Modules Are Active Reliability Risks

The remaining files in `reports/file_budget_burndown_baseline.json` are not cosmetic violations:

- `query_core/engine.py`: **4,138 lines**
- `release_core/manager.py`: **2,262 lines**
- `storage/database.py`: **1,138 lines**
- 68 additional oversized files

At a 400-line ceiling, `query_core/engine.py` is more than ten module budgets. That does not imply blindly cutting it into eleven files. It means that the file almost certainly contains multiple independently changing responsibilities that have not been named or isolated.

For example, a query engine should not collapse all of these into one compilation unit:

- Request normalization
- Deterministic project-prefix parsing
- LLM routing
- Route arbitration and precedence
- Retrieval-plan construction
- Prompt assembly
- Token-budget allocation
- Model invocation
- Structured-result parsing
- Fallback selection
- Receipt and telemetry emission

These concerns have different invariants, dependencies, failure modes, and rates of change. Keeping them together guarantees that a change to one concern exposes every other concern to regression.

Likewise:

- `release_core/manager.py` should separate gate registration, gate evaluation, receipt production, receipt verification, promotion policy, and orchestration.
- `storage/database.py` should separate transaction management, repository contracts, migrations, query implementations, and backend-specific adapters—without fragmenting transactional invariants.
- `validate_dual_pipeline_contracts.py` should separate contract loading, individual invariant rules, comparison logic, report construction, and CLI behavior.

No new behavior should be added to the 71 oversized modules without first creating an appropriate seam. The file-budget baseline must become a **ratchet**, not a permanent waiver list.

## 2. Oversized Modules Force Humans and LLM Agents to Guess

A 4,138-line file is not the full reasoning context. Correctly changing it also requires understanding:

- Its callers
- Its side effects
- Its imported globals
- Its test fixtures
- Its compatibility requirements
- Its configuration sources
- Its output schemas
- Its fallback behavior
- Its historical assumptions

Even when an LLM can technically ingest that token volume, token capacity is not semantic comprehension. Relevant invariants are diluted among unrelated branches. The agent must infer which details are contracts and which are incidental implementation choices.

That is precisely where hallucinated assumptions arise:

- “The LLM router can choose a better project than the deterministic prefix.”
- “Markdown headings are presentation only.”
- “This default argument is unused.”
- “A 1,500-token completion allowance is enough for valid JSON.”
- “This fallback can execute without changing output semantics.”

Humans fail similarly. Human working memory cannot retain the interactions among dozens of branches, fallback paths, global settings, and compatibility behaviors. Reviewers inspect the local diff while the true contract remains distributed through thousands of lines.

A good module makes the correct context small enough to reason about completely. It exposes assumptions as:

- Types
- Function signatures
- Explicit precedence rules
- Port interfaces
- Invariants
- Contract tests

Architecture should eliminate the need to guess.

## 3. The RCAs Indict Implicit Contracts, Not Modularization

The historical RCAs are often misrepresented as evidence that decomposition is dangerous. They demonstrate that **uncontracted decomposition** is dangerous.

### RCA 1: NEA project misrouting

Moving routing behavior exposed that `^NEA` precedence was embedded implicitly in the monolithic execution order. The extracted LLM router was permitted to override a deterministic rule.

The correct architecture is:

```text
prefix parser
    -> deterministic route decision
LLM router
    -> probabilistic route candidate
route arbitrator
    -> deterministic decisions take precedence
```

The arbitrator should produce a typed decision containing source, rationale, and precedence. A hermetic test must prove that `^NEA` can never be replaced by `enterprise_architecture`, regardless of the LLM response.

### RCA 2: Speaker and attachment alignment

Markdown turn headers were treated as formatting even though downstream behavior depended on them. Visual cues were omitted because the matching contract was not explicit.

Semantic alignment should consume a structured transcript representation such as:

```text
TranscriptTurn(timestamp, speaker, text, visual_cues)
```

Rendering Markdown should be a downstream adapter, not an undocumented semantic interface. Keyword, speaker, timestamp, and visual-cue matching should be separate signal providers with direct tests.

### RCA 3: Cognitive-complexity refactor thrash

Dropping default arguments and breaking callers was not modularization. It was mechanical extraction driven by a metric without API preservation.

The proper pattern is:

1. Characterize existing behavior.
2. Freeze public signatures and defaults.
3. Introduce a compatibility façade.
4. Extract pure behavior behind that façade.
5. Change behavior only in a separate PR.

`scaffold_meeting_metadata` attendee fallback should have been protected by signature and caller-compatibility tests before any extraction occurred.

### RCA 4: Token truncation and fallback

Token ceilings, reasoning budgets, prompt construction, parsing, and fallback selection are currently too entangled.

They require separate components:

- Prompt budget planner
- Token counter
- Completion-reserve policy
- Model adapter
- Structured-output parser
- Fallback controller

The budget planner must reject or compress a request before invocation when it cannot reserve enough completion capacity. Truncated JSON must not be discovered only after an expensive model call.

The lesson from all four RCAs is consistent: **implicit boundaries fail**.

## 4. The Risk Score and Test Gap Are Architectural Defects

A Churn × Complexity score above 3,600 means the code is simultaneously difficult to reason about and frequently modified. That is the worst possible combination.

The fact that `query.py` and `validate_dual_pipeline_contracts.py` have no direct unit-test suites is not merely a testing backlog. It is evidence that they cannot be instantiated or exercised hermetically without dragging in large portions of the system.

That leads to integration-heavy testing with predictable consequences:

- Slow execution
- Shared-state interference
- Environment dependence
- Difficult parallelization
- Nondeterministic model or filesystem behavior
- Failures that do not localize the defective rule

A contract validator should be among the easiest components to test: input contract plus rule should yield deterministic violations. If it is not, orchestration, I/O, reporting, configuration, and validation policy have been improperly fused.

Indirect test coverage is not an adequate substitute. A critical branch exercised incidentally through an end-to-end test is still effectively unprotected because the test does not identify or lock down its local invariant.

## 5. Enforce L0–L6 Boundaries

The repository needs an enforceable dependency model. A suitable L0–L6 structure is:

| Layer | Responsibility |
|---|---|
| **L0** | Immutable value objects, schemas, enums, and errors |
| **L1** | Pure deterministic parsing, normalization, and domain policy |
| **L2** | Ports, protocols, and use-case input/output contracts |
| **L3** | Application workflows and orchestration |
| **L4** | Side-effecting adapters: database, LLM, filesystem, Whisper, WebSocket |
| **L5** | Composition roots, configuration, and pipeline assembly |
| **L6** | API, CLI, jobs, and governance entry points |

Rules must be machine-enforced:

- Dependencies may point only from higher layers to lower layers.
- Lower layers may never import adapters, configuration, or entry points.
- Cross-package imports must use explicit public contracts.
- Circular imports are prohibited.
- Domain policy cannot read environment variables or instantiate clients.
- Imports must not perform I/O.
- LLMs, clocks, token counters, repositories, ID generators, and receipt sinks must be injected.

Dependency injection here means explicit constructor or function parameters—not a global service locator and not a heavyweight DI framework.

With these boundaries:

- L1 routing rules can be tested in milliseconds.
- L3 orchestration can use fake model and repository ports.
- L4 adapters can be contract-tested independently.
- Tests can run concurrently because clocks, IDs, clients, and stores are not global.
- An OpenAI SDK change cannot alter deterministic routing policy.
- A storage migration cannot change query planning.
- A Markdown renderer cannot delete semantic transcript data.

That is blast-radius control.

## 6. The Required Migration Strategy

This must be a controlled strangler migration, not another line-budget splitting campaign.

1. **Characterize current behavior first**
   - Add golden tests for `^NEA` precedence.
   - Preserve transcript timestamps, headers, and visual cues.
   - Snapshot public signatures and default arguments.
   - Add token truncation and fallback fixtures.
   - Capture prompt score floors and promotion-gate behavior.

2. **Preserve stable façades**
   - Existing callers continue to use compatible entry points.
   - Internals move behind those façades.
   - Aliases and defaults are removed only through an explicit deprecation process.

3. **Extract pure policy before adapters**
   - Routing precedence, gate rules, token budgeting, and validation rules become side-effect-free modules first.

4. **Introduce explicit ports**
   - Model client, repository, clock, filesystem, token counter, and receipt writer become injected protocols.

5. **Separate extraction from behavior changes**
   - A PR either relocates behavior with equivalence tests or intentionally changes behavior with new acceptance tests. It must not do both invisibly.

6. **Ratchet the architecture**
   - No new file may exceed 400 lines.
   - No file on the 71-file baseline may grow.
   - Top-risk modules require direct tests before modification.
   - Forbidden imports and cycles block merge.
   - Every extracted module must have one coherent reason to change.

The 400-line limit is not the architecture by itself. Cohesion, explicit contracts, acyclic dependencies, and direct tests are the architecture. The line ceiling is the enforcement mechanism that prevents another 4,000-line orchestration sink from re-forming.

## Conclusion

More than half of the last 150 commits were consumed by prompt-policy drift and governance mechanics. Critical modules combine extreme churn, extreme complexity, and zero direct tests. The major RCAs repeatedly reveal hidden precedence rules, hidden data contracts, hidden compatibility guarantees, and hidden resource assumptions.

Continuing with the current structure means accepting recurring regressions as a permanent operating model.

Further **semantic modularization** is therefore not an aesthetic preference or cleanup exercise. It is the prerequisite for:

- Deterministic testing
- Safe parallel development
- Reliable LLM-assisted engineering
- Bounded regression impact
- Faster diagnosis
- Stable governance
- Controlled future change

The choice is not between modularization and stability. The choice is between **disciplined modularization now** and **unbounded failure propagation later**.

---

## Agent 2 (The Systems Pragmatist): Rebuttal Against Fragmentation

# Rebuttal: Stop Treating File Count as Reliability Engineering

Agent 1 commits a category error: confusing **physical source separation** with **semantic isolation**.

The repository already has more than 25 `*_core/` packages. Three of the four named RCAs were precipitated by decomposition that lost behavior at newly created boundaries. The fourth was caused by model-token constraints. Prescribing another repository-wide splitting campaign—this time enforced by an arbitrary 400-line ceiling—is repeating the failed treatment at greater scale.

A 4,138-line file is a serious reviewability smell. It is not proof that splitting it into eleven files will make the system safer. The forensic record says the opposite: uncontrolled boundary creation has already erased invariants, broken callers, created cycles, and forced compatibility shims.

## 1. The Commit Data Does Not Support the Diagnosis

The top three categories account for **113 of 150 commits—75.3%**:

| Category | Commits | Share | Primary failure control |
|---|---:|---:|---|
| Prompt and LLM judge drift | 41 | 27.3% | Versioned prompts, pinned model configuration, calibrated evaluation corpus |
| Governance receipts and promotion | 39 | 26.0% | Declarative gate policy, idempotent receipts, replayable promotion state machine |
| General fixes and chores | 33 | 22.0% | Case-specific correctness controls |
| **Total** | **113** | **75.3%** | **Not a line-count intervention** |

The forensic record does not attribute these commits to oversized files. The largest two categories alone—**80 commits, or 53.3%**—concern changing policies and nondeterministic evaluation behavior, not source layout.

Only **11 commits, or 7.3%**, are explicitly categorized as cognitive-complexity and line-budget work. Those commits are not evidence that file budgets solved incidents. They are associated with refactor thrash, dropped defaults, broken callers, and alias shims.

Commit count is also not RCA count. Agent 1 uses the volume of prompt and governance changes as evidence of weak module boundaries, but no causal link has been established:

- Splitting a prompt builder into four files does not stabilize an LLM judge.
- Moving gate evaluators into micro-modules does not stop promotion policy from changing.
- Dependency injection does not prevent reasoning-token starvation.
- An L0–L6 hierarchy does not resolve ASR ambiguity.
- A 400-line ceiling does not make regex precedence correct.

The dominant failures require behavioral controls, not more directories.

## 2. Modularization Directly Precipitated Three Major RCAs

Agent 1 attempts to rescue modularization by declaring each incident “bad modularization.” That is unfalsifiable. If every decomposition regression merely proves that still more disciplined decomposition was needed, the proposal can never be disproven.

The actual engineering record matters.

### RCA 1: NEA misplacement

The project router was decoupled into `query_router_core`. During that separation, the LLM route became authoritative and the deterministic `^NEA` prefix rule was lost.

That was not caused by insufficient file splitting. It was caused by distributing one cohesive routing decision across boundaries whose contracts did not encode precedence.

Agent 1 proposes a parser, LLM candidate producer, and arbitrator. That creates three interfaces and leaves correctness dependent on their composition. The simpler and safer semantic unit is one cohesive route policy:

1. Match authoritative deterministic prefixes.
2. Return immediately when one matches.
3. Invoke the LLM only for unresolved inputs.
4. Validate the LLM output against allowed projects.

That policy can have internal helpers without becoming separate packages. A table-driven test should establish that `^NEA` always wins. The guarantee comes from the invariant and the test—not from the number of files.

### RCA 2: Transcript and attachment alignment

Decomposition stripped `### [HH:MM:SS]` turn headers and excluded visual cues from keyword matching. The narrower interfaces discarded information that the original integrated flow implicitly preserved. The result was **100% missed attachment mentions**.

A structured transcript representation could help, but that is **domain modeling**, not evidence for micro-modules. A `TranscriptTurn` type that omits visual cues would simply make the same bug look architecturally respectable.

The necessary protection is an end-to-end semantic-preservation test:

```text
transcript + deck
    -> normalized turns
    -> candidate mentions
    -> aligned attachment references
```

The fixture must prove that timestamps, speaker identity, text, Markdown rendering, and visual cues survive the complete pass. Tiny unit tests around isolated transforms would not have caught the pipeline-level information loss.

### RCA 3: Complexity-refactor thrash

Functions were split to satisfy `cognitive_complexity <= 15`. During that work:

- Default parameters were dropped.
- `scaffold_meeting_metadata` lost attendee-fallback behavior.
- Existing callers broke.
- Alias shims were added.
- Import cycles proliferated.
- CI required a Tarjan SCC linter to police the resulting dependency graph.

This is exactly how metric-driven modularization behaves under delivery pressure. Once the target is “under 15 complexity” or “under 400 lines,” engineers satisfy the metric by moving branches, creating wrappers, adding re-exports, and pushing state through more interfaces. Semantic complexity remains; navigational complexity increases.

The result is not modularity. It is indirection.

### RCA 4: Token truncation

The remaining RCA involved a 1,500-token completion ceiling and reasoning-token starvation under high prompt load. JSON was truncated, then healing-mesh fallbacks activated.

No source-file decomposition addresses that failure. The controls are:

- Model-specific context and completion budgets
- Preflight token accounting
- Reserved completion capacity
- Prompt compaction
- Constrained structured output
- Explicit truncation detection
- Bounded retry and fallback policy

Calling each of these a separate module does not make the calculation correct.

## 3. The 71 Oversized Files Are Triage Signals, Not Split Orders

Files such as:

- `query_core/engine.py`: 4,138 lines
- `release_core/manager.py`: 2,262 lines
- `storage/database.py`: 1,138 lines

deserve investigation. They do not justify an automatic `<400 lines` policy.

Line count does not measure:

- Cohesion
- State ownership
- Contract stability
- Side effects
- Fan-out
- Defect density
- Co-change patterns
- Transactional integrity
- Testability

A 250-line module can be untestable because it reads global configuration, instantiates an LLM client, and mutates shared state. A 1,000-line module composed of pure policy tables and deterministic transformations can be easy to test.

The named files also have different architectural characteristics:

- `query_core/engine.py` may contain separable side-effect boundaries, but its routing and fallback sequence may need to remain visible as one cohesive workflow.
- `release_core/manager.py` may represent one promotion state machine. Splitting every state transition into separate files can obscure legal transition ordering.
- `storage/database.py` may contain transaction-lifecycle invariants. Separating begin, write, receipt, commit, and rollback behavior across arbitrary modules can make atomicity harder to verify.

The correct analysis is responsibility, co-change, and dependency analysis—not division by 400.

A hard 400-line gate will produce predictable Goodhart effects:

- Helper dumping grounds
- One-function files
- Re-export layers
- More `__init__.py` indirection
- Compatibility façades that never disappear
- Duplicate types at package boundaries
- More import edges and cycles
- Smaller files with unchanged semantic complexity

It can improve the dashboard while worsening the system.

Agent 1’s rule that oversized files cannot receive new behavior before creating a seam is especially dangerous. It forces urgent correctness fixes to wait behind structural refactors. It also contradicts the recommendation to separate extraction from behavior changes: every incident becomes at least two PRs, with the risky structural PR landing before the actual fix.

## 4. High Churn and Complexity Demand Tests, Not Automatic Fragmentation

Risk scores above 3,600 for `query.py` and `validate_dual_pipeline_contracts.py` are legitimate alarms. Zero direct unit-test suites are unacceptable.

But the conclusion that they lack tests *because they are too large to test hermetically* is unsupported. Other explanations include:

- The modules were treated as entry points.
- Coverage exists only through broad integration tests.
- External dependencies were not made replaceable.
- Test ownership was neglected.
- The validator was incorrectly assumed to be self-verifying.

**Hermeticity is determined by side-effect control, not file count.**

`query.py` can be tested through its public behavior by substituting only the true nondeterministic boundaries:

- Model client
- Repository
- Clock
- Filesystem
- Token counter

`validate_dual_pipeline_contracts.py` can be exercised against temporary, version-controlled fixture pairs:

- Valid equivalent outputs
- Missing required fields
- Speaker disagreement
- Timestamp drift
- Truncated JSON
- Receipt mismatch
- Schema-version mismatch

Those tests can be added before moving a line of production code.

Splitting validation into dozens of rule files might make each rule easy to unit-test while still failing to test the actual dual-pipeline contract. The key risk is disagreement between complete outputs, so golden and scenario-level tests are indispensable.

The right sequence is:

1. Characterize current behavior.
2. Add direct behavioral tests.
3. Measure dependencies and co-change.
4. Introduce the minimum side-effect seams required for hermetic execution.
5. Extract only when the resulting component has an independently meaningful contract.

The test suite—not a line budget—must authorize the extraction.

## 5. “Death by 1,000 Files” Is a Real Cognitive Failure Mode

Agent 1 argues that large files exceed human and LLM context. That is only half the context problem.

A meeting transcription pass spread across eight files in four `*_core` directories requires the engineer or agent to reconstruct:

- Which façade is current
- Which alias is backward-compatible
- Which layer owns normalization
- Where rendering changes into semantic matching
- Which protocol an adapter implements
- Where fallback precedence is decided
- Which composition root wires the production implementation
- Which tests exercise the assembled path

The total code does not disappear when it is split. It gains interface definitions, wrappers, imports, re-exports, configuration, and wiring.

For LLM agents, this is particularly hazardous. Retrieval may surface the local implementation but omit:

- The protocol in another package
- The composition root
- A compatibility shim
- A downstream consumer relying on undocumented formatting
- The integration test that defines actual behavior

That is exactly how an agent concludes that Markdown headers are “presentation only” or that a default argument is unused.

Physical separation isolates blast radius only when it corresponds to genuine runtime and ownership isolation. If every transcription request still traverses all eight files synchronously and shares the same data model, splitting has not isolated the blast radius. It has merely distributed it.

Testing can degrade similarly:

- More units require more mocks.
- Mock-heavy tests validate local assumptions rather than production composition.
- Every local suite can pass while the pipeline loses information between modules.
- Wiring and version compatibility become additional failure surfaces.
- Integration tests remain necessary and become harder to construct.

Deterministic, parallel testing comes from eliminating global state and controlling external resources. It does not require turning every domain operation into a package.

## 6. L0–L6 Is an Unproven Reorganization Tax

A universal seven-layer hierarchy is architecture by prescription, not by evidence.

The repository already has 25+ decomposed packages and import-cycle problems. Migrating it to L0–L6 would create enormous movement churn across precisely the modules with the highest current risk.

Strict horizontal layers also encourage shotgun changes. A single feature may require coordinated edits to:

- L0 schema
- L1 parser
- L2 port
- L3 workflow
- L4 adapter
- L5 composition
- L6 entry point

That is not automatically safer than a cohesive vertical capability.

Dependency injection should be applied selectively at nondeterministic and side-effecting boundaries. Injecting the LLM, storage, clock, filesystem, and token counter is sensible. Threading interfaces through every pure helper is not. Excessive DI creates wiring defects, mock forests, and interfaces with only one implementation.

The appropriate architecture may be much simpler:

1. Pure domain policy
2. Cohesive application workflow
3. External adapters
4. Entry points and composition

Even that should be adopted incrementally, not imposed as a repository-wide migration.

## 7. The Correct Reliability Program

The next phase should be behavioral stabilization, with selective extraction only where evidence supports it.

### Freeze metric-driven splitting

- Stop enforcing file size as an architectural outcome.
- Treat the 71-file baseline as a review queue, not a mandatory burndown.
- Permit consolidation where previous extraction created wrappers, cycles, or fragmented workflows.
- Do not combine urgent bug fixes with compulsory structural changes.

### Attack the dominant failure classes directly

**Prompt and judge drift**

- Version and hash prompts.
- Pin model and inference configuration where possible.
- Maintain a frozen evaluation corpus.
- Record score distributions, not only a score-90 floor.
- Require calibration evidence for threshold changes.
- Distinguish model variance from genuine regressions.

**Governance and promotion churn**

- Use one declarative gate manifest.
- Make receipts schema-versioned and idempotent.
- Represent promotion as a replayable state machine.
- Remove duplicated policy across pre-push, CI, and promotion scripts.

**Token exhaustion**

- Perform preflight token accounting.
- Reserve completion capacity based on output schema.
- Detect truncation explicitly.
- Bound retries and fallback transitions.
- Test against model-specific context limits.

**Routing**

- Keep deterministic prefix precedence in one authoritative route policy.
- Invoke probabilistic routing only when deterministic rules do not resolve the input.
- Add table-driven tests for every reserved prefix.

**Transcript and attachment alignment**

- Define a lossless canonical turn representation.
- Add complete transcript-to-attachment fixtures.
- Verify preservation of timestamps, speakers, Markdown headers, and visual cues.

**Regex, ASR, and HITL boundaries**

- Add property-based tests and fuzzing.
- Test ambiguous names, punctuation, timestamps, partial words, and low-confidence ASR output.
- Measure false-positive and false-negative behavior.

### Add direct tests to the highest-risk modules in place

For `query.py` and `validate_dual_pipeline_contracts.py`:

- Test the public behavior first.
- Replace only external side effects.
- Use in-memory implementations rather than deep mock chains.
- Add golden fixtures for historical incidents.
- Track branch and mutation coverage, not merely whether a file has a corresponding test filename.

### Permit extraction only under evidence-based criteria

An extraction should proceed only if it:

- Encapsulates one independently meaningful invariant
- Has a stable input/output contract
- Can be tested without reproducing the entire caller
- Reduces dependency edges or fan-out
- Matches observed co-change patterns
- Does not discard information
- Does not introduce a new cycle or compatibility façade
- Demonstrably reduces change-failure rate or test cost

Measure outcomes such as escaped defects, flaky-test rate, mean time to diagnose, graph cycles, fan-out, navigation hops, and change-failure rate. Do not declare success because a file crossed below 400 lines.

## Verdict

The repository does need stronger contracts, direct tests, controlled side effects, and selective seams. It does **not** need another blanket modularization crusade.

The forensic record is clear:

- More than 75% of commits fall into categories not attributed to file size.
- Three of four major RCAs were precipitated by decomposition that lost semantics or compatibility.
- Existing fragmentation has already produced import cycles, alias shims, and cross-package tracing costs.
- The remaining RCA was a token-budget failure that file splitting cannot solve.
- High Churn × Complexity scores justify immediate testing and stabilization, not automatic source fragmentation.

The correct objective is not “smaller files.” It is **fewer hidden assumptions and fewer opportunities to lose them**.

Sometimes that requires extraction. In this repository, the evidence says it will just as often require consolidation, stronger end-to-end contracts, and refusing to split a cohesive workflow merely to satisfy a vanity metric.

---

## Agent 3 (The Dialectical Arbiter): Synthesis & Final Recommendation

# Adjudication: Pivot From File Decomposition to Contract-Driven Risk Reduction

## 1. Executive Verdict

**No. Further modularizing the codebase would not derisk all—or even most—of the issues encountered in the last 150 PRs.**

A blanket modularization campaign, especially one driven by `<400 lines` or `cognitive_complexity <= 15`, would likely **increase near-term regression risk**. The repository has already demonstrated the failure pattern:

1. Split a cohesive workflow.
2. Lose an implicit invariant at the new boundary.
3. Add compatibility shims and re-exports.
4. Introduce cycles and additional governance checks.
5. Repair the resulting regressions across multiple PRs.

However, Agent 2’s strongest interpretation—avoid further decomposition—is also too broad. The 71 oversized files, particularly the high-churn and untested query and validation paths, remain genuine risks. Some need carefully chosen seams around pure policy and external side effects.

### Definitive ruling

> **Reject global file-budget modularization. Approve selective semantic modularization only after behavior is characterized and only where it reduces coupling, side effects, or duplicated policy.**

| Proposal | Ruling |
|---|---|
| Mandatory `<400-line` production modules | **Rejected** |
| Repository-wide L0–L6 reorganization | **Rejected** |
| Mechanical splitting to satisfy complexity thresholds | **Prohibited** |
| Treating all 71 oversized files as mandatory burndown items | **Rejected** |
| Treating those files as risk-review candidates | **Approved** |
| Direct tests for `query.py` and `validate_dual_pipeline_contracts.py` | **Mandatory, P0** |
| Dependency injection at LLM, storage, filesystem, clock, tokenizer, and ASR boundaries | **Approved** |
| Extraction of pure, independently testable policy | **Conditionally approved** |
| Consolidating fragmented wrappers, aliases, and duplicated policy | **Approved** |

Agent 1 is correct that package names do not constitute meaningful modularity and that high churn plus complexity plus zero direct tests is unacceptable. Agent 2 is correct that the proposed cure must not repeat the decomposition failures already documented.

The required strategy is **contract first, tests second, selective seams third, file movement last**.

---

# 2. Empirical Four-Quadrant Impact Matrix

The quadrants classify interventions, not necessarily entire commits. A governance change, for example, may be orthogonal to file count while still benefiting from consolidating duplicated policy.

## Quadrant I: Where Modularization Directly Helps and Is Warranted

Modularization helps when it creates a stable semantic boundary around independently changing behavior or isolates an actual side effect.

| Target | Warranted intervention | Required constraint |
|---|---|---|
| `query_core/engine.py` — 4,138 lines | Isolate deterministic routing policy from probabilistic model invocation | Keep routing precedence in one authoritative policy; do not distribute it across multiple competing routers |
| `validate_dual_pipeline_contracts.py` | Separate pure validation rules from fixture loading, CLI behavior, and reporting | First add complete-output fixtures and direct tests |
| `storage/database.py` — 1,138 lines | Separate backend-specific operations from domain-facing repositories | Preserve transaction and unit-of-work boundaries; do not split commit/rollback invariants |
| `release_core/manager.py` — 2,262 lines | Consolidate gate policy into a declarative manifest; isolate receipt persistence from state-transition evaluation | Maintain one promotion state machine and one source of truth |
| LLM/model invocation | Inject model client, tokenizer, model capabilities, and retry policy | No global clients or environment reads inside domain policy |
| Transcript processing | Introduce a lossless canonical representation for timestamp, speaker, text, visual cues, and confidence | Rendering must not become the semantic data transport |
| Token planning | Isolate deterministic preflight accounting from model invocation | Extraction makes testing possible but does not itself solve token starvation |

The proper query boundary is conceptually:

```text
authoritative deterministic route policy
    -> unresolved input only
probabilistic route candidate
    -> validation against allowed routes
single workflow orchestrator
```

This does **not** require three packages or a universal seven-layer architecture. It requires one visible precedence invariant and direct tests proving that `^NEA` cannot be overridden.

Likewise, storage isolation is valuable only if the transaction remains comprehensible. Splitting `begin`, `write`, `receipt`, `commit`, and `rollback` into unrelated micro-modules would make the database path less safe, not more.

### Governing principle

Modularization is warranted where it reduces one or more of:

- Nondeterministic dependencies
- Side-effect surface
- Dependency fan-out
- Policy duplication
- Independent change coupling
- Test setup cost

It is not warranted merely because a file exceeds a line count.

---

## Quadrant II: Where Modularization Directly Caused Regressions

The record contains three major transformation-induced failures.

### 1. Project-router decomposition lost precedence

Moving routing into `query_router_core` caused the LLM path to ignore the deterministic `^NEA` prefix. Meetings were misrouted to `enterprise_architecture`.

The new boundary failed to encode:

- Prefix authority
- Precedence ordering
- Conditions under which the LLM may run
- Validation of the LLM’s output

This was not a theoretical risk. It was an escaped production behavior change caused by decomposition.

### 2. Transcript and attachment decomposition lost information

Splitting the attachment aligner and transcript formatter:

- Stripped `### [HH:MM:SS]` turn headers
- Excluded visual cues from keyword matching
- Produced 100% missed attachment mentions

The extraction treated formatting as non-semantic despite downstream dependence on it. The narrower interface discarded data that the integrated pipeline had preserved.

Introducing a `TranscriptTurn` type helps only if it is lossless. A clean type that omits visual cues merely formalizes the same defect.

### 3. Complexity-threshold splitting broke caller contracts

Mechanical decomposition to satisfy `cognitive_complexity <= 15`:

- Dropped default parameters
- Broke `scaffold_meeting_metadata` attendee fallback
- Broke existing test callers
- Required alias shims
- Increased imports and compatibility paths

The repository subsequently needed SCC detection, including Tarjan-based cycle enforcement, because package decomposition increased graph complexity.

These regressions demonstrate an important distinction:

> **Semantic modularity reduces coupling. Mechanical modularity relocates coupling and frequently hides it.**

Agent 1 is correct that these were examples of poorly executed modularization. But that does not exonerate the strategy. Any architecture program must be judged under the repository’s actual incentives and delivery pressure. A metric that repeatedly produces wrapper files, dropped defaults, and hidden ordering changes is an unsafe governance mechanism.

---

## Quadrant III: Issues Orthogonal to Modularization

The dominant issue classes are not solved by source-file decomposition.

### Prompt and LLM judge drift: 41 commits, 27.3%

The appropriate controls are:

- Prompt versioning and hashing
- Pinned model and inference configuration
- Frozen evaluation corpora
- Score-distribution analysis
- False-positive and false-negative calibration
- Explicit threshold-change approval
- Variance tracking across repeated runs

A score-90 floor does not become more valid because its implementation is moved into a 200-line module.

### Governance receipts and promotion churn: 39 commits, 26.0%

Some policy consolidation is warranted, but file splitting is not the remedy. The controls are:

- One declarative gate manifest
- Schema-versioned and idempotent receipts
- A replayable promotion state machine
- One policy source shared by local, pre-push, CI, and promotion paths
- Deterministic explanations for gate failure

The primary problem is duplicated or evolving policy, not the physical size of the manager.

### Token exhaustion and JSON truncation: 3 commits, 2.0%

The named RCA resulted from:

- A 1,500-token completion ceiling
- Reasoning-token starvation
- High prompt load
- Truncated JSON
- Healing-mesh fallback activation

The solution is operational:

- Preflight context accounting
- Model-specific completion reservation
- Prompt compaction
- Constrained structured output
- Explicit truncation detection
- Bounded retry and fallback transitions

Modularization may make those controls easier to test. It does not supply the missing tokens.

### ASR, regex, and retrieval-performance failures

ASR repetitions, ambiguous names, boundary regex behavior, and BGE-M3 latency are similarly controlled through:

- Fixture corpora
- Property-based testing and fuzzing
- Deduplication windows
- Confidence thresholds
- Profiling, batching, caching, and index warmup
- Timeouts and bounded fallback behavior

BGE-M3 latency is not quantified in the supplied commit distribution, so no numerical attribution should be invented. Architecturally, however, it remains a performance problem rather than a line-count problem.

---

## Quadrant IV: Negative Externalities of Over-Modularization

### 1. Context fragmentation

“Death by 1,000 files” is a real cognitive failure mode.

When one meeting-transcription pass spans eight files in four `*_core` directories, an engineer or coding agent must reconstruct:

- The public façade
- The compatibility alias
- The protocol definition
- The production implementation
- The composition root
- The fallback policy
- The formatter
- The integration test defining actual behavior

The total semantic context has not decreased. It has acquired more wiring.

For LLM coding agents, retrieval makes this especially dangerous. The agent may retrieve a local transform but miss:

- A downstream consumer relying on Markdown headers
- A default preserved by a compatibility wrapper
- Production dependency wiring
- A route-precedence rule in another package
- An integration test outside the retrieved context

### 2. Interface and graph expansion

More modules normally mean more:

- Imports
- Re-exports
- Protocols
- DTO conversions
- Composition wiring
- Version compatibility paths
- Opportunities for cycles

Source-file separation controls blast radius only when the modules are independently owned, independently changing, or independently deployable/testable. A synchronous workflow traversing every new module still has the same runtime blast path.

### 3. Mock-heavy test blindspots

Excessive decomposition encourages tests that prove each local mock interaction while missing pipeline-level information loss.

Every unit can pass while:

- Headers disappear between stages
- Visual cues are omitted
- Defaults change
- Production wiring selects the wrong implementation
- Transaction ordering is violated

Local unit tests are necessary, but the historical RCAs prove that complete-path semantic tests are indispensable.

### 4. Goodhart effects from the 400-line metric

A hard line budget will predictably produce:

- One-function files
- Helper dumping grounds
- Pass-through wrappers
- `__init__.py` re-export mazes
- Interfaces with one internal implementation
- Permanent compatibility façades
- Smaller files with unchanged semantic complexity

A 400-line threshold may be an advisory review trigger. It must not be treated as proof of architecture quality.

---

# 3. Root-Cause Pareto Analysis

## What the data can—and cannot—prove

The repository data describes **commit distribution**, not a defect-level causal taxonomy. It therefore cannot honestly prove that a precise percentage of regressions had one root cause.

The top categories nevertheless provide a strong workload signal:

| Causal/work category | Commits | Share |
|---|---:|---:|
| Prompt/evaluation and governance-policy churn | 80 | 53.3% |
| General fixes and chores—causally unclassified | 33 | 22.0% |
| Cross-stage semantic behavior: speaker, routing, hierarchy, attachments | 22 | 14.7% |
| Complexity and line-budget refactoring | 11 | 7.3% |
| Token/runtime and ingestion work | 4 | 2.7% |

The top three original categories account for **113 commits, or 75.3%**. Adding speaker/HITL work brings the total to **125 commits, or 83.3%**.

That is the valid Pareto conclusion:

> Approximately 80% of the engineering workload was concentrated in policy/model behavior, governance, general correctness work, and speaker/HITL behavior—not demonstrated file-size failures.

The `Other / General` category is too broad to assign causally. It would be analytically dishonest to label all 33 commits as either modularity failures or unrelated defects.

## The actual recurring root-cause pattern

Across the named RCAs, the common root cause was:

### 1. Unmanaged behavioral contracts

Critical behavior existed as assumptions rather than executable invariants:

- `^NEA` must override probabilistic routing.
- Timestamp headers are semantically consumed downstream.
- Visual cues must participate in attachment matching.
- Default attendee fallback is part of the public contract.
- Valid JSON requires sufficient completion capacity.

### 2. Missing direct and end-to-end characterization tests

`query.py` and `validate_dual_pipeline_contracts.py` have risk scores above 3,600 with no direct unit-test suites. This allowed refactoring to proceed without a reliable behavioral lock.

### 3. Policy and model volatility

Prompt changes, judge drift, score-floor hardening, promotion gates, and receipts account for over half the observed activity. These are lifecycle and governance problems.

### 4. Information loss at newly introduced boundaries

Three named RCAs involved behavior lost during decomposition. That is not proof that all decomposition is wrong; it is proof that boundaries were introduced before contracts were made explicit.

### 5. Runtime resource constraints

Token ceilings and reasoning-budget starvation caused a failure mode that code organization could only expose or test—not eliminate.

### 6. Oversized files as an amplifier

Large, coupled files increase review difficulty and make local reasoning harder. They are a **risk multiplier**, not the dominant causal category.

A more accurate risk model is:

```text
Risk ≈ change frequency
     × semantic complexity
     × dependency coupling
     × nondeterminism
     × lack of direct observability/tests
```

Line count is only a weak proxy for some of those factors.

## Harmful system feedback loops

Two reinforcing loops are visible:

```text
Prompt drift
  -> threshold hardening
  -> more gates and receipts
  -> duplicated policy
  -> promotion churn
  -> more patching
```

and:

```text
Complexity/file metric pressure
  -> mechanical splitting
  -> lost invariants and more interfaces
  -> regressions
  -> aliases, wrappers, and SCC checks
  -> more governance and maintenance
```

The architecture program must break these loops rather than add another mandatory metric.

---

# 4. Prescriptive Architectural Recommendation

## A. Stop

Immediately stop the following:

1. **Repository-wide `<400-line` enforcement**
2. **Mechanical extraction solely to satisfy cognitive-complexity thresholds**
3. **Large-scale L0–L6 migration**
4. **Mixing structural extraction with intended behavior changes**
5. **Creating interfaces for pure internal helpers without a volatility or testing boundary**
6. **Adding compatibility aliases without an owner and removal date**
7. **Spreading one policy across multiple `*_core` packages**
8. **Forcing urgent correctness fixes to wait for unrelated structural refactoring**

The 71 files in `file_budget_burndown_baseline.json` should become a **risk-review inventory**, not a mandatory deletion queue.

## B. Continue

Continue and strengthen:

- Cycle detection and forbidden-import enforcement
- Direct testing of high-risk modules
- Dependency injection at actual side-effect boundaries
- Characterization and golden tests
- Typed, lossless domain representations
- Explicit dependency composition
- Churn × complexity monitoring
- Consolidation of duplicated policy
- Selective extraction where measurable coupling is reduced

## C. Pivot

Pivot from **file-budget governance** to **semantic-risk governance**.

The architecture should use a small number of enforceable dependency rules:

- Domain policy cannot import infrastructure clients.
- External services are constructed only in composition roots.
- Cyclic dependencies are prohibited.
- One workflow has one visible orchestration owner.
- Public contracts must preserve defaults and semantic data.
- Prompt, gate, route, and fallback policy each have one authoritative source.

A universal seven-layer scheme is unnecessary. Dependency direction matters; the number assigned to each layer does not.

---

## Exact Criteria for Modularization

An extraction should be approved only when **all** of the following are satisfied:

1. **Named invariant or capability**  
   The proposed component owns behavior that can be described without referring to its source-file size.

2. **Behavioral lock exists**  
   Characterization, golden, or direct tests cover current behavior—including historical RCA fixtures.

3. **Stable and lossless contract**  
   Inputs and outputs preserve defaults, timestamps, visual cues, provenance, confidence, and other consumed semantics.

4. **Independent reason to change**  
   Co-change history or ownership shows that the component evolves separately from its caller.

5. **Real boundary**  
   The extraction isolates pure policy, an external side effect, backend variation, or independently testable validation.

6. **Graph improvement**  
   No new cycle is introduced, and the public dependency surface becomes clearer rather than wider.

7. **Compatibility is explicit**  
   Existing signatures and defaults remain intact, or the behavior change follows a versioned deprecation plan.

8. **Extraction and behavior change are separate**  
   The extraction PR demonstrates equivalence. A later PR intentionally changes behavior.

9. **Outcome is measurable**  
   The change reduces test setup time, dependency fan-out, touched-file count, or change-failure rate.

### Strictly forbid splitting when any of these conditions applies

- The only justification is line count or complexity score.
- The proposed pieces always change together.
- The code owns one transaction, ordering invariant, or state machine that would become distributed.
- No characterization test exists.
- The new API uses generic dictionaries or rendered text that discards semantic information.
- The extraction creates only pass-through wrappers or re-export layers.
- It introduces a cycle, service locator, or new compatibility shim.
- It is bundled with prompt, threshold, schema, or routing-behavior changes.
- An active incident can be fixed safely in place first.
- The complete workflow becomes harder to trace than before.

If a module cannot initially be tested without extraction, create a public-path characterization harness first. Extraction must not be used as an excuse to operate without a behavioral baseline.

---

## Disposition of the Highest-Risk Files

### `query.py` and `query_core/engine.py`

1. Add direct public-path tests in place.
2. Encode historical routing and fallback incidents as fixtures.
3. Record route source, precedence, token budget, model choice, and fallback reason.
4. Extract only:
   - Deterministic route policy
   - Model invocation adapter
   - Pure token preflight policy
5. Retain one visible query workflow orchestrator.

### `validate_dual_pipeline_contracts.py`

1. Build fixture pairs for:
   - Valid equivalence
   - Missing fields
   - Speaker disagreement
   - Timestamp drift
   - Truncated JSON
   - Receipt mismatch
   - Schema-version mismatch
2. Test the complete validator first.
3. Extract pure rules only if they have independently meaningful contracts.
4. Keep one canonical runner and report model.

### `release_core/manager.py`

- Consolidate all gate definitions into one manifest.
- Maintain one promotion state machine.
- Isolate receipt storage and external publication.
- Do not split every gate transition into separate files.

### `storage/database.py`

- Separate backend-specific database operations where valuable.
- Preserve transaction scope in one unit-of-work abstraction.
- Contract-test adapters against the same repository semantics.
- Never fragment atomicity merely to satisfy a file budget.

---

## Higher-ROI Derisking Strategies

No cost data supports a literal measured “10× ROI” claim. The following are nevertheless the highest-leverage controls because they target the categories responsible for most observed work rather than treating files one at a time.

### 1. Historical RCA regression suite

Create mandatory fixtures for:

- `^NEA` deterministic precedence
- Attendee fallback defaults
- Markdown timestamp/header preservation
- Visual-cue attachment matching
- Token truncation and fallback behavior

These tests should run against the assembled workflow, not only isolated helpers.

### 2. Prompt and judge governance

- Version and hash every production prompt.
- Record model and inference configuration.
- Maintain a frozen evaluation corpus.
- Evaluate score distributions rather than only a score-90 floor.
- Require calibration evidence for threshold changes.
- Track model variance separately from code regressions.

This directly targets the largest category: 27.3%.

### 3. Promotion-policy consolidation

- One declarative 23-gate manifest
- Schema-versioned, idempotent receipts
- Replayable state transitions
- Identical policy across local, CI, and promotion paths
- Deterministic gate explanations

This directly targets another 26.0%.

### 4. Token and fallback controls

- Model-aware preflight accounting
- Completion-token reservation
- Structured-output enforcement
- Truncation detection before parsing/healing
- Bounded retries
- Explicit fallback transition telemetry

### 5. Lossless transcript contract

Define a canonical representation containing:

- Timestamp
- Speaker identity and confidence
- Text
- Visual cues
- Source provenance
- Rendering metadata where compatibility requires it

Then test transcript-to-attachment alignment end to end.

### 6. ASR, regex, and retrieval engineering

- Property-based and fuzz tests for names, timestamps, punctuation, and partial words
- Low-confidence ASR and repetition fixtures
- BGE-M3 p50/p95 latency benchmarks
- Caching, batching, warmup, timeout, and fallback measurements

### 7. Better causal telemetry

Future PRs and incidents should be tagged with a root-cause taxonomy:

- Policy/specification drift
- Model nondeterminism
- Boundary contract loss
- Resource exhaustion
- Interface compatibility
- Integration wiring
- Mechanical refactor
- External dependency
- Data-quality/ASR

Without this, future “Pareto analyses” will continue conflating commit activity with defect causality.

---

# Final Directive

Agent 1’s claim that further modularization is an **absolute necessity** is rejected. Agent 2’s implication that further decomposition is generally counterproductive is also too categorical.

The governing decision is:

> **Stabilize behavior first. Consolidate policy second. Introduce only evidence-backed seams third. Split files only as a consequence of those decisions—not as the objective.**

The repository’s primary problem is not that 71 files are too long. Its primary problem is that critical behavior—routing precedence, formatting semantics, defaults, prompt contracts, promotion policy, and token assumptions—has not consistently been represented as executable, observable, versioned contracts.

Large files amplify that risk. Excessive fragmentation amplifies it differently.

The architecture program succeeds only if it reduces **hidden assumptions, dependency ambiguity, and untested behavior**. A lower line count, by itself, proves none of those outcomes.

---

