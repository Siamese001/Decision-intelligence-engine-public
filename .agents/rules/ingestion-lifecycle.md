---
trigger: glob
globs: "{dual_pipeline/transcript_stages_core/**,pipeline_a_intake/**,scripts/ingest_meeting.sh}"
---

# Ingestion Lifecycle Rules

Governs NEE knowledge-graph ingestion pipeline. Sourced from `AGENTS.md`.

## Directive Lifecycle & Adapter Requirements
1. **Open**: `nee intake-directive-open` bound to contract-valid package bytes before U0.
2. **Attach**: `intake-directive-attach` for additional packages; invalidates prior approval binding.
3. **Approve**: `intake-directive-approve` or bind at creation.
4. **Execute**: Intake stages U0, L1, L0, C0.1–C0.3, X1–X3, L4.
5. **Release**: Approval-bound release, activate, and run terminal completion gate.
Deterministic adapter required; otherwise block via `intake-directive-block`.

## Mutation Approval & Release Lifecycle Completion
Graph-changing run requirements:
- Package hashes and run IDs match open directive. All intake audits pass (U0–L4).
- Production Ingestion Continuation (`INV-INGESTION-ACTIVE-COMPLETION`): Production ingestion requires active intent. When exact approval is missing, the run must persist an `active-ingestion-approval-request` and prompt the operator via `ask_user_question` (or host adapter), entering `AWAITING_APPROVAL`. Once approved, execution resumes the same run through publication, activation, and verification without repeating extraction.
- Retrieval release evaluation passes all 11 retrieval release gates under real BGE-M3 before activation (`validate_activation_evaluation`) with query caching and vectorized scoring (< 7,500ms).
- Provider-free boundary enforced for L1, normalization, identity resolution, routing, disposition, approval, writes.
- Engineering Isolation (`INV-INGESTION-ENGINEERING-ISOLATION`): Engineering waves, test suites, and benchmarks must never mutate deployment graphs (`CANONICAL_GRAPH.db`), executing writes exclusively in disposable sandboxes.
- Zero-Op Runs: Genuine replays with verified pre-existing graph coverage may complete with an immutable `idempotent_noop_receipt` or `COMPLETE_ALREADY_PRESENT`. Skipped, unattempted, or QA-only runs must never report `COMPLETE`.

## L2 Query Constraints
Query stage only; minimized source-bound packet. Recommendations limited to: CANDIDATE, QUARANTINE, REJECTED_NOOP, OPERATOR_INPUT_REQUIRED. Never select identity, approve facts, or execute graph operations.

## Allowed Terminal Outcomes
- `COMPLETE` (proven by `intake-completion-gate` with verified table deltas or `COMPLETE_ALREADY_PRESENT`), `AWAITING_APPROVAL`, `BLOCKED`, or `CANCELLED`.
- Direct callers must use `IngestionCompletionGuard`. Ingestion runs awaiting human approval or blocked by unattempted publication must return non-zero exit codes.

### Mandatory GraphDB Mutation Breakdown Table
Completion reports and mutation communications MUST include:
1. **Granular Graph DB Delta Table**:
   | SQLite Table | Baseline Count | Activated Count | Delta | What Changed |
   | :--- | :--- | :--- | :--- | :--- |
   Explicitly accounts for: `node`, `source`, `source_version`, `extraction_run`, `evidence_item`, `assertion`, `assertion_evidence`, `search_document`, `search_fts`, `audit_event`, `dense_vector`, `sparse_weight`, `colbert_token`, `retrieval_record`.
2. **Itemized Assertions Table**: `Assertion ID`, `Subject Node`, `Predicate`, `Literal / Claim Text`, `Status`.
3. **Idempotency & Hash Transition Explanation**: Mutating runs show before/after `graph_sha256` transition; zero-op runs show `IDEMPOTENT_NOOP` with `+0` across all tables.

## Active Ingestion Approval Prompt & Auto-Activation
Whenever an ingestion pipeline run completes QA gates without pre-bound clearance (`status == "AWAITING_APPROVAL"` or `graph_mutated == False`, `release_status == "PASS"`):
1. **Mandatory Approval Prompt**: The pipeline persists an `active-ingestion-approval-request` and prompts the human operator via `ask_user_question` with structured clickable options.
2. **Immediate Activation Path**: If the operator affirms activation, the pipeline resumes through `nee intake-publish --package <staged_package>` (or `nee ingest-resolve --decision approve`) to mutate the knowledge graph, encode BGE-M3 sidecar vectors, and bind the activation receipt.
3. **No Silent Non-Mutation**: Leaving a valid, passing ingestion run unmutated without surfacing the approval prompt is prohibited.

