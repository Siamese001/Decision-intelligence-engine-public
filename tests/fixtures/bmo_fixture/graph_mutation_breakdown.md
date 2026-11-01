## Tier 1 Scoped Scout — GraphDB Mutation Breakdown

**Run Mode:** Pre-Mutation Diagnostic (read-only, `PRAGMA query_only = ON`)
**Touched Entity Proposals:** 19
**Near-Duplicate Findings:** 87
**Proposals Emitted:** 67

### Scoped Graph Health & Duplicate Scout (Phase 3 Hot Path)
- **Proposals Emitted to Queue:** 67

### Granular GraphDB Delta Table

| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |
| :--- | :--- | :--- | :--- | :--- |
| `node` | 864 | — | +0 | Pre-mutation diagnostic scan (unmutated) |
| `source` | 250 | — | +0 | Unchanged |
| `source_version` | 273 | — | +0 | Unchanged |
| `extraction_run` | 104 | — | +0 | Unchanged |
| `evidence_item` | 2887 | — | +0 | Unchanged |
| `assertion` | 2759 | — | +0 | Unchanged |
| `assertion_evidence` | 3002 | — | +0 | Unchanged |
| `search_document` | 3334 | — | +0 | Unchanged |
| `search_fts` | 3334 | — | +0 | Unchanged |
| `audit_event` | 9109 | — | +0 | Unchanged |
| `reserved_11` | 0 | — | +0 | Unchanged |

### Sidecar Vector Table Counts

| Sidecar Table | Baseline Count | Activated Count | Delta | What Changed |
| :--- | :--- | :--- | :--- | :--- |
| `dense_vector` | 0 | — | +0 | Unchanged |
| `sparse_weight` | 0 | — | +0 | Unchanged |
| `colbert_token` | 0 | — | +0 | Unchanged |
| `retrieval_record` | 0 | — | +0 | Unchanged |

### Itemized Assertions Table

| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |
| :--- | :--- | :--- | :--- | :--- |
| *(none)* | *(none)* | *(none)* | Pre-mutation diagnostic scan | DIAGNOSTIC_PASS |

### Idempotency & Hash Transition

- **Transition State:** `PRE_MUTATION_DIAGNOSTIC`
- **Active Generation:** Preserved strictly unchanged
- **Graph Mutation Status:** Zero persistent side-effects until authoritative activation
