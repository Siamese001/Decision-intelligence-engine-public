# Protocol Decision — Three-Table Inline Output Gate

**Decision ID:** `PD-47.1-THREE-TABLE-INLINE-OUTPUT-GATE`  
**Status:** `approved_active`  
**Proposed:** 2026-07-11  
**Approved:** 2026-07-11  
**Effective package:** `NEE_v47.1`  
**Policy version:** 47.1  
**Approved proposal digest:** `afcfa949755115c38db28b146b8453402ccb4ba75796513b53a56162a853ae10`  
**Approval source:** explicit user instruction recorded in `approvals/PD-47.1-THREE-TABLE-INLINE-OUTPUT-GATE.json`

## Problem

The v47 policy required a ten-row graph-metric table, but the executable gate did
not generate or validate the user-visible output. A review could therefore file
the data without showing it, omit a required table, or bury the decision surface
inside excessive narrative.

## Decision

Every G1/C1 proposal review and every R1 conformance review must render exactly
three inline Markdown tables, in this order:

1. Graph Delta and Retrieval Impact.
2. Mandatory Graph Metrics.
3. Card Additions and Retrieval Impact.

All other assessments, operation inventories, reconciliations, digests, approval
instructions, and detailed evidence are filed separately.

The control utility must:

- generate the tables from machine-readable proposal or conformance rows;
- reconcile graph rows to component counts and verify arithmetic;
- require sources, nodes, aliases, edges, claims, validations, node-card routes,
  and every other non-zero component;
- enforce the fixed ten metric names, order, thresholds, result format, and
  blocking status rules;
- reconcile the card table to every body, frontmatter, and route impact;
- reject any extra inline narrative, table, code block, or omitted row;
- bind the G1 output hash to approval and the R1 output hash to conformance; and
- block packaging unless both outputs validate.

## Alternatives considered

1. **Documentation-only rule.** Rejected because it would repeat the v47
   enforcement gap.
2. **Flexible narrative plus the three tables.** Rejected because the gate would
   remain inconsistent and overlong.
3. **Exact generated and fail-closed validated output.** Selected because it
   creates a compact, deterministic, auditable decision surface.

## Activation scope

- Account graph delta: zero.
- Graph schema, migrations, ontology, and relationship vocabulary: zero.
- Non-policy canonical-card delta: zero.
- Control card changed: `NEE_99`.
- Root mirror changed: `PROJECT_INSTRUCTIONS.md`.
- Gate tool, tests, templates, approval/conformance records, generated inline
  outputs, QA, and release-control documentation updated.
- No account-knowledge export regenerated.

## Risks and controls

| Risk | Control |
|---|---|
| A valid proposal is blocked by malformed display data | The machine-readable contract and first-failure message identify the exact row or mismatch |
| A metric is altered after review | The generated output is hash-bound and must match byte-for-byte after line-ending normalization |
| Route-only card impact is omitted | Expected cards are derived from C1, control exceptions, and graph card routes |
| Necessary evidence is hidden | Evidence remains mandatory but is filed separately; only the inline decision surface is constrained |
| A failure is shown but ignored | Failed metric or output validation blocks approval, implementation, conformance, or packaging |

## Rollback / forward repair

Rollback restores the v47 policy, tool, templates, and tests and removes the
PD-47.1 control artifacts. No graph, migration, or non-policy card rollback is
required. Any future change to the three-table schema, metric set, threshold,
status model, or hash-binding rule requires a separately approved C6 decision.

## Verification

Release QA verifies policy mirror equality, declared policy hash, baseline
immutability, zero graph and non-policy-card deltas, exact three-table generation,
G1 and R1 validation, metric enforcement, affected-card reconciliation, approval
and conformance hash binding, 14 automated tests, changed-file scope, SQLite
integrity, and gated candidate packaging.
