# Repository Testing Gaps & Verification Architecture Review Prompt (v1.1.0)
**Document ID:** `PROMPT-GOV-TESTING-GAPS-REVIEW-20261003-V1_1`  
**Classification:** Internal Strategic Engineering & Quality Assurance Governance Contract  
**Target Subsystems:** Entire Repository Testing Fabric (`tests/`, `dual_pipeline/`, `storage/`, `pipeline_a_intake/`, `tools/`, `.githooks/`)  
**Objective:** Audit, evaluate, and structurally upgrade the automated verification architecture across all 9 testing dimensions (Unit, End-to-End, Integration, Regression, Performance, Human-in-the-Loop Governance, Epistemic Security, Multi-Agent Protocols, and Tooling/Gate Parity) to eliminate blindspots, enforce fail-closed invariants, and guarantee mission-critical reliability for the Decision Intelligence Engine.

---

## EXECUTION INSTRUCTIONS FOR THE EVALUATING AGENT / LLM

> **Role & Persona**: You are an elite Principal QA Systems Architect, Test Engineering Director, and Distributed Systems Verification Specialist with over 25 years of experience architecting test harnesses for safety-critical aviation, financial settlement networks, and hyperscale knowledge graph engines.
>
> **Task**: Perform an exhaustive, forensic, end-to-end audit of this repository (`decision-intelligence-engine`) to identify critical testing gaps, unverified edge cases, silent regressions, race conditions, mock leakages, and governance bypasses across all software layers.
>
> **Standard of Judgment**: Apply zero-tolerance scrutiny. High line coverage is not proof of safety. A test that passes without asserting hard state transitions, foreign-key invariants, timing budgets, or failure modes is considered a testing defect.

---

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                MASTER AUDIT & EVALUATION PROMPT DIRECTIVE                                   ║
║                                                                                                             ║
║  "Audit this codebase to guarantee that every critical subsystem, data boundary, and state transition       ║
║   is fortified by rigorous automated verification across all 9 dimensions: Unit Isolation, End-to-End       ║
║   Lifecycle Completion, Cross-Subsystem Integration, Non-Compensating Regression Shields, Hardware-Bound    ║
║   Performance, Atomic HITL Decision Gating, Career Leverage Epistemic Security, Multi-Agent Protocols, and  ║
║   Tooling / Git Hook Parity. Expose all unverified code paths, untested error states, and unasserted        ║
║   side effects."                                                                                            ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## PART 1: CODEBASE ASSETS & VERIFICATION SCOPE TO AUDIT

Before calculating scores or generating gap reports, you MUST inspect the following primary testing and production assets:

1. **Test Suite Inventory (`tests/`)**:
   - Total test files ($\approx 1,095$ files), test class hierarchies, test functions, and fixtures (`tests/embedding_fixture.py`, `tests/l2_fakes.py`, `tests/fixtures/`).
   - Pytest execution compliance: Verify strict compliance with `.agents/rules/wave-progression.md` (Class A debug vs. Class B receipt, mandatory `--timeout=60`, prohibition of unbounded full-suite pytest runs without authorization).
   - Test execution harness: `tools/run_tests_with_progress.py` (`tqdm` progress tracking, dynamic width, pass/fail/error/skip metrics).

2. **Core Production Packages**:
   - `dual_pipeline/` (127 modules): Intake engine, normalization, identity resolution, graph writer, release lifecycle, retrieval suite, LLM judge, semantic cache, decision engine, strategic reasoning, and unified agent architecture.
   - `storage/`: `storage/database.py` (Relational SQLite schema, write serializer, WAL persistence, SQLCipher encrypted shadow DB).
   - `pipeline_a_intake/`: Audio ingestion, Silero VAD gate, circular audio ring buffer, LCS token stitcher, and WebSocket streaming daemon.
   - `tools/` (72 scripts): Pre-mutation gates, contract validators, CI runners, output hierarchy validators, and synthetic embedding ban checkers.

3. **Governance & Gate Infrastructure**:
   - Git Hooks: `.githooks/pre-commit`, `.githooks/pre-push`, `.githooks/post-checkout`, `.githooks/post-merge`.
   - CI Gates: `tools/run_ci_gates.py` (Deterministic profile, BGE profile, UWG gate, timeout policies).
   - Governance Rules: `.agents/rules/` (`contract-enforcement.md`, `ingestion-lifecycle.md`, `hitl-decisions.md`, `runtime-evidence.md`, `wave-progression.md`, `career-leverage-firewall.md`, `unified-agent-architecture.md`).

---

## PART 2: THE 8 TESTING AUDIT DIMENSIONS

Evaluate the repository against the following 8 testing dimensions. For each dimension, assign a **Maturity Score (0% – 100%)**, identify **Verified Strengths**, document **Concrete Testing Gaps**, and specify **Prescriptive Test Additions**.

---

### DIMENSION 1: UNIT TESTING & BOUNDARY ISOLATION
*Are pure algorithms, state machines, and business logic tested in strict isolation without environmental or provider coupling?*

- **Audit Checks**:
  1. **Provider-Free Write Boundary**:
     - Do ingestion normalization, identity resolution, assertion disposition, and graph delta calculations have pure unit tests that execute without network access, LLM providers, or disk I/O side effects?
  2. **Mock & Stub Boundary Hygiene**:
     - Do unit tests utilize explicit, contract-valid test doubles (`tests/l2_fakes.py`) rather than partial or monkey-patched objects?
     - Are mocks verified to reject out-of-contract method calls and unknown attributes?
  3. **Error Handling & Negative Paths**:
     - Are exception pathways explicitly tested (e.g., malformed JSON envelopes, missing foreign keys, duplicate identity aliases, corrupt audio chunks, invalid timestamps)?
     - Do tests verify that errors trigger fail-closed exceptions rather than silent default fallbacks?
  4. **Boundary Values & Null Safety**:
     - Are edge cases tested: zero-length strings, empty arrays, null optional fields, unicode normalization, maximum byte payloads, and timezone offsets?

---

### DIMENSION 2: END-TO-END (E2E) PIPELINE & INGESTION LIFECYCLE
*Does the test suite exercise complete, unbroken execution lifecycles from raw input to verified storage mutation?*

- **Audit Checks**:
  1. **Full Ingestion Stage Chain**:
     - Does E2E testing verify the unbroken pipeline flow: `Package Admission` $\to$ `U0 Intake` $\to$ `L1 Normalization` $\to$ `L0 Identity` $\to$ `C0 Classification` $\to$ `X1-X3 Evidence Verification` $\to$ `L4 Gate` $\to$ `W0/E0 Audits` $\to$ `Release Receipt` $\to$ `Activation` $\to$ `Terminal Completion Gate`?
  2. **Mandatory 14-Table GraphDB Delta Accounting**:
     - Does every mutating E2E test assert the exact delta before and after execution across all 14 SQLite and sidecar vector tables:
       `node`, `source`, `source_version`, `extraction_run`, `evidence_item`, `assertion`, `assertion_evidence`, `search_document`, `search_fts`, `audit_event`, `dense_vector`, `sparse_weight`, `colbert_token`, and `retrieval_record`?
  3. **Idempotency & Zero-Op Receipt Verification**:
     - Do tests verify that re-running the exact same package produces an immutable `IDEMPOTENT_NOOP` receipt where active generation and `graph_sha256` remain identical, with $+0$ across all 14 tables?
  4. **Terminal Outcome Enactment**:
     - Are all three valid terminal outcomes (`COMPLETE`, `BLOCKED`, `CANCELLED`) tested via `IngestionCompletionGuard`? Is it impossible for an ingestion directive to terminate in an unhandled intermediate state?

---

### DIMENSION 3: INTEGRATION & CROSS-SUBSYSTEM INTEROPERABILITY
*Are asynchronous boundaries, concurrent workers, and hybrid database layers tested under realistic inter-process conditions?*

- **Audit Checks**:
  1. **Fast-Path & Slow-Path Handshake**:
     - Does integration testing verify the handoff between the real-time audio pipeline (Fast Path: Splicer $\to$ Whisper $\to$ Stitcher $\to$ WebSocket) and the asynchronous deep extraction worker (Slow Path)?
     - Is there test coverage ensuring audio chunks buffered during compute spikes are not dropped or duplicated?
  2. **Hybrid Relational Graph & Vector Sidecar Synchronization**:
     - When a node or assertion is inserted, updated, or retired in `CANONICAL_GRAPH.db`, do tests verify that the sidecar vector indices (`dense_vector`, `sparse_weight`, `colbert_token`) update atomically without orphaned embeddings or dangling references?
  3. **Concurrency Contention & `SQLITE_BUSY` Resilience**:
     - Do tests simulate simultaneous multi-threaded reads and writes to verify that the in-memory write serializer prevents deadlocks and database lock exceptions?
  4. **Copilot UI & WebSocket Loopback**:
     - Is the WebSocket streaming protocol (`ws://127.0.0.1:8765/ws/live`) tested with simulated disconnects, reconnections, malformed frames, and high-frequency delta broadcasts?

---

### DIMENSION 4: REGRESSION & INVARIANT SHIELDING
*Are non-negotiable architectural invariants safeguarded by unskippable machine gates and cryptographic checks?*

- **Audit Checks**:
  1. **The 11 Non-Compensating BGE-M3 Retrieval Gates**:
     - Does the regression test suite execute `validate_activation_evaluation` enforcing all 11 non-compensating gates under pinned BGE-M3 weights before activation?
     - Do tests ensure that passing 10 gates with high scores cannot compensate for failing the 11th gate?
  2. **Synthetic Embedding Ban**:
     - Does `tools/validate_no_synthetic_embeddings.py` run continuously in pre-commit and CI to detect and block zero vectors, random vectors, and mock embeddings in release paths?
  3. **Contract Digest & Schema Integrity**:
     - Do tests verify that modified contract schemas trigger RFC-8259 canonical JSON digest invalidation, failing closed on unversioned mutations?
  4. **Governed Output Hierarchy Enforcement**:
     - Does `tools/validate_output_hierarchy.py` enforce strict date formatting (`MM-DD-YYYY` subdirectories) and verify that all 3 deliverables (`01_hitl_resolved_transcript.md`, `02_executive_recap.md`, `03_canonical_signal_projection.json`) and 4 receipts reside in the folder without residue?

---

### DIMENSION 5: PERFORMANCE, LATENCY, GPU & CONCURRENCY
*Are latency budgets, hardware acceleration paths, and memory leak vectors benchmarked under sustained load?*

- **Audit Checks**:
  1. **Sub-120ms Local STT Compute Budget**:
     - Is there a dedicated benchmark asserting that Whisper transcription of a 1.5s audio window on Apple Silicon Metal GPU completes in $< 120\text{ms}$?
  2. **WebSocket & UI Render Latency**:
     - Does an automated performance test verify that loopback WebSocket message delivery latency remains $< 5\text{ms}$ and client render updates achieve 120Hz without dropped frames?
  3. **Vectorized Retrieval Latency Gate**:
     - Does retrieval testing verify that steady-state query encoding and hybrid scoring complete within the $< 7,500\text{ms}$ gate limit?
  4. **Memory Stability & Zero-Copy Audio Buffering**:
     - Does a sustained soak test (e.g., simulating a 60-minute continuous audio stream) verify that circular ring buffers and token stitchers maintain $O(1)$ memory consumption without leakages?

---

### DIMENSION 6: HUMAN-IN-THE-LOOP (HITL) & GOVERNANCE AUDITING
*Are human escalation rules, ambiguity margins, and operator feedback loops rigorously verified against governance rules?*

- **Audit Checks**:
  1. **Atomic (One-at-a-Time) Presentation**:
     - Do tests assert that the HITL manager never presents multiple review questions simultaneously in a single prompt or batch?
  2. **The 15% Ambiguity Margin Gate ($\Delta \ge 0.15$)**:
     - Does the test suite verify both branches of the ambiguity gate:
       - If $\Delta = c_{top} - c_{second} < 15\%$, the decision MUST be staged in the HITL review queue and presented to the operator.
       - If $\Delta \ge 15\%$, the decision MUST proceed autonomously under an automated audit receipt without interrupting the operator.
  3. **Interactive Clickable Options Mandate**:
     - Do tests verify that options are structured as discrete, calibrated choices with explicit confidence scores (`HIGH`, `MEDIUM`, `LOW`), rejecting raw markdown bullet lists?
  4. **Persistent Feedback Ledger & Prior Calibration**:
     - Do tests assert that operator decisions are recorded in `hitl_decision_ledger.json` and properly update empirical priors to weight future resolutions?

---

### DIMENSION 7: EPISTEMIC BOUNDARIES & SECURITY / DATA CLASSIFICATION
*Are confidential advisory data, career leverage intelligence, and private credentials strictly quarantined from public deliverables?*

- **Audit Checks**:
  1. **Career Leverage & Artifact Independence Firewall**:
     - Do tests assert that Output 01 (Forensic Transcript) and Output 02 (Executive Recap) are 100% free of private career positioning (executive compensation band), informal political power dynamics, and confidential advisory leverage?
     - Do tests verify that personal career insights are strictly isolated into the encrypted `INTERNAL_ADVISORY` shadow graph?
  2. **Pre-Egress Cloud Sanitization**:
     - Do tests verify that before any payload is sent to cloud LLM providers (`gpt-4o`, Gemini), local regex and NER filters redact API keys (`sk-*`, `ghp_*`, `AKIA*`), private keys, and restricted client PII?
  3. **Multi-Tenant Partitioning**:
     - Do tests verify that queries scoped to `client:nee` cannot access or leak records belonging to `client:partner_advisory_internal` or another client workspace?

---

### DIMENSION 8: MULTI-AGENT DELEGATION & AGENT-TO-AGENT (A2A) PROTOCOLS
*Are multi-agent interactions governed by formal contracts, task ledgers, and deterministic return protocols?*

- **Audit Checks**:
  1. **Binding Delegation Contract Validation**:
     - Do tests verify that an Agent cannot delegate to a Sub-Agent without supplying all 10 mandatory contract fields:
       `task_id`, `parent_task_id`, `objective`, `scope_in`, `scope_out`, `allowed_tools`, `deliverable`, `definition_of_done`, `budget`, and `escalation_rule`?
  2. **Task Ledger State Machine**:
     - Do tests verify the strict state progression of tasks in `dual_pipeline/a2a/task_ledger.py` (`OPEN` $\to$ `IN_PROGRESS` $\to$ `COMPLETE` / `BLOCKED` / `ESCALATED` / `FAILED`), preventing illegal transitions or ghost completions?
  3. **Upstream Return Contract**:
     - Do tests verify that Sub-Agents return structured payloads containing status, verified deliverables, evidence provenance pointers, and consumed tool metrics?
  4. **7-Step Lifecycle Conformance**:
      - Do tests verify that agent workflows adhere to the canonical 7-step execution lifecycle defined in `dual_pipeline/unified_agent_architecture.py`?

---

### DIMENSION 9: TOOLING, GIT HOOKS & CI GATE PARITY
*Are pre-commit/pre-push hooks, gate orchestrators, and optimization shortcuts verified for parity, worktree safety, and fail-closed security?*

- **Audit Checks**:
  1. **Staged vs. Full Scan Parity**:
     - Do tests verify that staged scoping (`--staged`) detects the exact same set of violations as full repository scans for the staged file subset, with zero false negatives?
  2. **Fast-Path & Rebase Bypass Shields**:
     - Do tests verify that shortcut mechanisms (such as rebase bypass markers or environment variable flags) cannot be exploited to leak unverified mutations into `main`?
     - Does the pre-push boundary catch any violation skipped during pre-commit rebase fast-paths?
  3. **Worktree Isolation & Linked Worktree Safety**:
     - Do git hook and orchestrator path resolutions operate correctly in secondary linked worktrees (`.worktrees/<slug>`), where `.git` is an indirection file rather than a directory (`git rev-parse --git-path`)?
  4. **Hermetic Environment Isolation**:
     - Do all hook runners and child processes sanitize `GIT_*` environment variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_PREFIX`) to prevent cross-repository contamination?
  5. **Non-Destructive Rollback & Baseline Protection**:
     - Do `.gitignore` changes and index cleanup operations assert that all required tracked baselines (such as `graph/nee_graph.sqlite` and rollback SQLite files) remain tracked and unignored?

---

## PART 3: AUDIT OUTPUT FORMAT & REQUIRED DELIVERABLES

When executing this audit, structure findings into the following four mandatory sections:

### 1. THE EXECUTIVE TESTING MATURITY SCORECARD
Provide a markdown summary table evaluating all 9 dimensions:

| # | Testing Dimension | Maturity Level (1–5) | Score (0–100%) | Verdict | Primary Testing Gap |
|---|---|---|---|---|---|
| 1 | Unit Testing & Boundary Isolation | ... | ... | PASS / WARN / FAIL | ... |
| 2 | End-to-End Pipeline & Lifecycle | ... | ... | ... | ... |
| 3 | Integration & Interoperability | ... | ... | ... | ... |
| 4 | Regression & Invariant Shielding | ... | ... | ... | ... |
| 5 | Performance, Latency & Concurrency | ... | ... | ... | ... |
| 6 | HITL & Governance Auditing | ... | ... | ... | ... |
| 7 | Epistemic Security & Career Firewall | ... | ... | ... | ... |
| 8 | Multi-Agent Delegation Protocols | ... | ... | ... | ... |
| 9 | Tooling, Git Hooks & Gate Parity | ... | ... | ... | ... |
| **TOTAL** | **OVERALL VERIFICATION MATURITY** | **...** | **...** | **...** | **...** |

*Maturity Level Definitions*:
- **Level 1 (Fragile)**: Sparse unit tests; missing negative paths; zero performance or concurrency checks; manual testing reliant.
- **Level 2 (Coarse)**: Happy-path unit and integration tests exist; mocks are informal; edge cases and race conditions unaddressed.
- **Level 3 (Governed)**: Structured unit, integration, and E2E tests; basic CI gates; timeout enforcement; contract validation active.
- **Level 4 (Robust)**: Strict 14-table mutation tracking; 11 BGE-M3 gates; HITL 15% margin tests; latency budgets benchmarked.
- **Level 5 (World-Class)**: Complete multi-agent formal verification; soak & memory leak testing; hardware-accelerated benchmarking; mathematical guarantee of zero data leakage across epistemic firewalls.

---

### 2. FORENSIC GAP ANALYSIS (P0 / P1 / P2 DEFICIENCIES)
Categorize identified testing deficiencies by severity:
- **P0 (Critical / Release-Blocking)**: Unverified mutation paths, missing firewall leak tests, untested race conditions, or unasserted data integrity invariants.
- **P1 (Architectural / Integration)**: Missing cross-subsystem integration suites, unverified error states, or lack of sustained load benchmarks.
- **P2 (Operational / Quality of Life)**: Test suite execution speed optimizations, duplicate test fixtures, or improved error message assertions.

---

### 3. CONCRETE REMEDIATION & TEST EXPANSION BLUEPRINT
Provide exact, copy-paste ready test specifications, pytest implementations, and test fixture definitions to close the discovered gaps:
1. **Target Unit Test Suites (`tests/test_<subsystem>_unit.py`)**:
   - Provider-free mock tests for untested modules.
2. **Target Integration & Performance Suites (`tests/test_<subsystem>_perf.py`)**:
   - Benchmarks for sub-120ms compute, concurrency locks, and memory leak soak testing.
3. **Target Epistemic & Security Suites (`tests/test_<subsystem>_firewall.py`)**:
   - Strict assertion tests for data isolation and cloud egress sanitization.

---

### 4. 10 TEST BENCHMARK SCENARIOS: MISSION-CRITICAL ASSURANCE
Define 10 complex, real-world failure scenarios that the test suite must simulate and verify:
1. *Corrupt Audio Stream Recovery*: Mid-stream disconnect and corrupted packet recovery.
2. *Concurrent SQLite Contention*: 20 parallel threads reading and writing simultaneously.
3. *BGE-M3 Near-Miss Gate Failure*: 10 gates passing with 99%, 1 gate scoring 69.9% (assert release rejection).
4. *Career Leverage Leakage Injection*: Deliberate injection of confidential executive compensation text into raw meeting transcript (assert quarantine from Outputs 01 & 02).
5. *HITL 14.9% vs 15.1% Ambiguity Boundary*: Verify boundary trigger precision.
6. *Idempotent Re-Ingestion Delta*: Re-submitting identical 10,000-token transcript yields strictly 0 delta across all 14 tables.
7. *Sub-Agent Malicious Escalation*: Sub-agent attempting to mutate global graph directly (assert immediate contract violation).
8. *Memory Stability Soak Test*: 2-hour simulated streaming ingestion with zero memory growth.
9. *Cloud Egress Redaction Failure*: Simulated credential leak in prompt payload (assert pre-egress block).
10. *Bi-Temporal Stale Observation Adjudication*: Querying an expired architectural assumption (assert exclusion from active decision context).

---

```
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
END OF AUDIT PROMPT — COMMENCE FORENSIC CODEBASE EVALUATION ACCORDING TO THESE SPECIFICATIONS
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
```
