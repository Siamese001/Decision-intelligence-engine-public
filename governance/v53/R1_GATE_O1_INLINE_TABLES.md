## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 172 | 173 | 1 | Implemented exactly: +1 row(s); exact adds/updates are listed in the G1 operation inventory. | Adds exact-match retrieval and provenance for the July 13 Sherpa discussion with scoped authority and a cold pointer. |
| Source versions | 172 | 173 | 1 | Implemented exactly: +1 row(s); exact adds/updates are listed in the G1 operation inventory. | Binds content hash, date precision, authority, scope, and posture so lineage traversal reaches the exact source version. |
| Evidence items | 1602 | 1614 | 12 | Implemented exactly: +12 row(s); exact adds/updates are listed in the G1 operation inventory. | Adds exact line-level evidence locators for participant-attributed Sherpa evaluation, SDLC, visibility, and velocity statements. |
| Nodes | 652 | 652 | 0 | Implemented exactly: no row-count change. | NA |
| Node states | 652 | 652 | 0 | Implemented exactly: no row-count change. | NA |
| Aliases | 429 | 429 | 0 | Implemented exactly: no row-count change. | NA |
| Canonical aliases | 424 | 424 | 0 | Implemented exactly: no row-count change. | NA |
| Edges | 1069 | 1069 | 0 | Implemented exactly: no row-count change. | NA |
| Claims | 568 | 580 | 12 | Implemented exactly: +12 row(s); exact adds/updates are listed in the G1 operation inventory. | Adds source-attributed and explicitly marked synthesis statements that answer Sherpa eval maturity, nearline/runtime classification, delivery evidence, and scorecard questions. |
| Assertions | 1670 | 1682 | 12 | Implemented exactly: +12 row(s); exact adds/updates are listed in the G1 operation inventory. | Makes each new claim evidence-linked, temporally scoped, currentness-aware, and decision-safe for typed graph retrieval. |
| Assertion-evidence links | 1697 | 1720 | 23 | Implemented exactly: +23 row(s); exact adds/updates are listed in the G1 operation inventory. | Enables citation and lineage traversal from each assertion to exact July 13 lines and existing Brien Muschett evidence. |
| Claim-assertion lineage maps | 568 | 580 | 12 | Implemented exactly: +12 row(s); exact adds/updates are listed in the G1 operation inventory. | Preserves compatibility lookup from legacy claim IDs to canonical assertion IDs. |
| Edge-assertion lineage maps | 1069 | 1069 | 0 | Implemented exactly: no row-count change. | NA |
| Canonical routes | 3890 | 3940 | 50 | Implemented exactly: +50 row(s); exact adds/updates are listed in the G1 operation inventory. | Routes every new claim and assertion to its primary and supporting card homes for deterministic synthesis retrieval. |
| Node-to-card mappings | 1628 | 1628 | 0 | Implemented exactly: no row-count change. | NA |
| Validations | 182 | 184 | 2 | Implemented exactly: +2 row(s); exact adds/updates are listed in the G1 operation inventory. | Updates existing Sherpa evidence questions and adds owned, dated closures for the shared velocity scorecard and unresolved Brian/Brien identity. |
| Search documents | 2250 | 2262 | 12 | Implemented exactly: +12 row(s); exact adds/updates are listed in the G1 operation inventory. | Indexes the new canonical claims for lexical retrieval of offline harness, nightly evaluation, runtime constraints, foundational work, and KPI gaps. |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | No nodes are added; claims attach to existing Sherpa and AI-SDLC nodes. |
| Duplicate node count | 5 candidate groups → 5 candidate groups (+0; 0 new high-impact duplicates) | 0 new high-impact duplicates | PASS | No node or alias is added. Andy Kilinskis, Lillian Le, Amit Ayer, and Brien Muschett reuse existing identities; Brian remains unresolved. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | No edge is added or changed. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | No relationship vocabulary or edge family is added. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | All 12 implemented claims have a subject, source, confidence, HITL, evidence, canonical assertion map, and active route. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | The new material source has a 64-character fingerprint and content hash, exact date posture, authority rank, scope, and allowed-use posture. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | All new time-sensitive assertions use the exact 2026-07-13 meeting date and retain source-attributed currentness boundaries. |
| High-impact identity collisions | 0 → 0 (+0) | 0 unresolved | PASS | No Brian alias or identity mapping is implemented; an owned validation keeps the spelling unresolved. |
| P0/P1 SLA breach rate | 0.0000% (0/112 undated) → 0.0000% (0/114 undated) (+0.0000 pp; +2 open items, all owned and dated) | 0% | PASS | The two new P1 items have named owners and valid due dates; existing P0/P1 hygiene is preserved. |
| Decision-safe answer regression | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | 0 regression on approved test set | PASS | All 21 permanent retrieval cases pass on the baseline and simulated proposal; five focused Sherpa supplemental queries also pass. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_07 | Reframe the gap using July 13 evidence; distinguish existing offline/nightly practice from unresolved in-path runtime assurance and add the shared scorecard implication. | body+frontmatter+route | Lillian Le and Andy Kilinskis meeting evidence plus existing Brien Muschett feedback; source attribution and artifact gaps remain explicit. | Directly answers AI-SDLC maturity, offline-versus-runtime classification, and evaluation-control questions from the canonical eval card. |
| NEE_12 | Add platform lessons on in-house eval ownership, invisible foundational work, and evidence required for reported AI-assisted cycle-time gains. | body+frontmatter+route | July 13 participant-attributed statements; no independent metric or vendor-status inference. | Improves platform-pattern retrieval for vendor dependency, foundational delivery, and AI-assisted engineering evidence. |
| NEE_16 | Register source hash, date, participants, authority boundary, cold pointer, and unresolved Brian/Brien identity. | body+frontmatter | Exact input SHA-256 and user participant correction; no raw note is proposed for a successor package. | Enables deterministic provenance and identity-boundary lookup for every July 13 claim. |
| NEE_22 | Update Sherpa current-state posture to source-attributed offline/nightly capability with artifact, runtime-authority, and scorecard gaps still open. | body+frontmatter+route | July 13 meeting notes, current SOW scope, and existing Brien Muschett runtime/system-eval direction. | Improves current Sherpa architecture and evaluation retrieval without overstating implementation maturity. |
| NEE_99 | Clarify that direct natural-language approval after a passed Phase A Gate O1 surface is sufficient; the system binds exact hashes. | control_policy | Explicit user instruction in the current conversation; PD-53 records the approved prospective rule. | Future update workflows no longer require the user to copy formulaic digest wording while exact machine binding and fail-closed ambiguity controls remain. |
