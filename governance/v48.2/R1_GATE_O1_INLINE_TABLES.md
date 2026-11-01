## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 147 | 148 | 1 | Add one distinct confidential internal PPTX source; do not merge it with the v17 curated staffing source. | Exact source-table lookup and immutable provenance distinguish the direct 2026 deck from prior curated synthesis. |
| Source versions | 147 | 148 | 1 | Add one immutable source version with raw-artifact and normalized-content fingerprints. | Fingerprint and content hash enable exact version identity, deduplication, and evidence-chain traversal. |
| Nodes — total / active | 632 / 628 | 632 / 628 | 0 / 0 | No node additions, merges, status changes, or retirements. | NA |
| Aliases | 393 | 393 | 0 | No legacy or canonical alias additions; named people remain literal source mentions. | NA |
| Edges — total / active | 1026 / 1000 | 1026 / 1000 | 0 / 0 | No legacy relationship additions or changes. | NA |
| Claims — total / active | 520 / 477 | 520 / 477 | 0 / 0 | No legacy claim additions or changes; ingestion is canonical-candidate-only. | NA |
| Validation items | 173 | 174 | 1 | Add one owned, dated P1 item for staffing/hiring identities, effective date, and decision authority. | Safety gating blocks current/official use of slide-8 decision-right claims until named authority and effective date are confirmed. |
| Node-to-card mappings | 1566 | 1566 | 0 | No node-to-card mapping changes. | NA |
| Canonical assertion rows | 1534 | 1548 | 14 | Add 14 atomic, source-attributed candidate assertions with currentness unknown. | Subject-indexed candidate assertions add precise product-engineering, delivery-model, role, screening, and travel retrieval without entering decision-safe views. |
| Evidence items | 1546 | 1560 | 14 | Add one exact slide locator for each proposed candidate assertion. | Exact slide locators provide citation-grade lineage and allow claim-by-claim inspection. |
| Assertion-evidence links | 1546 | 1560 | 14 | Link each candidate assertion to its direct slide evidence; no independent corroboration claimed. | Direct links make support chains deterministic while preserving same-source/non-independent posture. |
| Canonical routes | 3620 | 3634 | 14 | Add ten primary candidate routes to NEE_06 and four to NEE_10. | Card routing surfaces operating-model evidence in NEE_06 and talent/identity evidence in NEE_10 without card-body duplication. |
| Search documents | 2094 | 2108 | 14 | Add candidate review projections; keep authoritative FTS eligibility at zero. | Candidate review queries gain deterministic titles/bodies while authoritative FTS ranking remains unchanged. |
| P1 validation sprint items | 96 | 97 | 1 | Add the staffing-authority validation to a coherent talent-governance lane. | Lane-based retrieval groups the identity/authority question with near-term staffing decisions. |
| P0/P1 action-register rows | 93 | 94 | 1 | Add one owner/due-date action to obtain current authoritative evidence. | Owner and due-date retrieval exposes the exact evidence request and blocked use. |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | No nodes or entity relationships are added; candidate literal assertions and routes do not create operational node orphans. |
| Duplicate node count | 6 quarantined candidate groups → 6 quarantined candidate groups (+0 high-impact duplicates) | 0 new high-impact duplicates | PASS | No person, location, role, or source node is created; the new artifact remains a source-table record and named people remain unlinked literals. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | Legacy edges are unchanged and no entity-object assertion is added; each candidate assertion has one distinct fingerprint. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | No new relationship vocabulary or entity relationship is introduced; all new assertions use the registered literal predicate HAS_CLAIM_TEXT. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | The legacy claim layer is unchanged; every proposed candidate assertion separately includes subject, source/version, route, confidence, HITL, temporal posture, and exact evidence. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | The new material source has artifact and content hashes, immutable version fingerprint, date precision/status, authority rank, scope, posture, and cold-archive pointer. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | All proposed assertions record the document snapshot date and estimated date status, while currentness remains unknown; no dated source is promoted as current independent truth. |
| High-impact identity collisions | 0 unresolved canonical collisions → 0 unresolved canonical collisions (+0; four unlinked full-name mentions remain review-gated) | 0 unresolved | PASS | No person node, alias, affiliation, or decision-right edge is created. Slide-8 names remain literal source mentions behind one P1 validation. |
| P0/P1 SLA breach rate | 0/109 breaches (0.0000%) → 0/110 breaches (0.0000%) (+0 breaches) | 0% | PASS | The new P1 item has one owner (Amit Ayer), a due date of 2026-07-24, required evidence, blocked use, and a coherent sprint/action lane. |
| Decision-safe answer regression | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | 0 regression on approved test set | PASS | All 21 cases pass on the simulated database. New assertions remain candidate/currentness-unknown and are excluded from authoritative FTS and decision-safe views. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_06 | Route 10 source-attributed candidate assertions covering product-first behavior, visible/no-surprises delivery, capacity versus PartnerAdvisory-led lanes, the Solutions Lead/EM/Tech Lead pattern, and working norms. | route | Slides 4-7 provide direct internal briefing evidence, while the card already owns the co-delivery/co-hydrated operating-model topic; no prose duplication is warranted. | Primary assertion-to-card routes surface exact slide evidence and dated source posture from the established operating-model home. |
| NEE_10 | Route 4 candidate assertions covering the technical/interview bar, named staffing-screening process, and travel expectation, plus one P1 identity/authority validation. | route | Slides 5 and 8 directly support the source-attributed talent details, but named decision rights and current location/travel expectations are not authoritative without validation. | Routes make talent and identity-sensitive evidence discoverable from the stakeholder/credentialing home while the P1 gate blocks official-current use. |
