# Protocol Decision — Pre-Mutation Graph and Canonical-Card Approval Gates

**Decision ID:** `PD-45-PRE-MUTATION-GRAPH-CARD-GATES`  
**Status:** `approved_active`  
**Proposed:** 2026-07-11  
**Approved:** 2026-07-11  
**Effective package:** `NEE_v45`  
**Policy version:** 45.0  
**Approval source:** explicit user instruction recorded in
`approvals/PD-45-PRE-MUTATION-GRAPH-CARD-GATES.json`

## Problem evidence

The original v45 build applied semantic graph and existing-card changes before
the user reviewed and approved the deltas. Policy 34.0 gated structural
ontology and card-spine changes but not all routine graph mutations or edits
to existing card bodies.

## Alternatives considered

1. **No change.** Rejected because the same failure could recur.
2. **Rely on candidate status.** Rejected because candidate status controls
   promotion, not mutation or packaging.
3. **Require approval only for high-materiality changes.** Rejected because
   small graph or card edits can alter provenance, identity, routing, and
   executive interpretation.
4. **Require exact pre-mutation proposals and fail-closed implementation.**
   Selected.

## Decision

- Every semantic graph mutation requires Gate G1.
- Every canonical card edit requires Gate C1.
- Implementation requires a baseline- and digest-bound approval envelope.
- Material implementation drift causes a hard stop and reapproval.
- Successor packaging requires Gate R1 conformance.
- Generic instructions to run, ingest, reconcile, or apply the policy mean
  analyze and propose only.
- Candidate status does not substitute for mutation approval.
- The rule applies prospectively and cannot ratify an earlier unapproved
  mutation.

## Activation scope

This v45 activation is instruction-only:

- graph delta: zero;
- non-policy card delta: zero;
- control card changed: `NEE_99`;
- root mirror changed: `PROJECT_INSTRUCTIONS.md`;
- control tooling, approvals, RCA, QA, release notes, and manifest added or
  updated;
- prior unapproved v45 semantic changes excluded.

## Risks and controls

| Risk | Control |
|---|---|
| Approval record becomes stale | Bind baseline and input hashes |
| Summary approval is mistaken for exact approval | Bind canonical proposal digests |
| Implementation discovers additional affected objects | Hard stop and revised G1/C1 proposal |
| Build bypasses the process | Gated packaging utility and QA check |
| Policy change is used retroactively | Explicit no-retroactive-ratification rule |
| Control release is mistaken for full synthesis propagation | Zero-delta declarations and release limitation |

## Rollback / forward repair

Rollback is to restore the v44 policy files and remove v45 control artifacts.
Forward repair is a separately approved protocol release. Neither path may
alter the account graph or non-policy cards without G1/C1 approval.

## Verification

The release QA verifies the policy mirror, declared hash, baseline immutability,
graph and card zero deltas, approval binding, gate-tool tests, changed-file
scope, and packaging order.
