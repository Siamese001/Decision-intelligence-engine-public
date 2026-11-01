# Protocol Decision — Mandatory Graph-Change Metric Gate

**Decision ID:** `PD-47-MANDATORY-GRAPH-CHANGE-METRIC-GATE`  
**Status:** `approved_active`  
**Proposed:** 2026-07-11  
**Approved:** 2026-07-11  
**Effective package:** `NEE_v47`  
**Policy version:** 47.0  
**Approved proposal digest:** `6a8790d61f706654c8470a2d45a07f907faad9db33bdb450f720249368dccfd4`  
**Approval source:** explicit user instruction recorded in `approvals/PD-47-MANDATORY-GRAPH-CHANGE-METRIC-GATE.json`

## Problem

Graph-change reviews previously required exact object deltas and conformance but did not require one compact, blocking table showing whether the proposed or actual graph improved or degraded core graph-health and retrieval-safety measures.

## Decision

Every semantic graph change must include the approved metric table in both Gate G1 and Gate R1. Results use `before → after (delta)`. A failed row blocks implementation or release unless an explicit, pre-approved exception exists. Each failure, exception, or material movement must state the required improvement, corrective action, and accountable owner.

## Activation scope

- Account graph delta: zero.
- Non-policy canonical-card delta: zero.
- Control card changed: `NEE_99`.
- Root mirror changed: `PROJECT_INSTRUCTIONS.md`.
- Release-control documentation updated.
- No schema, ontology, relationship-vocabulary, migration, or executable gate-tool change.

## Alternatives considered

1. **No metric gate.** Rejected because object-level conformance alone does not expose graph-health regression.
2. **A large diagnostic report.** Rejected because it is too easy to skip and too difficult to review consistently.
3. **A compact mandatory table with blocking thresholds and corrective comments.** Selected.

## Risk and control

| Risk | Control |
|---|---|
| A metric is not applicable | `N/A` requires a written reason |
| Baseline degradation is hidden | Require `before → after (delta)` |
| Failure is documented but ignored | Failure blocks implementation or release absent a pre-approved exception |
| Comment is vague | Require corrective action and one accountable owner |
| Counts are mistaken for semantic quality | Retain the policy statement that counts support but do not prove correctness |

## Rollback / forward repair

Rollback restores the v46 policy files and removes the v47 control artifacts. Any future threshold, metric, or exception-model change requires a separately approved C6 protocol decision.
