## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 174 | 174 | 0 | No semantic graph change. | NA |
| Source versions | 174 | 174 | 0 | No semantic graph change. | NA |
| Evidence items | 1617 | 1617 | 0 | No semantic graph change. | NA |
| Nodes | 652 | 652 | 0 | No semantic graph change. | NA |
| Node states | 652 | 652 | 0 | No semantic graph change. | NA |
| Aliases | 429 | 429 | 0 | No semantic graph change. | NA |
| Canonical aliases | 424 | 424 | 0 | No semantic graph change. | NA |
| Edges | 1071 | 1071 | 0 | No semantic graph change. | NA |
| Claims | 583 | 583 | 0 | No semantic graph change. | NA |
| Assertions | 1687 | 1687 | 0 | No semantic graph change. | NA |
| Assertion-evidence links | 1729 | 1729 | 0 | No semantic graph change. | NA |
| Claim-assertion lineage maps | 583 | 583 | 0 | No semantic graph change. | NA |
| Edge-assertion lineage maps | 1071 | 1071 | 0 | No semantic graph change. | NA |
| Canonical routes | 3948 | 3948 | 0 | No semantic graph change. | NA |
| Node-to-card mappings | 1624 | 1624 | 0 | No semantic graph change. | NA |
| Validations | 184 | 184 | 0 | No semantic graph change. | NA |
| Search documents | 2271 | 2271 | 0 | No semantic graph change. | NA |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | Protocol-only proposal; the account graph is byte-identical and no orphan can be introduced. |
| Duplicate node count | 5 quarantined candidate groups → 5 quarantined candidate groups (+0; 0 new high-impact duplicates) | 0 new high-impact duplicates | PASS | No node, identity, or alias operation is proposed. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | No edge or relationship operation is proposed. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | No ontology or relationship vocabulary changes. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | No claim or assertion operation is proposed. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | No canonical source is added or changed; the user instruction is bound only as review-bundle input evidence. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | No time-sensitive graph proposition changes. |
| High-impact identity collisions | 0 unresolved canonical collisions → 0 unresolved canonical collisions (+0) | 0 unresolved | PASS | No identity resolution or merge is proposed. |
| P0/P1 SLA breach rate | 0.0000% (0/114 undated) → 0.0000% (0/114 undated) (+0.0000 pp) | 0% | PASS | No validation queue operation is proposed. |
| Decision-safe answer regression | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | 0 regression on approved test set | PASS | The graph and current retrieval corpus remain unchanged; activation testing will add focused policy-behavior cases at R1. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_99 | Mandatory post-synthesis HITL clarification prompts for every detected unresolved inconsistency, overlap, and open question; explicit no-issue declaration; QA and answer-packet contracts. | policy body + frontmatter + generated root mirror | Direct user request dated 2026-07-14; closes the control gap where ambiguity can remain implicit after synthesis. | Makes update and query outputs expose unresolved boundaries immediately after synthesis instead of silently collapsing them. |
