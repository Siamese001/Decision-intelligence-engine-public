# Protocol Decision — Zero-Delta `NA` Retrieval Rationale

**Decision ID:** `PD-47.1.1-ZERO-DELTA-NA-RATIONALE`  
**Status:** `approved_active`  
**Proposed:** 2026-07-11  
**Approved:** 2026-07-11  
**Effective package:** `NEE_v47.1.1`  
**Policy version:** 47.1.1  
**Approved proposal digest:** `739263fc6019211e83789e454868652713b99a0d780e6b43094f8a35638ed181`  
**Approval source:** explicit user instruction recorded in `approvals/PD-47.1.1-ZERO-DELTA-NA-RATIONALE.json`

## Problem

V47.1 did not make graph-rationale enforcement delta-aware. Literal `NA` could pass
on a non-zero graph change, while an unchanged row could still emit unnecessary
rationale prose. That weakened the strict three-table review surface.

## Decision

For every Graph Delta row in G1 and R1:

- an all-zero scalar delta renders the literal `NA`;
- an all-zero slash-delimited vector delta renders the literal `NA`;
- any non-zero scalar or vector delta must include a mechanism-specific retrieval
  rationale; and
- `NA`, `N/A`, blank, `not applicable`, or generic retrieval language on a
  non-zero row fails Gate O1.

The renderer performs deterministic zero-row normalization. The validator uses the
existing integer-vector parser so `+0 / +0` is zero and `+1 / +1` is non-zero.

## Activation scope

- Account graph delta: zero.
- Graph migrations, schema, ontology, and relationship vocabulary: zero.
- Non-policy canonical-card delta: zero.
- Control card changed: `NEE_99`.
- Root policy mirror, gate utility, tests, graph-proposal template, and
  release-control records updated.
- No account-knowledge derivative regenerated.

## Verification

Five new regression tests cover zero scalar rendering, zero vector rendering,
non-zero `NA` rejection, non-zero vector rationale acceptance, and all-zero-table
normalization. The complete gate suite, G1 output, R1 output, scope check, policy
mirror, SQLite integrity, and deterministic packaging must all pass.

## Rollback / forward repair

Restore V47.1 policy and control files. No graph, migration, or non-policy card
rollback is required. Any later change to the zero-delta literal or non-zero
rationale rule requires a separately approved C6 decision.
