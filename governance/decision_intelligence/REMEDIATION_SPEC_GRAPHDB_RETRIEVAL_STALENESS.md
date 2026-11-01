# Architectural Remediation Specification: GraphDB & Retrieval Staleness Elimination

**Document ID:** `SPEC-RCA-2026-0925-STALENESS-01`  
**Governing Standard:** Unified Architecture (Skills, Agents, Sub-Agents, Hooks, Rules)  
**Branch:** `feat/rca-graphdb-retrieval-staleness`  
**Root Cause Reference:** [`RCA_GRAPHDB_VS_RETRIEVAL_STALENESS.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.worktrees/rca_graphdb_retrieval_staleness/governance/decision_intelligence/RCA_GRAPHDB_VS_RETRIEVAL_STALENESS.md)  
**Judicial Allocation:** 65% GraphDB Content & Ingestion Layer / 35% Retrieval & Copilot Serving Layer  

---

## 1. Executive Remediation Architecture

The 77.5% staleness defect demonstrated a fundamental breakdown across the evidence-to-answer lifecycle: an interim morning blocker was captured, but subsequent afternoon screenshot updates removing the row were never ingested, prior assertions were never tombstoned or superseded, and Copilot's retrieval engine presented the unretracted historical blocker as active current reality.

```ascii
┌─────────────────────────────────────────────────────────────────────────────┐
│                       END-TO-END REMEDIATION LIFECYCLE                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. STAGE U0 CHAT INGRESS BRIDGE                                             │
│    • Ingest chat screenshots, pasted tables, and slide revisions            │
│    • OCR table structures & emit structured evidence envelope               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. MONOTONIC ARTIFACT VERSIONING & ASSERTION TOMBSTONING                    │
│    • Track artifact_id (LENS_Waterfall_Table) across revisions (v1 -> v2)   │
│    • Detect deleted/modified rows and transition assertions:                │
│      currentness_state = 'superseded', superseded_at = <timestamp>          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. RETRIEVAL LIFECYCLE FILTERING & INTRA-DAY RECENCY DECAY                 │
│    • Enforce SQL filter: WHERE currentness_state = 'current' for active     │
│    • Apply intra-day sub-second timestamp decay across same-day sessions    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. COPILOT EPISTEMIC QUALIFICATION ENGINE                                   │
│    • Detect candidate/draft dependencies in prompt generation               │
│    • Qualify uncorroborated historical blockers with epistemic certainty    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Ingestion & GraphDB Content Specifications (65% Allocation)

### 2.1 Stage U0 Chat & Screenshot Ingress Bridge (`dual_pipeline/chat_ingress.py`)
To prevent the multimodal blindspot where working updates exist only in chat screenshots:
1. **Governed Entry Point**: Provide `nee ingest-chat-attachment`:
   ```bash
   python nee.py ingest-chat-attachment \
     --project <project_name> \
     --meeting <standardized_slug> \
     --image <path_or_buffer> \
     --author <operator_name> \
     --artifact-name <name_e.g._waterfall_table> \
     --is-finalized
   ```
2. **Deterministic Processing Pipeline**:
   - Calculate SHA-256 of image bytes.
   - Run multimodal OCR table extraction (using structured JSON vision models).
   - Compare extracted table rows against the prior version of the artifact.
   - Route to `tools/prepare_evidence_envelope_intake.py` under package `urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0`.

### 2.2 Monotonic Artifact Versioning Schema
Add structured versioning metadata to `storage/models.py` and SQLite schema:
```sql
CREATE TABLE IF NOT EXISTS artifact_version (
    artifact_id TEXT NOT NULL,
    version_id TEXT PRIMARY KEY,
    predecessor_version_id TEXT,
    project_name TEXT NOT NULL,
    meeting_slug TEXT NOT NULL,
    authority_status TEXT CHECK(authority_status IN ('draft', 'interim', 'finalized')),
    snapshot_completeness TEXT CHECK(snapshot_completeness IN ('complete_snapshot', 'partial_view')),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(predecessor_version_id) REFERENCES artifact_version(version_id)
);
```

### 2.3 Automated Assertion Lifecycle & Tombstoning Engine
When Version $N+1$ with `authority_status = 'finalized'` is ingested:
1. The engine compares rows against Version $N$.
2. For any row present in $N$ but absent in $N+1$ (e.g. the 77.5% metric row):
   - Find all assertions referencing this row or dependent action items.
   - Execute an atomic mutation:
     ```sql
     UPDATE assertion 
     SET currentness_state = 'superseded',
         superseded_at = CURRENT_TIMESTAMP,
         superseded_by_assertion_id = :superseding_assertion_id,
         retrieval_eligible = 0
     WHERE subject_node_id = :affected_node_id
       AND extraction_run_id = :prior_run_id;
     ```
   - Log cryptographic `audit_event` documenting the supersession.

---

## 3. Retrieval & Copilot Serving Specifications (35% Allocation)

### 3.1 Lifecycle-Aware Retrieval Filtering (`dual_pipeline/graph_retrieval.py`)
Modify the retrieval candidates selection in `GraphRetriever` and FTS queries:
1. For queries seeking active state ("open blockers", "current workstreams", "next steps"):
   ```sql
   SELECT a.assertion_id, a.subject_node_id, a.predicate, a.object_literal
   FROM assertion a
   WHERE a.retrieval_eligible = 1
     AND (a.currentness_state IS NULL OR a.currentness_state = 'current')
     AND a.superseded_at IS NULL;
   ```
2. Any assertion marked `superseded` or `retracted` is strictly barred from active-status prompt assembly.

### 3.2 Intra-Day Temporal Recency Decay Function
Incorporate sub-day chronological scoring into `dual_pipeline/retrieval/ranker.py`:
$$S_{\text{final}}(d) = S_{\text{hybrid}}(d) \times \exp\left(-\lambda \cdot \Delta t_{\text{hours}}\right)$$
Where:
- $\Delta t_{\text{hours}}$ is the time elapsed between the item's `recorded_at` timestamp and the latest meeting timestamp of the reporting period.
- For same-day sessions, an afternoon session (16:44 ET) receives a recency multiplier relative to an 11:05 ET morning session ($\lambda = 0.05$).
- Explicit authoritative state always overrides raw decay.

### 3.3 Copilot Epistemic Prompt Guard (`live_meeting_recorder_core/copilot.py`)
Update prompt contracts for executive synthesis:
- **Rule of Chronological Precedence**: If context includes multiple meetings from the same day, the model is instructed:
  > *"When summarizing open workstreams and blockers, cross-reference the chronology of sessions. If an item was raised as an open question in a morning session but a subsequent afternoon session finalized the deliverable or baseline without that item, do NOT assert it as an unqualified open blocker. State explicitly: 'Raised as an interim question in [Session 1], but omitted from subsequent finalized baselines.' "*

---

## 4. Governed Verification & CI Gate Criteria

To prevent regressions, the following gates must be enforced:

| Gate | Verification Target | Pass/Fail Condition |
| :--- | :--- | :--- |
| **Gate 10.1** | `test_superseded_assertion_exclusion` | Verifies that assertions marked `superseded` or `retrieval_eligible = 0` are excluded from `GraphRetriever.query_active_claims()`. |
| **Gate 10.2** | `test_intraday_recency_decay_ordering` | Verifies that an identical claim in a 16:00 session outranks an 11:00 session under equal query relevance. |
| **Gate 10.3** | `test_tombstone_lifecycle_execution` | Verifies that ingesting a finalized table lacking a previous row transitions the prior row's assertion to `currentness_state = 'superseded'`. |
| **Gate 10.4** | `test_copilot_epistemic_blocker_qualification` | Verifies that Copilot prompt generation labels uncorroborated morning blockers with epistemic uncertainty rather than active truth. |

---

## 5. Architectural Invariant Compliance
- **400-Line Module Budget**: All new modules (`chat_ingress.py`, `artifact_versioning.py`) must be partitioned into `*_core/` packages under 400 lines.
- **Unidirectional Layer Hierarchy**: L0 (storage) $\rightarrow$ L1 (ingress) $\rightarrow$ L2 (pipeline) $\rightarrow$ L3 (retrieval) $\rightarrow$ L4 (copilot). No upward cyclic dependencies.
- **Zero Raw SQLite Connections**: All database modifications must utilize the governed `ConnectionManager` with WAL mode and serialized queues.
