## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 173 | 174 | 1 | Added one direct user-correction source; strengthen the June 15 EDO source posture and notes. | Provides exact provenance for the authority, Pod 4 retirement, and enterprise-boundary correction. |
| Source versions | 173 | 174 | 1 | Added one hash-bound user source version; update the June 15 version scope/posture. | Makes source ranking, scope, date precision, and current-authority traversal explicit. |
| Evidence items | 1614 | 1617 | 3 | Added three exact proposition-level evidence items from the July 14 instruction. | Lets every corrected authority/boundary assertion cite the exact user-confirmed proposition. |
| Nodes (total / active) | 652 / 647 | 652 / 646 | 0 / -1 | Updated EDO, NEE, NEE IT, and June-source nodes; retire the Pod 4 umbrella from active retrieval. | Stops exact node lookup from treating Pod 4 as current and makes EDO’s enterprise-wide boundary explicit. |
| Node states (total / current eligible) | 652 / 647 | 652 / 646 | 0 / -1 | Updated five authority/currentness states; Pod 4 becomes historical-only and the June source becomes a connected controlling source. | Removes Pod 4 from current retrieval while retaining lineage and makes the June source eligible as an authoritative graph context. |
| Legacy aliases | 429 | 429 | 0 | Reclassify two Pod 4 alias rows as historical lineage; row count is unchanged. | NA |
| Canonical aliases (total / active) | 424 / 424 | 424 / 422 | 0 / -2 | Deactivated the canonical “Pod 4: Data Products” and “EDO Data Products” aliases effective June 15. | Prevents exact alias resolution from returning the obsolete umbrella as a current workstream. |
| Edges (total / unsuperseded) | 1069 / 1041 | 1071 / 1033 | 2 / -8 | Added the enterprise-wide NEE→EDO boundary and June-source context edges; supersede two under-IT, three Pod 4, and five March snapshot edges. | Current traversal now reaches exactly the June 15 eight-workstream view and no longer traverses EDO as subordinate to NEE IT. |
| Claims (total / unsuperseded) | 580 / 520 | 583 / 519 | 3 / -1 | Added three corrected claims, strengthen two existing claims, and supersede four obsolete current claims. | Current claim retrieval returns the enterprise boundary, June 15 authority, and Pod 4 retirement instead of dated umbrella framing. |
| Assertions (total / retrieval eligible) | 1682 / 1563 | 1687 / 1553 | 5 / -10 | Added five accepted canonical assertions, promote the June 15 authority assertion, reconcile one historical whiteboard assertion, and supersede fourteen conflicting assertions. | Decision-safe retrieval now excludes subordinate/Pod 4/March-snapshot assertions and promotes the controlling authority facts. |
| Assertion-evidence links | 1720 | 1729 | 9 | Added nine support links across the five new assertions and the promoted June 15 authority assertion. | Preserves direct citation chains for every corrected high-materiality assertion. |
| Claim-assertion lineage maps | 580 | 583 | 3 | Added three compatibility mappings for the new claims. | Keeps legacy claim IDs and canonical assertions traversable together. |
| Edge-assertion lineage maps | 1069 | 1071 | 2 | Added two compatibility mappings for the new boundary/source-context edges. | Keeps new edge IDs linked to their canonical assertions for graph and compatibility queries. |
| Canonical routes (total / active) | 3940 / 3939 | 3948 / 3943 | 8 / 4 | Added eight claim/assertion routes and deactivate four Pod 4 node routes. | Routes corrected facts to NEE_10/NEE_13 and removes the obsolete umbrella from portfolio, reuse, pattern, and provenance destinations. |
| Node-to-card mappings | 1628 | 1624 | -4 | Removed four derived Pod 4 node-card mappings when their routes are deactivated. | Eliminates stale current card destinations for the retired umbrella while the inactive route rows preserve audit lineage. |
| Validations | 184 | 184 | 0 | Resolved EDO placement and close Pod 4 reconciliation; retain only exact-title/lineage validation where still open. | NA |
| Search documents (total / eligible) | 2262 / 1346 | 2271 / 1352 | 9 / 6 | Added nine corrected claim/assertion projections, update twenty-one affected projections, and suppress superseded eligible projections. | Lexical and FTS retrieval converge on enterprise-wide EDO, June 15 authority, and the eight direct workstreams. |
| Cards | 19 | 19 | 0 | No graph card-row count change; C1 implemented three body/frontmatter revisions and three route-only impacts. | NA |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | No operational orphan is introduced. The June 15 source is connected to EDO; the retired Pod 4 node is historical-only, not an operational orphan. |
| Duplicate node count | 5 candidate groups → 5 candidate groups (+0; 0 new high-impact duplicates) | 0 new high-impact duplicates | PASS | No node or identity is merged or added. Pod 4 is retired in place so its historical identity remains auditable. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | The two new relationships use distinct registered predicates and subjects; conflicting old edges are superseded rather than duplicated. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | No new relationship vocabulary is introduced; HAS_HORIZONTAL_LAYER/CONTAINS and DATED_SNAPSHOT_OF/CONTEXT_FOR are already registered families. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | All 583 implemented claims retain subject, source, card route, confidence, HITL posture, evidence, and assertion lineage. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | The new user source/version is hash-bound with exact date, scope, posture, authority rank, and cold pointer; June 15 metadata remains complete. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | June 15 controls retirement/current-workstream dates; July 14 controls the direct enterprise-boundary correction. Historical assertions receive explicit valid-to dates. |
| High-impact identity collisions | 0 unresolved → 0 unresolved (+0) | 0 unresolved | PASS | No person or organization identity resolution is changed. The Pod 4 umbrella is retired, not merged into any one current workstream. |
| P0/P1 SLA breach rate | 0.0000% → 0.0000% (+0.0000 pp) | 0% | PASS | The two affected validation rows remain closed or explicitly scoped; no new open P0/P1 item is created. |
| Decision-safe answer regression | 0 failures / 21 cases; 0/9 targeted checks → 0 failures / 21 cases; 9/9 targeted checks (+0 suite failures; +9 targeted passes) | 0 regression on approved test set | PASS | All 21 permanent semantic cases and the V54 authority count-lock pass on the implemented graph; the nine targeted EDO authority/boundary checks pass 9/9. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_02 | Deactivated the retired Pod 4 node route; no card-body bytes change. | route | Direct user instruction to remove Pod 4 from current graph retrieval; June 15 direct workstreams remain routed. | Prevents portfolio/status routing from resolving through the obsolete umbrella. |
| NEE_06 | Corrected historical operating-model wording: EDO is enterprise-wide across NEE and not subordinate to NEE IT. | body+frontmatter | Direct user boundary correction; historical whiteboard lineage remains explicitly labeled. | Makes org-boundary synthesis decision-safe while preserving historical context. |
| NEE_10 | Corrected Josh Millar / EDO treatment and route the new confirmed leadership claim. | body+frontmatter+route | Existing user-confirmed Josh leadership plus the July 14 correction that EDO is not under NEE IT. | Exact stakeholder/org lookup no longer returns the false subordinate relationship. |
| NEE_11 | Deactivated the retired Pod 4 reuse route; no card-body bytes change. | route | Pod 4 is historical-only; reuse retrieval should resolve to direct GridOpti, Atlas, Trade Floor, Digital Thread, and E&C patterns. | Removes umbrella routing ambiguity from architecture-pattern traversal. |
| NEE_13 | Established June 15 as controlling current EDO authority, retire Pod 4 current treatment, and harden EDO as enterprise-wide across NEE. | body+frontmatter+route | Direct July 14 user correction plus the formal June 15 EDO status source and existing eight-workstream evidence. | Currentness ranking, exact-match lookup, and graph traversal converge on the eight direct workstreams and correct enterprise boundary. |
| NEE_16 | Deactivated the retired Pod 4 provenance route while preserving historical source lineage; no card-body bytes change. | route | Retirement preserves source evidence but removes the obsolete node from current route resolution. | Keeps provenance auditable without making the historical umbrella a current retrieval destination. |
