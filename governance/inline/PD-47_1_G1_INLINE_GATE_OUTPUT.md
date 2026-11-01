## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 143 | 143 | +0 | No source records changed. | Preserves the exact governed source inventory while the release changes only review controls. |
| Nodes | 628 | 628 | +0 | No graph nodes changed. | Preserves entity identity and existing node-ranking behavior. |
| Aliases | 393 | 393 | +0 | No aliases changed. | Preserves exact-name and alternate-name matching behavior. |
| Edges — total / active | 1,021 / 995 | 1,021 / 995 | +0 / +0 | No relationships changed. | Preserves lineage and traversal results; V47.1 only makes edge deltas visible and validated. |
| Claims — total / active | 515 / 472 | 515 / 472 | +0 / +0 | No claims changed. | Preserves semantic answer content while forcing future claim changes into a visible, reconciled table. |
| Validation items | 171 | 171 | +0 | No validation items changed. | Preserves currentness and HITL behavior while requiring future movements to be shown inline. |
| Node-to-card mappings | 1,555 | 1,555 | +0 | No routes changed. | Preserves current card retrieval routes while making future route additions visible in both graph and card tables. |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.00% → 0.00% (+0.00 pp) | ≤5% and no increase | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Duplicate node count | 0 → 0 (+0) | 0 new high-impact duplicates | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Duplicate edge rate | 0.00% → 0.00% (+0.00 pp) | ≤1% unexplained and no increase | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Rare edge-type rate | 5.13% → 5.13% (+0.00 pp) | ≤25% or no increase | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Claim completeness | 100% → 100% (+0.00 pp) | 100% | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Source metadata completeness | 97.90% → 97.90% (+0.00 pp) | ≥95%; 100% for material sources | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Temporal completeness | 95.33% → 95.33% (+0.00 pp) | ≥90% and no decrease | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| High-impact identity collisions | 0 → 0 (+0) | 0 unresolved | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| P0/P1 SLA breach rate | 0.00% → 0.00% (+0.00 pp) | 0% | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |
| Decision-safe answer regression | 0/5 → 0/5 (+0) | 0 regression on approved test set | PASS | Zero account-graph delta; the baseline value is preserved and the row is now mandatory in generated output. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_99 | Add Gate O1, the exact three-table contract, machine-readable output fields, approval/conformance hash binding, and packaging enforcement. | control_policy | The user required exactly three inline outputs and explicitly requested V47.1 with gates and inline output gates. | Forces every future graph, metric, card, and route change into a compact visible decision surface, reducing omitted routing, disambiguation, and retrieval-impact evidence. |
