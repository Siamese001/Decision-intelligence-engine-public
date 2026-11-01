## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 148 | 161 | 13 | Add 13 distinct external public sources with authority, currentness, independence, access and use boundaries. | Exact source identifiers, URLs and immutable version metadata support deterministic provenance and deduplication. |
| Source versions | 148 | 161 | 13 | Add one immutable metadata/version record for each proposed external source. | Version fingerprints support exact evidence-chain traversal without embedding raw copyrighted web content. |
| Nodes — total / active | 632 / 628 | 632 / 628 | 0 / 0 | No node additions, merges, retirements or status changes; public entities are mapped only to existing graph objects. | NA |
| Aliases | 393 | 393 | 0 | No alias or identity-resolution change. | NA |
| Edges — total / active | 1026 / 1000 | 1026 / 1000 | 0 / 0 | No legacy or canonical entity relationship change. | NA |
| Claims — total / active | 520 / 477 | 520 / 477 | 0 / 0 | No legacy claim-table change; proposed content remains in candidate assertion/evidence architecture. | NA |
| Validation items | 174 | 174 | 0 | No validation status, owner, due-date or resolution change. The complete open queue is classified only in review exports. | NA |
| Node-to-card mappings | 1566 | 1566 | 0 | No node-card mapping change. | NA |
| Canonical assertion rows | 1548 | 1566 | 18 | Add 18 atomic candidate assertions that separate public fact, announcement, empirical result, outcome boundary and reference practice. | Subject-indexed candidate assertions permit exact source-attributed review while canonical and authoritative retrieval eligibility remain zero. |
| Evidence items | 1560 | 1578 | 18 | Add one exact URL/title locator for each proposed assertion. | Evidence locators provide deterministic claim-to-source lineage and expose authority/use boundaries. |
| Assertion-evidence links | 1560 | 1578 | 18 | Link each candidate assertion to one retained evidence item; no independent corroboration is overstated. | Direct support links make the evidence chain queryable and preserve single-lineage status. |
| Canonical routes | 3634 | 3652 | 18 | Add primary candidate routes to NEE_01, NEE_06, NEE_07, NEE_09 and NEE_10. | Route-specific retrieval surfaces public facts and external practices from established card homes without duplicating or editing card prose. |
| Search documents | 2108 | 2126 | 18 | Add candidate review projections with retrieval_eligible=0. | Deterministic review projections support direct candidate inspection while authoritative FTS and decision-safe answers remain unchanged. |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | No nodes or entity relationships are added; every candidate assertion uses an existing active subject and a primary card route. |
| Duplicate node count | 6 quarantined candidate groups → 6 quarantined candidate groups (+0 high-impact duplicates) | 0 new high-impact duplicates | PASS | No node, alias or identity operation is proposed; public names remain source metadata or map to already existing organization nodes. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | No edge is added. Exact source-title/URL checks found no proposed source duplicate, and candidate assertion fingerprints are unique. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | No relationship vocabulary or entity edge is added; assertions use registered literal predicate HAS_CLAIM_TEXT. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | Legacy claims are unchanged. Every proposed assertion has subject, source/version, exact evidence, route, confidence, temporal posture, audience, HITL and use boundary. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | All 13 proposed material sources include organization, title, type, date posture, retrieval date, URL, scope, authority, independence, currentness, access and use limitations. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | Each proposed assertion records valid_from, date precision/status and current/future/historical posture; planned milestones are not promoted as completed. |
| High-impact identity collisions | 0 unresolved canonical collisions → 0 unresolved canonical collisions (+0) | 0 unresolved | PASS | No person identity, public-profile match, organization merge, alias, role, reporting line or decision-right operation is proposed. |
| P0/P1 SLA breach rate | 0/110 breaches (0.0000%) → 0/110 breaches (0.0000%) (+0 breaches) | 0% | PASS | No validation row changes. The review bundle preserves all open P0/P1 items, due dates and accountable owners in the routing and internal-evidence queues. |
| Decision-safe answer regression | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | 0 regression on approved test set | PASS | Simulation preserves baseline decision-safe and authoritative FTS eligibility because all new assertions and search projections have retrieval_eligible=0. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_01 | Route current public corporate-strategy facts on the announced Apex Global-Dominion transaction. | route | Public transaction reporting materially contextualizes the account thesis, but route-only treatment prevents premature synthesis or closing inference. | Assertion routes expose the exact announcement, approval-dependent status and stated rationale from the executive account home. |
| NEE_06 | Route bounded Product Owner accountability evidence from the Scrum Guide. | route | The card already owns the operating model; v42 already covers user-needs and multidisciplinary practices, so only the incremental accountability source is added. | The route retrieves the external reference next to existing internal/candidate operating-model evidence without rewriting policy. |
| NEE_07 | Route bounded AI productivity experiments and human-in-the-loop nuclear-AI evidence. | route | The card owns AI SDLC and evaluation; conflicting RCTs and nuclear document-control boundaries improve evidence discipline without claiming NEE adoption. | Routes support study-specific comparison and keep candidate evidence outside decision-safe retrieval. |
| NEE_09 | Route current public Apex Global-Google, nuclear, grid-operator and grid-interactive data-center evidence. | route | The card owns utility thought leadership and public strategy context; status labels prevent announcements from becoming proven outcomes. | Routes permit peer/public-fact retrieval by exact assertion, status and source while preserving public/private boundaries. |
| NEE_10 | Route OPM structured-interview and work-sample assessment controls. | route | The card owns credentialing and open questions; external assessment design can inform a framework but cannot validate named screeners or current staffing authority. | Routes make the controls discoverable from credentialing while keeping current staffing decisions review-gated. |
