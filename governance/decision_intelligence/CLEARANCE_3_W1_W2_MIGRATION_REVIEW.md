# Clearance 3 — W1/W2 Migration Proposal

## Outcome

The exact W1/W2 migration proposal is prepared and digest-bound. It is draft-only; no graph rows, sidecars, approvals, or releases were changed.

## Proposal

[Clearance 3 migration proposal](/C:/Git/NEE-Knowledge-Graph/.local/clearance3-w1-w2-migration-20260809/migration-proposal.json)

- Plan digest: `cd9189f55697c4c66b1bff7a83d49b03b013c4b773c4c6bd0b0796d8b46d4070`
- Proposal file SHA256: `73992E951462739EF138B34C87D28FA9CDA64F6314BF2569D072AF7B78F22DAB`
- Base graph SHA256: `5CE434C5CB819B66BBD39278573FDA0645C7EDF0377E132B138BF7AC86F9F184`
- `graph_mutation_authorized=false`

## Exact scope

| Area | Count | Proposed treatment | Apply posture |
|---|---:|---|---|
| Derived current/retrieval assertions | 17 | Preserve evidence, move graph record to `evidence_only`, clear canonical/retrieval eligibility, create a separate derived artifact reference. | HITL required |
| Client/restricted audience mixtures | 3 | Remove client audience labels; retain restricted/internal scope. | Apply-ready |
| Confidential/restricted client exposure | 2 | Remove client audience labels; retain non-client scope. | Apply-ready |
| Unparsed legacy assertions | 459 | Preserve legacy rows; set retrieval eligibility to 0. 44 currently exposed candidates would change; the remainder are already quarantined. | 44 apply-ready |
| `OWNS_OR_LEADS` | 45 | Hold for explicit `OWNS`, `LEADS`, or provenance-only decision. | HITL required |
| `RELATED_TO` | 3 | Hold for a typed relation decision or provenance-only treatment. | HITL required |
| `HAS_CLAIM_TEXT` | 677 | Retain as provenance-only; do not treat as a semantic business edge. | No row rewrite |
| Future target node types | 12 | Defer to W3/W5/W6 ownership waves. | No action in this proposal |

Summary: 529 proposal operations; 49 apply-ready; 65 require human decision.

## Safety shield added

The retrieval clearance now also blocks `unparsed_legacy_text` assertions. This protects existing sidecars before any approved graph migration.

Runtime probe against the active graph/sidecar:

- Rendered records: 976.
- Unparsed legacy records rendered: 0.
- Derived records rendered: 0.
- Missing-lineage records rendered: 0.
- Incomplete-HITL records rendered: 0.
- Existing sidecar authorized records: 528.
- Unsafe records selected from existing sidecar: 0.
- Focused tests: 9/9 passed.
- Contract validation: PASS (93 schemas/fixtures; 0 provider calls; 0 runtime mutations).

## Approval gate

This proposal is not a graph mutation authorization. To apply it, the operator must:

1. complete HITL decisions for the 17 derived records and 48 compound/generic relation records;
2. bind the exact proposal digest and graph SHA above to an approval;
3. execute the governed release lifecycle and completion gate; and
4. re-run W0, W1, and W2 audits.

Until then, the proposal remains reviewable evidence and the retrieval shield remains the active protection.
