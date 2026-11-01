## 1. Graph Delta and Retrieval Impact

| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |
|---|---|---|---|---|---|
| Sources | 169 | 172 | 3 | Implemented exactly: 3 adds | Adds exact source-attribution boundaries for July 6 status, the official Google announcement, and the TRM draft artifact. |
| Source versions | 169 | 172 | 3 | Implemented exactly: 3 adds | Binds each uploaded source to immutable hashes, dates, scope, and authority posture. |
| Evidence items | 1592 | 1602 | 10 | Implemented exactly: 10 adds | Provides page/section/card locators for status, public-announcement, and TRM requirements assertions. |
| Nodes (total / active) | 651 / 646 | 652 / 647 | 1 / 1 | Implemented exactly: 1 add + 11 updates | Creates one exact-match TRM Lakehouse workstream and refreshes dated workstream status text used by node lookup. |
| Node states | 651 | 652 | 1 | Implemented exactly: 1 add + 11 updates | Keeps node search currentness and status-mirror text synchronized without changing ontology or RAG vocabulary. |
| Legacy aliases | 426 | 429 | 3 | Implemented exactly: 3 adds | Enables exact lookup of three source-grounded TRM Lakehouse name variants. |
| Canonical aliases (total / active) | 421 / 421 | 424 / 424 | 3 / 3 | Implemented exactly: 3 adds | Normalizes the TRM variants to one reviewed entity and blocks duplicate node creation. |
| Alias lineage maps | 426 | 429 | 3 | Implemented exactly: 3 adds | Preserves deterministic alias lineage from legacy lookup rows to canonical aliases. |
| Edges (total / active) | 1066 / 1038 | 1069 / 1041 | 3 / 3 | Implemented exactly: 3 adds | Adds approved-vocabulary traversal from TRM Lakehouse to Databricks, Unity Catalog, and the Endur boundary. |
| Claims (total / active) | 544 / 499 | 568 / 508 | 24 / 9 | Implemented exactly: 24 adds + 15 supersedes | Adds atomic source-bounded status and requirements statements while preserving June 29 claims as historical. |
| Canonical assertions | 1643 | 1670 | 27 | Implemented exactly: 27 adds + 2 updates + 15 supersedes | Promotes evidence-backed dated assertions, supersedes stale status candidates, and reconciles official Google evidence. |
| Assertion-evidence links | 1668 | 1697 | 29 | Implemented exactly: 29 adds | Prevents unsupported canonical retrieval by attaching every promoted assertion to exact source evidence. |
| Edge-assertion lineage maps | 1066 | 1069 | 3 | Implemented exactly: 3 adds | Maintains exact lineage from each new legacy edge to its canonical relationship assertion. |
| Claim-assertion lineage maps | 544 | 568 | 24 | Implemented exactly: 24 adds | Maintains exact lineage from each new claim to its canonical assertion. |
| Validations (total / open) | 176 / 143 | 182 / 147 | 6 / 4 | Implemented exactly: 6 adds + 4 updates | Resolves two cleared P0 gates and opens/narrows currentness, authority, staffing, and control questions with owners/dates. |
| Canonical routes (total / active) | 3812 / 3811 | 3890 / 3889 | 78 / 78 | Implemented exactly: 78 adds + 2 updates | Adds primary/support homes across the existing spine and upgrades Google routes from candidate to approved source-attributed. |
| Node-card maps | 1624 | 1628 | 4 | Implemented exactly: 4 adds | Routes the new TRM workstream to existing cards without adding or re-scoping the spine. |
| Search documents (total / eligible) | 2222 / 1304 | 2250 / 1334 | 28 / 30 | Implemented exactly: 28 adds + 28 updates | Indexes exact names, current dated statuses, source-attributed requirements, and non-inference boundaries while hiding superseded rows. |

## 2. Mandatory Graph Metrics

| Metric | Result | Pass threshold | Status | Comment / required improvement |
|---|---|---|---|---|
| Operational orphan rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤5% and no increase | PASS | Actual verification: No new operational orphan is introduced; the one new TRM node has approved-vocabulary edges and explicit card mappings. |
| Duplicate node count | 5 candidate groups → 5 candidate groups (+0; 0 new high-impact duplicates) | 0 new high-impact duplicates | PASS | Actual verification: The TRM workstream is a distinct instance; three reviewed aliases normalize to it without merging existing entities. |
| Duplicate edge rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤1% unexplained and no increase | PASS | Actual verification: The three new relationship assertions use distinct subject-predicate-object combinations and preserve edge/evidence lineage. |
| Rare edge-type rate | 0.0000% → 0.0000% (+0.0000 pp) | ≤25% or no increase | PASS | Actual verification: No ontology vocabulary is added; TRM relationships reuse USES_PATTERN, DEPENDS_ON, and RETAINS_DEPENDENCY_ON. |
| Claim completeness | 100.0000% → 100.0000% (+0.0000 pp) | 100% | PASS | Actual verification: All 24 proposed claims have subject, source, confidence, HITL posture, evidence lineage, and active canonical routing. |
| Source metadata completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥95%; 100% for material sources | PASS | Actual verification: All three material uploads have immutable hashes, source versions, dates/date posture, authority rank, scope, and allowed-use posture. |
| Temporal completeness | 100.0000% → 100.0000% (+0.0000 pp) | ≥90% and no decrease | PASS | Actual verification: July 6 status is dated exactly; the December 2025 announcement remains dated intent; the TRM snapshot date is explicitly estimated rather than execution-effective. |
| High-impact identity collisions | 0 → 0 (+0) | 0 unresolved | PASS | Actual verification: No promoted alias collision is created; the proposal avoids a bare Endur alias and preserves all existing identity candidates. |
| P0/P1 SLA breach rate | 0.0000% (0/108 undated) → 0.0000% (0/112 undated) (+0.0000 pp; +4 open items, all owned and dated) | 0% | PASS | Actual verification: Two cleared P0 gates resolve, four existing items are updated, and six focused validations are added; every remaining open P0/P1 has one owner and due date. |
| Decision-safe answer regression | 0 failures / 21 cases → 0 failures / 21 cases (+0 failures) | 0 regression on approved test set | PASS | Actual verification: All 21 behavioral cases pass; V51 hardcoded authority counts differ only by the exact G1 proposal deltas and are separately reconciled. |

## 3. Card Additions and Retrieval Impact

| Card | Addition | Change type | Defense / evidence | Retrieval improvement |
|---|---|---|---|---|
| NEE_02 | July 6 formal status overlay, 2-yellow/0-red rollup, 11 refreshed core rows, and Nuclear June 29 conflict note. | implemented body+frontmatter+route | July 6 formal internal status packet. | Current status lookup returns July 6 posture while preserving June 29 history. |
| NEE_03 | TRM draft RFP/SOW-like document-authority and nonexecution guardrail. | implemented body+frontmatter+route | TRM document title/body mismatch and absence of execution/commercial markers. | Contract/SOW queries abstain from treating the TRM draft as executed. |
| NEE_04 | July 6 Q3 entry controls; PD/DocIQ P0 closures and narrower P1 follow-ups. | implemented body+frontmatter+route | July 6 executive readout and tech-lead detail. | Q3 action lookup resolves cleared gates and surfaces named remaining controls. |
| NEE_07 | AI-usage interpretation boundary, DocIQ phase-two controls, TRM agent guardrails, and Google announcement caveat. | implemented body+frontmatter+route | Status usage indicators, DocIQ detail, TRM sections 4.10–4.11, and official Google technology section. | AI queries distinguish self-report, guardrails, and public intent from telemetry or deployment. |
| NEE_09 | Official dated Apex Global / Google Cloud announcement with currentness validation gate. | implemented body+frontmatter+route | Official December 8, 2025 Apex Global / Google Cloud release. | Public-strategy retrieval uses the primary source and blocks stale launch/completion inference. |
| NEE_10 | Route-only impact from 11 July 6 workstream node-status refreshes; no card-body or mapping change. | implemented route | Existing NEE_10 routes and the July 6 formal internal status packet. | Stakeholder/workstream navigation returns refreshed dated status text without upgrading delivery leads into client decision owners. |
| NEE_11 | Cross-pod reuse of access, certification, lineage, cost, review, and charter controls. | implemented body+frontmatter+route | Cross-source reuse synthesis with source-attribution boundaries. | Pattern retrieval connects similar controls without inventing implementation equivalence. |
| NEE_13 | TRM governed-data-foundation, data-product, Unity Catalog, operating-model, and interoperability requirements. | implemented body+frontmatter+route | TRM sections 3–4.12 and knowledge-transfer requirements. | TRM data-foundation queries route to certified-data-product and governance patterns. |
| NEE_14 | TRM Endur/ETRM source-of-record and future Risk Engine implementation boundaries. | implemented body+frontmatter+route | TRM sections 4.4, 4.12, and out-of-scope list. | Trading queries distinguish analytical foundation, ETRM authority, and future risk-engine interface. |
| NEE_15 | Route-only currentness impact for PD Conduit / Conductor name resolution; no card-body, alias, or mapping change. | implemented route | Existing NEE_15 name-resolution route and the July 6 PD Conduit evidence. | Alias lookup resolves the same PD Conduit entity while returning the July 6 dated posture rather than the superseded June 29 status. |
| NEE_16 | Hashes, source dates, authority posture, and allowed-use boundaries for all three uploads. | implemented body+frontmatter+route | Recorded SHA-256 values and source-authority reconciliation. | Provenance lookup exposes exact hashes, dates, authority, and use limits. |
