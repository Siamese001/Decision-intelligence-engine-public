# Tier 2 — GraphDB Semantic Concept Deduplication & Signal Sharpening

> **Scope**: Deep concept consolidation across nodes, edges (incl. assertion types, confidence levels, edge evidence, edge lineage) to deduplicate similar concepts and sharpen retrieval signal in the NEE knowledge graph.
>
> **Branch**: `feat/tier2-graphdb-concept-dedup`
>
> **Prerequisite**: Tier 1 structural hygiene (orphan pruning, stale candidate cleanup, integrity/FK validation) is PASS — see `metrics/graph_health_table_v54.md`.

---

## 1. Objective

Tier 1 ensured **structural correctness** — zero FK violations, zero orphan nodes, zero duplicate edges, 100% provenance coverage. Tier 2 goes deeper: it targets **semantic redundancy** that structural scans cannot detect. Two graph nodes may be structurally independent (no FK link, no shared IDs) yet represent the same real-world concept, diluting retrieval precision and inflating the assertion surface.

### What Tier 2 Solves

| Problem Class | Example | Impact |
| :--- | :--- | :--- |
| **Synonymous nodes** | `"Cloud Migration"` vs `"GCP Cloud Migration"` vs `"Migration to Cloud"` | Splits assertions across 3 entities; retrieval misses 2/3 of evidence |
| **Alias-drifted entities** | `"Amit Ayer"` + `"Amit"` stored as separate nodes when one should be an alias | Duplicate relationship edges; inconsistent confidence scoring |
| **Predicate synonyms** | `leads` vs `is_lead_of` vs `owns` when semantically identical | One-off predicate rate inflation; fragmented query routing |
| **Overlapping assertions** | Two canonical assertions with same subject+predicate but ~identical object literals at different confidence levels | Conflicting signals in retrieval; confidence ambiguity |
| **Evidence fragmentation** | Same source passage cited as 2+ evidence items with slightly different `locator_value` or `direct_quote` | Over-counted corroboration; inflated `source_authority_rank` signal |
| **Edge lineage gaps** | Legacy `edge` table relationships without corresponding `assertion` migration, or assertions without traceable `extraction_run` lineage | Broken provenance chains; unreliable confidence auditing |

---

## 2. GraphDB Schema Context (V54)

The agent executing this prompt must ground all operations in the **live SQLite graph database**. Key tables and their dedup-relevant columns:

### 2.1 Nodes (`node` table)

```
node_id TEXT PK, node_type TEXT, canonical_name TEXT, display_name TEXT,
description TEXT, status TEXT, active INTEGER, created_at TEXT, updated_at TEXT
```

- **Identity rule**: `node_type_registry.identity_rule` governs merge policy per type. All active creatable types carry the `"never auto-merge on name similarity"` rule — meaning merges require explicit HITL approval.
- **Aliases**: `alias` table (legacy) and `canonical_alias` table (governed) link alternate names to `node_id`. Check `ambiguity_state` and `preferred_flag`.
- **Node state**: `node_state` table tracks `lifecycle_state`, `resolution_state`, `intentional_orphan`, etc.
- **Entity resolution**: `entity_resolution_candidate` + `entity_resolution_candidate_member` tables store pending and resolved merge candidates.
- **Merge events**: `entity_merge_event` + `entity_redirect` tables record completed merges with `event_type IN ('merge','split','reverse_merge')`.

### 2.2 Assertions (`assertion` table)

```
assertion_id TEXT PK, assertion_fingerprint TEXT UNIQUE (SHA-256),
subject_node_id TEXT FK→node, predicate TEXT, object_node_id TEXT FK→node,
object_literal TEXT, assertion_type TEXT, confidence_label TEXT,
confidence_score REAL [0.0–1.0], validation_state TEXT,
currentness_state TEXT, canonical_status TEXT, canonical_eligible INT,
retrieval_eligible INT, extraction_run_id TEXT FK→extraction_run,
primary_source_id TEXT FK→source, superseded_by_assertion_id TEXT FK→assertion,
approval_reference TEXT FK→governance_approval
```

- **Assertion types** (`assertion_type`): Classify the kind of knowledge claim.
- **Confidence levels** (`confidence_label` + `confidence_score`): Dual representation — categorical label + numeric 0.0–1.0 score.
- **Predicate registry** (`assertion_predicate_registry`): Governs allowed predicates with `object_kind`, `cardinality`, `conflict_policy`, `symmetric_mode`.
- **Assertion conflicts** (`assertion_conflict`): Explicit contradiction pairs with `conflict_type IN ('explicit_contradiction','competing_exclusive_value','human_declared_conflict')`.
- **Supersession chain**: `superseded_by_assertion_id` and `superseded_at` track temporal replacement.

### 2.3 Edges (Legacy `edge` table)

```
edge_id TEXT PK, from_node_id TEXT FK→node, edge_type TEXT,
to_node_id TEXT FK→node, description TEXT, evidence_posture TEXT,
confidence TEXT, materiality TEXT, source_id TEXT FK→source,
superseded_by TEXT FK→edge
```

- **Edge type registry** (`edge_type_registry`): Defines `canonical_predicate`, `relationship_family`, `direction`, `inverse_predicate`, `cardinality`, `evidence_required`.
- **Domain/range constraints** (`edge_type_domain_range`): Restricts which `source_node_type` → `target_node_type` pairs are valid per `edge_type_id`.
- **Migration bridge**: `legacy_edge_assertion_map` links old edges to new assertions. `legacy_claim_assertion_map` does the same for claims.

### 2.4 Evidence (`evidence_item` table)

```
evidence_id TEXT PK, evidence_fingerprint TEXT UNIQUE (SHA-256),
source_id TEXT FK→source, source_version_id TEXT FK→source_version,
locator_kind TEXT, locator_value TEXT, page_number TEXT,
section_name TEXT, paragraph_number TEXT, transcript_timestamp TEXT,
direct_quote TEXT, evidence_directness TEXT,
source_authority_rank INT [1–9], extraction_confidence REAL [0.0–1.0],
corroboration_group TEXT
```

- **Assertion↔Evidence bridge** (`assertion_evidence`): Links `assertion_id` to `evidence_id` with `evidence_relationship IN ('supports','contradicts')` and `independent_corroboration` flag.

### 2.5 Search & Retrieval Sidecars

```
search_document: search_document_id, object_type IN ('node','claim','assertion'),
                 object_id, title, body, canonical_status, retrieval_eligible
search_fts:      FTS5 virtual table over search_document
dense_vector:    Sidecar SSOT_REF_SSOT_REF_BGE-M3_Retrieval_Requirements_Retrieval_Requirements embeddings (768-dim)
sparse_weight:   Sidecar BM25/lexical weights
colbert_token:   Sidecar ColBERT token-level embeddings
retrieval_record: Query→document retrieval audit trail
```

---

## 3. Execution Protocol — Wave-Based Implementation Plan

> [!IMPORTANT]
> All waves are receipt-gated. Do not advance to Wave N+1 until Wave N is verified COMPLETE with a durable runtime receipt.

### Wave 1 — Discovery Scan & Concept Clustering

**Objective**: Produce a read-only inventory of all semantic dedup candidates across the six problem classes without mutating anything.

**Scope**:
1. **Node-level concept clustering**: Extend `GraphScout.scan_duplicate_entities()` beyond string similarity (current: 0.55×SequenceMatcher + 0.45×Jaccard) to include:
   - **Cross-type semantic similarity** (e.g., a `workstream` node and a `project` node describing the same initiative)
   - **Alias collision detection**: Scan `canonical_alias` for `ambiguity_state = 'ambiguous'` entries and cross-reference with `node` table
   - **Description-based clustering**: Group nodes where `description` text overlap exceeds 0.80 even if `canonical_name` similarity is below threshold
   - **Display name vs canonical name drift**: Flag nodes where `display_name` has diverged significantly from `canonical_name`

2. **Predicate synonym detection**: Query `assertion_predicate_registry` and `edge_type_registry` for:
   - Predicates with identical `relationship_family` but different `canonical_predicate` strings
   - Active predicates with `cardinality` and `object_kind` overlap suggesting semantic equivalence
   - One-off predicates used by ≤2 assertions that could map to an existing canonical predicate

3. **Assertion overlap detection**: Identify assertion pairs where:
   - Same `subject_node_id` + identical/near-identical `predicate`
   - `object_literal` text similarity ≥ 0.85 (after normalization)
   - Both are `canonical_status = 'canonical'` and `currentness_state = 'current'`
   - Different `confidence_score` values (signals competing assessments of the same fact)
   - Neither has `superseded_by_assertion_id` set (both still active)

4. **Evidence dedup scan**: Identify `evidence_item` pairs where:
   - Same `source_id` and `source_version_id`
   - `direct_quote` text similarity ≥ 0.90 or identical `locator_value`
   - Different `evidence_id` (structural duplicates from separate extraction runs)
   - Cross-reference `assertion_evidence` to find assertions linked to both evidence items

5. **Edge lineage gap audit**: Identify:
   - `edge` table rows where `superseded_by IS NULL` and no corresponding entry in `legacy_edge_assertion_map`
   - Assertions where `extraction_run_id IS NULL` or references a non-existent `extraction_run`
   - Assertion→evidence chains where `evidence_item.source_id` doesn't match `assertion.primary_source_id`

**Deliverables**:
- `output/graph_scout/tier2_discovery_report.json` — Full machine-readable scan results
- `output/graph_scout/tier2_concept_clusters.md` — Human-readable HITL review document with:
  - Cluster ID, member node IDs, similarity scores, merge/distinct confidence, margin delta
  - Predicate synonym groups
  - Overlapping assertion pairs
  - Duplicate evidence items
  - Lineage gap inventory

**Acceptance Criteria**:
- Zero-mutation verified (all queries via `PRAGMA query_only = ON`)
- All findings include SSOT_REF_HITL_Atomic_Presentation_Rules levels and 15% margin delta HITL gating
- Scan covers 100% of active nodes, canonical assertions, and non-deprecated predicates

**Receipt**: `python -m dual_pipeline.graph_scout --runtime-root <runtime-root> --tier 2 --discovery-only`

---

### Wave 2 — HITL Concept Resolution Surface

**Objective**: Present dedup candidates to the human operator one-at-a-time with structured merge/distinct/defer options per the HITL 15% ambiguity margin protocol.

**Scope**:
1. **Node merge decisions**: For each concept cluster from Wave 1:
   - Present the `SSOT_REF_HITL_Atomic_Presentation_Rules` interactive tool with options:
     - **(Recommended) Merge → [canonical_name_a]**: Confidence X% — retain node A as canonical, redirect node B, consolidate aliases, re-point assertions and edges
     - **Merge → [canonical_name_b]**: Confidence Y% — opposite merge direction
     - **Keep distinct**: Confidence Z% — mark pair as `resolved_distinct` in `entity_resolution_candidate`
     - **Defer**: Confidence: LOW (20%) — Not enough context to decide now
   - Only surface HITL when `margin_delta < 0.15` (ambiguity threshold). When `Δ ≥ 0.15`, auto-resolve under audit receipt.
   - Record every decision in `SSOT_REF_HITL_Atomic_Presentation_Rules.json` with `decision_id`, `timestamp`, `option_selected`, `confidence`, `rationale`

2. **Predicate consolidation decisions**: For each synonym group:
   - Present recommended canonical predicate with rationale
   - Option to remap or keep separate
   - If remapping: specify the migration path for all affected assertions

3. **Assertion dedup decisions**: For each overlapping pair:
   - Present side-by-side comparison: subject, predicate, object, confidence, source, evidence count
   - Options: Supersede older by newer / Supersede lower-confidence by higher / Keep both (mark as explicitly non-conflicting) / Defer

4. **Evidence consolidation decisions**: For each duplicate evidence cluster:
   - Present quote/locator comparison
   - Options: Merge to single evidence item (keep higher-authority instance) / Keep distinct (different locators are intentional)

**Deliverables**:
- Updated `SSOT_REF_HITL_Atomic_Presentation_Rules.json` with all Tier 2 decisions
- `output/graph_scout/tier2_resolution_plan.json` — Machine-executable merge/remap/supersession plan

**Acceptance Criteria**:
- Every concept cluster has a terminal decision (merge/distinct/defer)
- All auto-resolved decisions (Δ ≥ 0.15) have audit receipts
- Zero decisions presented in batch — strictly atomic, one-at-a-time

**Receipt**: Count of decisions made, auto-resolved, deferred

---

### Wave 3 — Graph Mutation Execution

**Objective**: Execute the approved resolution plan as a governed graph mutation through the Universal Write Gateway.

**Scope**:
1. **Pre-mutation snapshot**: Back up graph via `backup_run` table, record `source_database_sha256`

2. **Node merges** (for each approved merge):
   - Create `entity_merge_event` with `event_type = 'merge'`, `status = 'approved'`
   - Create `entity_redirect` from retired node to canonical survivor
   - Re-point all `assertion.subject_node_id` and `assertion.object_node_id` references
   - Re-point all `edge.from_node_id` and `edge.to_node_id` references
   - Consolidate `canonical_alias` entries (transfer aliases from retired to survivor)
   - Consolidate `node_card_map` entries
   - Set retired node `active = 0`, update `node_state.lifecycle_state`
   - Update `search_document` entries: merge body text, recalculate retrieval eligibility
   - Regenerate dense/sparse/ColBERT vectors for affected `search_document` rows

3. **Predicate remapping** (for each approved consolidation):
   - Update `assertion.predicate` for affected assertions
   - Set deprecated predicates to `registry_state = 'deprecated'` with `replacement_predicate` pointer in `assertion_predicate_registry`
   - Similarly deprecate in `edge_type_registry` with `replacement_predicate`

4. **Assertion supersession** (for each approved dedup):
   - Set `superseded_by_assertion_id` on the retired assertion
   - Set `superseded_at` timestamp
   - Update `currentness_state = 'superseded'`, `canonical_status` accordingly
   - Transfer `assertion_evidence` linkages to the surviving assertion (if not already linked)
   - If both had `assertion_conflict` entries, resolve/consolidate

5. **Evidence dedup** (for each approved merge):
   - Re-point `assertion_evidence.evidence_id` from retired to surviving evidence item
   - Set retired evidence item to inactive (or delete if zero remaining references)
   - Update `corroboration_group` for surviving item

6. **Audit trail**: Every mutation produces an `audit_event` with:
   - `actor_or_process = 'tier2_concept_dedup'`
   - `action` = specific operation type
   - `previous_value_json` / `new_value_json` capturing exact state transition
   - `migration_or_ingestion_run_id` linking to the dedup run

**Deliverables**:
- Mutated graph database
- `audit_event` entries for every change
- `entity_merge_event` + `entity_redirect` records for all merges

**Acceptance Criteria**:
- All mutations go through `write_control` transaction gating
- Post-mutation graph passes all Tier 1 health metrics (zero FK violations, zero orphans, etc.)
- `graph_sha256` transition proves positive operations changed the graph hash

**Receipt**: `python tools/graph_health_check.py --graph <path>` — all 15 metrics PASS

---

### Wave 4 — Retrieval Index Rebuild & Release Qualification

**Objective**: Rebuild search/retrieval indexes for affected entities and qualify the consolidated graph for release.

**Scope**:
1. **Search document regeneration**: For every node/assertion touched:
   - Rebuild `search_document` entries with consolidated body text
   - Sync `search_fts` virtual table
   - Regenerate `dense_vector`, `sparse_weight`, `colbert_token` sidecar vectors using real SSOT_REF_SSOT_REF_BGE-M3_Retrieval_Requirements_Retrieval_Requirements

2. **Release preparation**: `python nee.py release-prepare --directive-id <tier2-directive>`

3. **Retrieval release evaluation**: Pass all 11 SSOT_REF_BGE-M3_Retrieval_Requirements gates under real SSOT_REF_SSOT_REF_BGE-M3_Retrieval_Requirements_Retrieval_Requirements:
   - E3 gate: `python nee.py retrieval-release-evaluate ...`
   - Steady-state latency < SSOT_REF_BGE-M3_Retrieval_Requirements
   - Query encoding caching and vectorized scoring enforced
   - Evaluation deltas scoped to Tier 2 operations only

4. **Release activation**: `python nee.py release-activate --release-id <id>`

5. **Completion gate**: `python nee.py intake-completion-gate --directive-id <tier2-directive>`

**Deliverables**:
- Sealed retrieval evaluation receipt under `<runtime_root>/retrieval_evaluations/`
- Updated `active_pair.json`
- Release receipt with `graph_sha256` transition

**Acceptance Criteria**:
- All 11 retrieval gates PASS
- W0 and E0 release lifecycle audits PASS
- Activation receipt matches release and current `active_pair.json`
- No SSOT_REF_BGE-M3_Retrieval_Requirementss — all vectors from real SSOT_REF_SSOT_REF_BGE-M3_Retrieval_Requirements_Retrieval_Requirements execution

**Receipt**: `python nee.py intake-completion-gate --directive-id <tier2-directive>`

---

### Wave 5 — Signal Quality Validation & Regression

**Objective**: Confirm that the consolidated graph produces sharper, non-regressed retrieval signal.

**Scope**:
1. **Before/after retrieval comparison** on a benchmark query set:
   - Precision@5 should improve (fewer duplicate results from duplicate entities)
   - Recall should not decrease (merged entities preserve all evidence)
   - Citation accuracy ≥ 98%

2. **Graph health metrics** — regenerate `metrics/graph_health_table_v55.md`:
   - All 15 metrics PASS
   - Duplicate-edge rate: 0.0000%
   - One-off predicate rate should decrease (predicate consolidation)
   - Provenance coverage: ≥ 98%

3. **Granular Graph DB Delta Table** (mandatory per AGENTS.md):
   | SQLite Table | Baseline Count | Activated Count | Delta | What Changed |
   | :--- | :--- | :--- | :--- | :--- |

4. **Itemized Assertions Table** for all affected assertions

5. **Idempotency proof**: Re-running the Tier 2 scan should produce zero new findings for resolved clusters

**Deliverables**:
- `metrics/graph_health_table_v55.md`
- `output/graph_scout/tier2_completion_report.md` with delta table and idempotency proof
- Updated `walkthrough.md` with Wave Implementation Status Table

**Acceptance Criteria**:
- Precision@5 ≥ baseline (no regression)
- All 15 graph health metrics PASS
- Idempotency scan returns zero new findings for resolved clusters
- Completion report includes Granular Graph DB Delta Table and Itemized Assertions Table

**Receipt**: Runtime receipt with `graph_sha256` before/after, retrieval evaluation digest, and wave completion timestamp

---

## 4. Architectural Constraints

> [!CAUTION]
> All constraints from `AGENTS.md` apply. Key ones for this work:

1. **Zero-mutation scans**: Waves 1–2 are strictly read-only (`PRAGMA query_only = ON`). No graph writes until Wave 3.
2. **HITL SSOT_REF_HITL_Atomic_Presentation_Rules**: One decision at a time. Use `SSOT_REF_HITL_Atomic_Presentation_Rules` tool. 15% margin delta auto-resolve threshold.
3. **Provider-free boundary**: Node merges, predicate remaps, assertion supersession, evidence dedup, and all graph writes are provider-free. No LLM calls for structural mutations.
4. **Real SSOT_REF_SSOT_REF_BGE-M3_Retrieval_Requirements_Retrieval_Requirements only**: No synthetic or mock embeddings for retrieval evaluation (Wave 4).
5. **400-line module budget**: Any new Python modules must stay under 400 lines. Use `*_core/` package decomposition.
6. **learning-change-proposal:1.0.0**: All proposed changes emit contract-valid proposals with `approval_required: true`, `auto_apply: false`.
7. **Release lifecycle**: All graph mutations follow the full release cycle (prepare → evaluate → activate → completion gate).
8. **Idempotent operations**: Running the same merge/remap twice must be a no-op (check `entity_redirect.active`, `assertion.superseded_by_assertion_id`, etc.).

---

## 5. Wave Implementation & Completion Status Table

> [!WARNING]
> **Forensic Audit & Status Correction (2026-10-03)**:
> An empirical audit of the active graph pair (`generation-48db47ec501444edaf418880`, `graph_sha256`: `752df7557bb81d4886a65e072f7b736e724671818c7b5b246865377b7ab5fadf`) proved that Tier 2 mutations were executed **only against an isolated worktree scratch copy** (`reports/graph_scout/nee_graph_tier2_work.sqlite`).
> Zero Tier 2 audit events and zero merged nodes exist in the active graph; all 10 target entities and 40 exact-triple assertion duplicate groups remain active in the active graph.
> See sealed baseline receipt: [`reports/graph_consolidation/wave1_baseline_receipt.json`](file:///Users/amitayer/Git/decision-intelligence-engine/reports/graph_consolidation/wave1_baseline_receipt.json) (Digest: `74c123785bd5dbeac9a105cc75962d22ccde40c8603ef101453ae0135793f35d`).
> Statuses for Waves 3–5 are downgraded accordingly below.

| Wave Number | Description / Scope | Completion Status | Detailed Scope & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Discovery Scan & Concept Clustering (6 parallel scans, 2,556+ findings) | ✅ COMPLETE | Read-only scan findings across working copy | `tools/tier2_scan[0-5]_*.py` → `output/graph_scout/tier2_discovery_report.md` |
| Wave 2 | HITL Concept Resolution Surface (36 autonomous decisions under Δ ≥ 0.10) | ⚠️ SCRATCH-ONLY | Autonomous decisions applied to scratch copy only; not in active pair | `tools/tier2_execute_dedup.py` (Phase 1-2 auto-resolution receipts) |
| Wave 3 | Graph Mutation Execution (5 node merges, 31 supersessions, 1 edge lineage fix) | ⚠️ SCRATCH-ONLY | Applied to scratch DB only (`nee_graph_tier2_work.sqlite`); 0 merged nodes in active graph | `output/graph_scout/tier2_execution_receipt_tier2-dedup-54bec7cdf86361dd42bebfab.json` |
| Wave 3b | Domain/Range Migration & Predicate Consolidation (6/6 predicates remapped) | ⚠️ SCRATCH-ONLY | Applied to scratch DB only; 0 deprecated predicates active in active graph | `tools/tier2_finish_waves.py` (scratch copy) |
| Wave 4 | Search Document & FTS Rebuild (51 docs synced, 29 duplicate edges cleaned) | ⚠️ SCRATCH-ONLY | Rebuilt scratch FTS only; active sidecars unaffected | `output/graph_scout/tier2_waves_3b_4_5_receipt.json` |
| Wave 5 | Signal Quality Validation & Regression (14 checks, 100% provenance, 0 bad edges) | ⚠️ SCRATCH-ONLY | Validation passed on scratch DB only; active pair retained 40 triple duplicate groups | Proved by [`wave1_baseline_receipt.json`](file:///Users/amitayer/Git/decision-intelligence-engine/reports/graph_consolidation/wave1_baseline_receipt.json) |

