# Clearance 2 — Lineage, HITL, and Alias Debt

## Outcome

The remediation is prepared and the query-time shield is active, but the graph-level quarantine is **not applied**. Applying it requires a separate exact-scope graph-mutation approval.

## Exact baseline

Active graph SHA256:

```text
5CE434C5CB819B66BBD39278573FDA0645C7EDF0377E132B138BF7AC86F9F184
```

Strict audit digest:

```text
5220B1C41AA1C7BAA0F7B72D12072DEBC3780C77D1641830E4985466664A56D3
```

| Debt | Count | Exact treatment |
|---|---:|---|
| Non-legacy assertions without `extraction_run_id` | 231 | Quarantine: preserve the row, demote canonical rows to `validation_pending`, set `canonical_eligible=0`, `retrieval_eligible=0`, and set `validation_state=needs_review`. No lineage is fabricated. |
| HITL assignment incomplete | 1 | Hold for a real human owner; proposed priority `P1`; no apply until the owner is supplied. |
| Active alias with unregistered confidence | 1 | Quarantine by setting `active=0`, `review_state=ambiguous_blocked`, and `preferred_flag=0`; no deletion. |

## Draft artifact

[Clearance 2 remediation draft](/C:/Git/NEE-Knowledge-Graph/.local/clearance2-lineage-hitl-alias-20260808/remediation-draft.json)

- Plan digest: `f5b9ce2594d2888595e8b16a803cda40c4b52e7d48e7bf6379037ed91f7d9030`
- 232 apply-ready quarantine operations.
- 1 human-decision operation, not apply-ready.
- Draft-only: `graph_mutation_authorized=false`.

The incomplete HITL record is:

```text
asrt_claim_v52_nea_ai_usage_indicator_boundary_2026_07_06
```

The unregistered alias is:

```text
calias_108eb00d3acef369a2f071e6
TBD_OFFICIAL_NAME -> PGD CoPilot
confidence_label=unknown
```

## Runtime proof

- Query-time retrieval shield selected zero records with missing non-legacy lineage, incomplete HITL assignment, derived assertion types, unsafe audience mixtures, or confidential client exposure.
- Rendered active retrieval records: 1,020 after the shield.
- Existing sidecar authorized records checked: 528; unsafe records selected: 0.
- Focused tests: 8/8 passed.
- Contract validation: PASS (93 schemas/fixtures; 0 provider calls; 0 runtime mutations).
- Graph SHA256 after preparation: unchanged at `5CE434C5...86F9F184`.

## What is still required to close step 2

1. Supply the real HITL owner for the one held record.
2. Approve the exact plan digest above against the exact graph SHA.
3. Run the governed graph mutation lifecycle and completion gate.
4. Re-run the strict label audit; the 233 blocking violations should reach zero.

Until then, the unsafe records are protected at retrieval time but remain present in the graph for review and auditability.
