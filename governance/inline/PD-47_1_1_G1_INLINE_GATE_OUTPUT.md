## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 143 | 143 | +0 | No source records changed. | NA |
| Nodes | 628 | 628 | +0 | No graph nodes changed. | NA |
| Aliases | 393 | 393 | +0 | No aliases changed. | NA |
| Edges — total / active | 1,021 / 995 | 1,021 / 995 | +0 / +0 | No relationships changed. | NA |
| Claims — total / active | 515 / 472 | 515 / 472 | +0 / +0 | No claims changed. | NA |
| Validation items | 171 | 171 | +0 | No validation items changed. | NA |
| Node-to-card mappings | 1,555 | 1,555 | +0 | No routes changed. | NA |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.00% → 0.00% (+0.00 pp) | ≤5% and no increase | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Duplicate node count | 0 → 0 (+0) | 0 new high-impact duplicates | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Duplicate edge rate | 0.00% → 0.00% (+0.00 pp) | ≤1% unexplained and no increase | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Rare edge-type rate | 5.13% → 5.13% (+0.00 pp) | ≤25% or no increase | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Claim completeness | 100% → 100% (+0.00 pp) | 100% | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Source metadata completeness | 97.90% → 97.90% (+0.00 pp) | ≥95%; 100% for material sources | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Temporal completeness | 95.33% → 95.33% (+0.00 pp) | ≥90% and no decrease | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| High-impact identity collisions | 0 → 0 (+0) | 0 unresolved | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| P0/P1 SLA breach rate | 0.00% → 0.00% (+0.00 pp) | 0% | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |
| Decision-safe answer regression | 0/5 → 0/5 (+0) | 0 regression on approved test set | PASS | Zero account-graph delta; V47.1 values remain unchanged and Gate O1 enforcement passed. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_99 | Make Graph Delta rationale delta-aware: render NA for every all-zero scalar or vector delta and reject NA/N/A for any non-zero delta; add five regression tests. | control_policy | The user directed that unchanged graph rows should use NA and explicitly instructed “V47.1.1 - generate” after the enforcement gap was identified. | Removes non-informative rationale prose from zero-change rows while preserving mandatory mechanism-specific rationale for material graph changes, making retrieval-impact review faster and safer. |
