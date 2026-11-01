# Root-Cause Analysis — Missing Pre-Mutation Approval Gates for NEE Graph and Card Deltas

**Incident ID:** `RCA-NEE-2026-07-11-PREMUTATION-GATES`  
**Date:** 2026-07-11  
**Status:** closed for policy/control activation; prior semantic build remains quarantined  
**User-designated baseline:** `NEE_v44(1).zip`  
**Baseline SHA-256:** `714b3468810e44abed9ed30c2721dfcf39eca6016996cb99d258d4bea9867531`  
**Affected unapproved build:** original `NEE_v45.zip`  
**Affected build SHA-256:** `bd1d1f10291a5ceba8fd673b4def6bc38efae3cc51bb0d7197df25663f82f178`  
**Corrective decision:** `PD-45-PRE-MUTATION-GRAPH-CARD-GATES`  
**Corrective policy:** 45.0

## 1. Incident statement

The earlier v45 process analyzed six attachments and wrote graph, card,
derivative, migration, manifest, and package changes before presenting the
graph and card deltas for explicit approval.

The required sequence was:

1. Inspect the approved baseline and inputs in a read-only workspace.
2. Produce complete proposed graph and card deltas.
3. Stop for explicit user approval of those exact deltas.
4. Implement only the approved envelope on a copy of the baseline.
5. Prove exact conformance before creating a successor ZIP.

Preserving v44 and labeling the resulting v45 package `candidate` did not
satisfy the missing pre-mutation approval requirement.

## 2. Impact

The original unapproved v45 build contained these semantic deltas relative to
v44:

| Component | Unapproved delta |
|---|---:|
| Sources | +4 |
| Nodes | +23 |
| Edges | +37 |
| Claims | +17 |
| Aliases | +12 |
| Node-to-card mappings | +58 |
| Validation items | +10 |
| Canonical card count | 0 |

Ten existing cards were rewritten: `NEE_00`, `NEE_02`, `NEE_06`, `NEE_09`,
`NEE_10`, `NEE_11`, `NEE_12`, `NEE_13`, `NEE_15`, and `NEE_16`.

The original v45 changed 25 files already present in v44 and added 26 files.
The v44 baseline remained byte-identical.

## 3. Root cause

### 3.1 Policy-design gap

Policy 34.0 explicitly gated new ontology types, domain anchors, and
card-spine operations, but did not explicitly gate every routine source, node,
edge, claim, alias, route, validation, or existing-card body change.

### 3.2 Workflow-order defect

The prior Section 6 directed the process to update canonical evidence and
routes before producing the final reconciled delta. The delta was therefore a
post-mutation report, not a pre-mutation proposal.

### 3.3 Gate-scope defect

The X/Y/Z gate protected structural card-spine changes. It did not protect the
contents of an existing card when card count and card IDs stayed constant.

### 3.4 Candidate-state misconception

`candidate` controlled promotion but did not prevent mutation or packaging.
Promotion approval and mutation approval are different controls.

### 3.5 Missing technical write barrier

The build path did not require a baseline-bound approval record, graph
proposal digest, card proposal digest, changed-file allowlist, or exact
conformance result.

### 3.6 QA coverage gap

QA validated post-change correctness but not pre-change authorization,
approval chronology, exact-delta conformance, or out-of-envelope fail-closed
behavior.

### 3.7 Execution error

The request to run the project instructions was interpreted as authorization
to execute routine changes. The conservative interpretation should have been
read-only analysis and proposal until the exact graph and card deltas were
approved.

## 4. Five-whys summary

1. A refactored ZIP was produced because the task request was treated as
   implementation authorization.
2. That interpretation was possible because routine graph and existing-card
   edits were not explicitly approval-gated.
3. No pause occurred because the workflow generated the final delta after
   writes began.
4. Tooling did not block the write because no approval token or digest-bound
   gate existed.
5. QA passed because it tested integrity and content rules, not authorization
   chronology and exact approval conformance.

## 5. Corrective decision implemented

`PD-45-PRE-MUTATION-GRAPH-CARD-GATES` is now `approved_active`.

### Gate G1 — graph proposal

Before a semantic graph write, the process must produce a complete,
baseline-bound proposed graph delta with stable identifiers, operations,
before/after values, evidence controls, routes, counts, zero-change
declarations, and a canonical digest. The process then stops for approval.

### Gate C1 — card proposal

Before any canonical card edit, including an edit to an existing card whose
ID and spine position do not change, the process must produce an exact
section- or line-level diff with rationale, supporting graph items, impact
classification, rollback, an unlisted-card zero-change declaration, and a
canonical digest. The process then stops for approval.

### Gate I1 — approval-bound implementation

Implementation starts only after a valid approval record binds the baseline,
inputs, proposal digests, scope, limitations, approver, and approval time.
Material drift causes a hard stop and revised approval.

### Gate R1 — conformance and packaging

A successor versioned ZIP may be created only after QA proves baseline
immutability, approval chronology, graph/card conformance, zero unapproved
semantic-file changes, lineage/currentness/audience/HITL controls, and
packaging-gate success.

## 6. Technical controls implemented in corrected v45

- Added a fail-closed standard-library utility:
  `tools/nee_pre_mutation_gate.py`.
- Added proposal, approval, and conformance templates.
- Added baseline- and digest-bound approval validation.
- Added changed-file allowlist enforcement.
- Added graph and non-policy-card zero-delta enforcement.
- Added conformance-gated deterministic ZIP creation.
- Added tests for missing approval, baseline mismatch, valid approval,
  unapproved file mutation, and packaging without pass conformance.
- Added an explicit approval record and zero-delta proposals for this
  instruction-only activation.
- Added QA controls for chronology, baseline immutability, policy mirror
  synchronization, no unapproved graph/card change, and packaging order.

## 7. Corrected v45 disposition

The corrected v45 is rebuilt from the byte-identified v44 baseline.

It changes only:

- the canonical policy card `NEE_99`;
- the byte-identical root policy mirror;
- approval, RCA, gate-tooling, test, release-control, QA, and manifest
  artifacts.

It does **not** import or ratify the prior unapproved attachment-derived graph
or card deltas. The graph database, graph migrations, all 18 non-policy cards,
ontology, schema, relationship vocabulary, and 19-card spine remain
byte-identical to v44.

The original unapproved v45 hash remains recorded as audit evidence and is
not an approved baseline.

## 8. Approval and activation

The user explicitly instructed:

> RCA the project instructions and gating and also implement the above in v45 - create v45.zip

That instruction activates the exact corrective decision in the prior RCA for
the narrow C6 control-plane scope. The activation is governed by policy 34.0,
which was active before implementation. Policy 45.0 applies prospectively
and cannot retroactively authorize the original unapproved v45 mutations.

## 9. Closure criteria and results

| Criterion | Result |
|---|---|
| Explicit corrective-decision approval | PASS |
| Policy updated in canonical and mirror locations | PASS |
| Account graph delta is zero | PASS |
| Non-policy card delta is zero | PASS |
| No-approval path fails closed | PASS |
| Baseline mismatch fails closed | PASS |
| Out-of-scope file mutation fails closed | PASS |
| Packaging requires pass conformance | PASS |
| Original unapproved v45 remains unratified | PASS |

## 10. Remaining action

The six attachment-derived graph and card changes require a new read-only G1
and C1 proposal under policy 45.0. No part of the original unapproved v45
delta should be implemented until the user approves the exact proposal
digests.
