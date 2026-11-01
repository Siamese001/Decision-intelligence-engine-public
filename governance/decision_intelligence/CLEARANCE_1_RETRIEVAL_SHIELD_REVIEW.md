# Clearance 1 — Retrieval Shield

## Outcome

Complete. Retrieval now fails closed for the known W1 boundary violations while the graph migration remains unapplied.

The shield denies:

- derived assertion types: `human_interpretation`, `recommendation`, `hypothesis`, and `pov`;
- client and restricted/private audience combinations;
- confidential or restricted records exposed to a client audience;
- malformed audience metadata;
- `private_personal` audience labels present in the NEE graph.

This is a query-time safety control. It does not change `canonical_status`, `retrieval_eligible`, graph assertions, sidecars, embeddings, approvals, or releases.

## Implementation

- [retrieval clearance policy](/C:/Git/NEE-Knowledge-Graph/dual_pipeline/retrieval_clearance.py)
- [render-time shield](/C:/Git/NEE-Knowledge-Graph/dual_pipeline/index.py)
- [query-time shield for existing sidecars](/C:/Git/NEE-Knowledge-Graph/dual_pipeline/graph_retrieval.py)
- [regression tests](/C:/Git/NEE-Knowledge-Graph/tests/test_retrieval_clearance.py)

Existing sidecars are protected by looking up the bound graph assertion metadata at query time. This closes the gap where an old sidecar may not yet contain the new assertion-type extension.

## Runtime proof

| Check | Result |
|---|---:|
| Active graph records rendered | 1,104 |
| Derived records rendered | 0 |
| Mixed client/restricted records rendered | 0 |
| Confidential/restricted client records rendered | 0 |
| Existing active sidecar authorized records checked | 525 |
| Derived records selected from existing sidecar | 0 |
| Focused regression tests | 6/6 passed |
| Contract validation | PASS (93 schemas/fixtures; 0 provider calls; 0 runtime mutations) |
| Provider calls | 0 |
| Graph mutations | 0 |
| Release activation | false |

The active graph SHA256 remained unchanged:

```text
5CE434C5CB819B66BBD39278573FDA0645C7EDF0377E132B138BF7AC86F9F184
```

## What remains open

The shield prevents exposure; it does not resolve the underlying 17 derived assertions, 3 mixed-audience records, or 2 confidential-client records. Those remain exact-scope migration candidates for clearance step 3 and the approval-bound lifecycle.

Next clearance step: repair or quarantine the strict-audit lineage, HITL, and alias debt.
