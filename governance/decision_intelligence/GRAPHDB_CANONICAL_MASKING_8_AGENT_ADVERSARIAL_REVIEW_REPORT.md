# Dialectical 8-Agent Adversarial Review: Stale Canonical Masking & GraphDB Reconciliation

**Incident Target**: Stale Canonical Masking, Candidate Isolation, and Entity Disconnection in NEE Knowledge Graph  
**Target Graph Generation**: `generation-6018bfb52eee82779373da629ce6`  
**Execution Timestamp**: 09/25/2026 22:50:00 UTC  
**Evaluation Standard**: Unidirectional Layer Hierarchy (L0–L6), 11-Gate BGE-M3 Retrieval Assurance, and Universal Write Gateway (UWG) Promotion Governance  

---

## Executive Summary of the Architectural Defect

When an operator requests the ingestion of a live Single Source of Truth (SSOT)—such as the live onboarding portal `https://nea-onboarding.static.PartnerAdvisory.com/`—the current pipeline exhibits a critical architectural failure:

1. **Candidate Jailing**: `intake-publish` admits all incoming entities, evidence, and assertions strictly into the `candidate` partition (`canonical_status = 'candidate'`, `retrieval_eligible = 0`).
2. **Stale Canonical Persistence**: Pre-existing assertions extracted months earlier (e.g., June 15, 2026 EDO program status) remain indefinitely marked as `canonical_status = 'canonical'` and `currentness_state = 'current'` (`retrieval_eligible = 1`).
3. **Silent Decision Service Fallback**: When users query current state via `ask --query-type current_state`, the engine restricts authoritative retrieval strictly to the `approved` lane. It silently returns the 3-month-old obsolete facts (such as Phani Teja Chigurupati leading Asset Data Product) while completely omitting the live September 2026 roster (Andrew Hanson, James Beebe), burying a subtle note: `"missing_information": ["Candidate material cannot establish internal fact."]`.
4. **Disconnected Entity Topologies**: The intake extracted `Charter Asset Data Product` (`entity-502bfedacc5aa30ed6fa27cbc577`) as a new isolated node rather than aliasing to the pre-existing `workstream_asset_data_product`, preventing topological collision detection and automated supersession.

Below is the exhaustive, 8-agent adversarial critique and implementation specification to eliminate this design flaw repository-wide, followed by the concrete GraphDB remediation plan.

---

## Agent 1: Epistemic Authority & Canonical Masking Inquisitor

*Role Key:* `canonical_masking_inquisitor`

### 1. Vulnerability Assessment
The architecture enforces a strict two-tier epistemic wall: facts are either *unverified candidates* or *unassailable canon*. However, it lacks an authority-driven supersession mechanism:
- An unauthenticated or low-authority historical document that achieved `canonical` status in June 2026 permanently blocks fresher, higher-authority candidate data ingested in September 2026.
- The pipeline reports `terminal_outcome: COMPLETE` and `status: GRAPH_MUTATED`, misleading operators into believing the active graph now reflects the new SSOT. In reality, only the quarantined `candidate` partition mutated; the authoritative `approved` retrieval lane remained 100% frozen in June.

### 2. Mandatory Acceptance Invariant
Let $S_{\text{new}}$ be an incoming source with authority rank $A(S_{\text{new}})$ and timestamp $T_{\text{new}}$, and $S_{\text{old}}$ be an existing canonical source with $A(S_{\text{old}})$ and $T_{\text{old}}$.
If an ingestion directive is declared with `--authoritative-ssot` or `--promote-canonical` and $A(S_{\text{new}}) \ge A(S_{\text{old}})$ and $T_{\text{new}} > T_{\text{old}}$:
$$\operatorname{IngestAction}(S_{\text{new}}) \implies \operatorname{PromoteCanonical}(S_{\text{new}}) \land \operatorname{SupersedeContradictions}(S_{\text{old}})$$
The system MUST NOT leave incoming SSOT assertions in `candidate` status if the directive authorized graph mutation.

---

## Agent 2: Temporal Precedence & Chronological Decay Arbiter

*Role Key:* `temporal_decay_arbiter`

### 1. Vulnerability Assessment
The database stores `source_date` (`source`), `source_date_normalized` (`source_version`), and `recorded_at` (`assertion`), but the ingestion and retrieval engines treat time as an inert string rather than a dynamic temporal precedence operator:
- `source_edo_status_2026_06_15` (recorded June 15, 2026) has `currentness_state = 'current'`.
- `source-dc6f7f6c3bdbd53f9d4f1154` (recorded September 25, 2026) also has `currentness_state = 'current'`.
- Because no automated chronological decay occurs, two contradictory assertions for the same domain simultaneously claim `currentness_state = 'current'`, with the older one winning retrieval solely due to legacy `canonical` status.

### 2. Mandatory Acceptance Invariant
Any assertion $a_{\text{old}}$ possessing predicate $P \in \text{MutexPredicates}$ on subject $N$ MUST undergo automated state transition upon activation of $a_{\text{new}}$:
$$\text{If } T(a_{\text{new}}) > T(a_{\text{old}}) \land \operatorname{DomainConflict}(a_{\text{new}}, a_{\text{old}}):$$
$$a_{\text{old}}.\text{currentness\_state} \leftarrow \text{'historical'}$$
$$a_{\text{old}}.\text{canonical\_status} \leftarrow \text{'superseded'}$$
$$a_{\text{old}}.\text{superseded\_at} \leftarrow T(a_{\text{new}})$$
$$a_{\text{old}}.\text{superseded\_by\_assertion\_id} \leftarrow a_{\text{new}}.\text{assertion\_id}$$
$$a_{\text{old}}.\text{retrieval\_eligible} \leftarrow 0$$

---

## Agent 3: Deterministic Identity Resolution & Entity Aliasing Sentry

*Role Key:* `identity_resolution_sentry`

### 1. Vulnerability Assessment
Stage U0 identity resolution failed to reconcile `Charter Asset Data Product` with `workstream_asset_data_product`:
- The package generator prefixed the entity ID as `workstream:charter_asset_data_product`.
- The identity resolver looked for exact token equivalence against `workstream_asset_data_product`, failed to find an exact match, and created a new isolated surrogate ID `entity-502bfedacc5aa30ed6fa27cbc577`.
- **Consequence**: The graph developed a split-brain condition. The old node held the old canonical edges; the new node held the new candidate edges. Because their node IDs differed, no collision detector or mutex constraint was triggered.

### 2. Mandatory Acceptance Invariant
Identity resolution in `dual_pipeline/intake_core/identity_resolution.py` MUST apply normalized fuzzy token equivalence:
- Strip generic administrative prefixes: `charter_`, `workstream_`, `project_`, `pod_`, `sow_`.
- Compute normalized slug: `clean_slug("Charter Asset Data Product") == clean_slug("Asset Data Product") == "asset_data_product"`.
- If an exact slug match exists on a canonical entity of type `workstream`, Stage U0 MUST either:
  1. Alias the incoming entity to the existing node (`ALIASED_TO` edge), OR
  2. Map the subject directly to the existing node ID (`workstream_asset_data_product`), preserving node history.

---

## Agent 4: Decision Service & Retrieval Lane Isolation Auditor

*Role Key:* `query_fallback_auditor`

### 1. Vulnerability Assessment
The decision service (`ask`) silently masks candidates:
- When queried with `--query-type current_state`, the engine queries `approved` lane documents (`retrieval_eligible = 1` and `canonical_status = 'canonical'`).
- Candidate documents have `retrieval_eligible = 0`, meaning BGE-M3 dense and ColBERT token scoring ignores them during approved lane retrieval.
- When an approved hit is found, the engine outputs the statement as ground truth. The fact that high-relevance, fresh candidate documents exist that directly contradict the approved hit is suppressed into a generic `missing_information` bullet.

### 2. Mandatory Acceptance Invariant
`ask` and `DecisionService` MUST implement an active **Contradiction Detection Hook**:
- During retrieval, execute a dual-lane probe (`approved` + `candidate`).
- If a candidate document achieves higher semantic similarity ($\Delta_{\text{score}} > 0.05$) or higher chronological precedence ($T_{\text{cand}} > T_{\text{appr}}$) on the queried subject, the engine MUST emit an explicit epistemic conflict flag:
  `"epistemic_warning": "CONTRADICTORY_CANDIDATE_DETECTED: Candidate assertion (2026-09-25) contradicts approved canonical assertion (2026-06-15). Promotion or reconciliation required."`
- The engine MUST NOT deliver outdated canonical assertions as uncontested current state when contradictory candidates exist.

---

## Agent 5: Universal Write Gateway (UWG) Promotion Gatekeeper

*Role Key:* `uwg_promotion_gatekeeper`

### 1. Vulnerability Assessment
Currently, promoting candidate facts to canonical requires an obscure, multi-step out-of-band workflow: `uwg-evaluate` $\rightarrow$ `uwg-promote` $\rightarrow$ `uwg-activate`. 
- Ordinary operators using `intake-publish` or `ingest-meeting` have no single-step flag to promote an authoritative intake.
- This creates an operational chasm where data is repeatedly ingested into candidate purgatory, never graduating to the active decision layer.

### 2. Mandatory Acceptance Invariant
Extend `dual_pipeline.cli intake-publish` and `intake-directive-open`:
- Add flag `--promote-canonical` / `--authoritative-ssot`.
- When passed, Stage X3 release runner automatically invokes the Canonical Promotion Gateway (`CanonicalPromotionGateway.promote()`), elevating validated candidate assertions to `canonical_status = 'canonical'` and `retrieval_eligible = 1` within the same atomic release receipt.

---

## Agent 6: Mutual Exclusivity (Mutex) & Semantic Spine Enforcer

*Role Key:* `mutex_enforcer`

### 1. Vulnerability Assessment
The knowledge graph schema lacks cardinality constraints on operational predicates:
- `OWNS_OR_LEADS`: A delivery pod can have only one primary Tech Lead / Engineering Lead at a time.
- `DELIVERS`: A delivery pod has a specific vendor/internal delivery model.
- `GOVERNS`: A project has a defined Executive Sponsor.
Because the schema permits unbounded parallel edges without mutex validation, the graph happily accumulates `Phani Teja Chigurupati CONTRIBUTES_TO Asset Data Product` (June) and `Andrew Hanson OWNS_OR_LEADS Asset Data Product` (September) as non-conflicting independent facts, avoiding automated reconciliation.

### 2. Mandatory Acceptance Invariant
Define strict Mutex Predicate Cardinality rules in `governance/decision_intelligence/W2_SEMANTIC_SPINE_CONTRACT.json`:
- For mutex predicates (`OWNS_OR_LEADS`, `DELIVERS`, `GOVERNS`):
  $$\forall N \in \text{Nodes}: |\{ a \in \text{Assertions} \mid a.\text{subject} = N \land a.\text{predicate} = P \land a.\text{currentness} = \text{'current'} \}| \le 1$$
- Ingestion of a second current edge on a mutex predicate MUST either reject with `MUTEX_CARDINALITY_VIOLATION` or automatically supersede the prior edge.

---

## Agent 7: Adversarial Red Team & Boundary Attack Lead

*Role Key:* `red_team_specialist`

### 1. Vulnerability Assessment & Exploit Vectors
The red team identified 4 critical exploits enabled by this flaw:
1. **Ghost Staffing Exploitation**: A contractor or employee terminated in August continues to be reported by the Executive Decision Copilot as the active delivery lead because their June canonical assertion is never superseded by September onboarding captures.
2. **Candidate Starvation Denial-of-Service**: A malicious or uncalibrated actor floods candidate ingestion with thousands of updates. The system reports 100% test pass rates and successful mutations, but the executive API serves completely static, outdated intelligence.
3. **Split-Brain Subgraph Partitioning**: Generating variant entity names (`Charter X`, `Project X`, `Workstream X`) creates parallel subgraphs that fragment topological reasoning and PageRank metrics.
4. **Deceptive CI Gate Validation**: All 29 PR governance gates passed cleanly because the gate checks whether the package matches the schema and whether BGE-M3 can retrieve *something*—it never tests whether candidate data actually overtook obsolete canonical facts.

### 2. Mandatory Acceptance Invariant
Add Gate 30 to PR Governance: **Canonical Supersession & Recency Verification Gate**:
- When ingesting a source labeled SSOT, verify that at least one canonical fact was updated or that candidate facts do not have older contradictory canonical facts in active retrieval.

---

## Agent 8: GraphDB Remediation & State Transition Commander

*Role Key:* `remediation_commander`

### 1. The Immediate Remediation State Machine
To immediately repair the active graph generation (`generation-6018bfb52eee82779373da629ce6`) and restore SSOT fidelity:

```
[State 0: Active Gen 6018bfb52eee82779373da629ce6]
   │
   ├─► Step 1: Reconcile Pod Entities with Existing Workstreams
   │     entity-502bfedacc5aa30ed6fa27cbc577 <-> workstream_asset_data_product
   │     entity-d24def7af34673e6039952e05adf <-> workstream_atlas
   │     entity-519e399503f10985ed8de5aa86e1 <-> workstream_project_spring
   │     entity-044b6d5c38c161783349e0d5d6cd <-> workstream_trade_floor
   │     entity-c29a9a577a59ef6f50be53adcdf7 (New: EDO PMO)
   │
   ├─► Step 2: Promote 19 Live Candidate Assertions to Canonical
   │     canonical_status <- 'canonical'
   │     retrieval_eligible <- 1
   │
   ├─► Step 3: Supersede Conflicting June 15, 2026 Assertions
   │     asrt_edge_0dd2aa0ee2b920f97d403989 (Phani Teja on Asset Data Product):
   │       currentness_state <- 'historical'
   │       canonical_status <- 'superseded'
   │       retrieval_eligible <- 0
   │       superseded_at <- '2026-09-25T21:20:31Z'
   │     asrt_edge_59469d50508b80a76252d054 (EDO portfolio dated status):
   │       currentness_state <- 'historical'
   │       retrieval_eligible <- 0
   │
   ├─► Step 4: Re-index Search Documents & Full-Text Search
   │     Synchronize search_document and search_fts with newly promoted canonical assertions
   │
   ├─► Step 5: Regenerate Sidecar Vector Indexing
   │     Update dense_vector, sparse_weight, colbert_token, retrieval_record
   │
   └─► Step 6: Activate Generation & Verify with Live `ask` Query
         Prove Andrew Hanson / James Beebe / Lan Tran appear in 'approved' lane
```

---

## Synthesis & Consensus Authorization

All 8 agents unanimously concur:
1. **The incident is a systemic architectural defect** (Stale Canonical Masking), not a transient data error.
2. **Immediate GraphDB mutation is authorized** to promote the live September 25, 2026 onboarding SSOT into canonical status and supersede outdated June 15 records.
3. **Prevention controls (automated supersession, entity aliasing, and `--promote-canonical` flag)** must be codified to permanently eliminate this vulnerability.
