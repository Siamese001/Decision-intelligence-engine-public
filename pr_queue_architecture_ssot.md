# PR Queue Consolidation — Architecture & SSOT Design

> **SVP+ Agentic Engineering Reference Architecture (Amit Ayer)**
> Exemplifies **engineering best practices, architectural foresight, and novel multi-agent PR governance** via a 5-Agent Council deliberation engine, Merkle-linked rank transition receipts, and zero-contention worktree isolation.

> [!IMPORTANT]
> **Status**: `SUPERSEDED`
> **Canonical Authority**: [`plans/docs_pr_queue_durable_prioritization_plan/implementation_plan.md`](plans/docs_pr_queue_durable_prioritization_plan/implementation_plan.md) (Decision Architecture SSOT v2.1 / Queue Contract v1.4.0)
> **Active Implementation Worktree**: `.worktrees/docs_pr_queue_durable_prioritization_plan`

## 1. Superseded Proposal Notice

The historical 4-wave consolidation proposal originally sketched in this document has been formally superseded by the comprehensive **PR Queue Recovery and Prioritization Plan** (`docs/pr-queue-durable-prioritization-plan`).

The durable prioritization architecture unifies:
1. **Unified Snapshot & Decision Engine**: Shared `QueueSnapshot` and `decision_api` serving CLI, JSON output, rank receipts, and agent summaries without duplicate scoring pipelines.
2. **True Business Value Governance**: Separation of volume heuristics (LOC, test filenames, commit regexes) from verified economic value statements. Unmeasured branches declare explicit `UNKNOWN: Unmeasured business value (structural heuristics only)`.
3. **Replayable Rank Change History**: Merkle-linked snapshot transitions attributing exact causal reasons to every position change.
4. **Primary Decision View & Linked Detail**: Restored 7-column primary table with Diagnostic Ledger or dedicated 6-column decision view (`--view decision`), coupled with an on-disk detail artifact (`reports/pr_queue_detail.json`) preserving complete wave progress and stable git stash identities.
5. **Accurate Inventory Coverage**: Mathematically accurate numerator and denominator measurements for worktree directories and stashes (`Worktree coverage: N/N children · M/M stashes`).

## 2. Superseded Wave Architecture Archive

For historical traceability, the legacy sketch is preserved below as deprecated context:

| Legacy Wave | Historical Scope | Current Status | Governing Replacement |
| :--- | :--- | :--- | :--- |
| Wave 1 | Markdown Rule Harmonization | SUPERSEDED | `.agents/rules/pr-queue-summary.md` (Decision Contract v1.4.0) |
| Wave 2 | Python CLI Consolidation | SUPERSEDED | `tools/pr_governance_core/decision_api.py` & `render.py` |
| Wave 3 | Relative Rationale Generation | SUPERSEDED | `tools/pr_governance_core/queue_actions.py` (`explain_relative_order`) |
| Wave 4 | E2E Governance Test Alignment | SUPERSEDED | `tests/manifests/pr_queue_contract_manifest.json` & Contract Harness |
