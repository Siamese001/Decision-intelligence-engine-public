---
trigger: glob
globs: "{reports/**,receipts/**,dual_pipeline/receipt_*.py}"
---

# Receipt Taxonomies Single Source of Truth (SSOT)

Governs the unified definition, schema, storage paths, and cryptographic verification mechanisms for all receipts across the repository.

---

## 1. Disambiguation of the Term "Receipt"

Across the repository, the word **receipt** refers to an immutable, cryptographically verifiable record proving an execution occurred under specific constraints. To eliminate ambiguity, all receipts MUST declare a typed classification prefix matching this taxonomy.

---

## 2. The 14 Governed Receipt Types

| # | Type Prefix | Description | Primary Generator | Canonical Storage Path | Verification Mechanism |
|---|-------------|-------------|-------------------|------------------------|------------------------|
| 1 | `wave_receipt` | Wave completion & sequential lock receipt | `tools/record_wave_receipt.py` | `reports/wave<N>_receipt.json` | Chained parent digest, `artifacts/locks/wave-<N>.complete` |
| 2 | `release_receipt` | Graph release audit & lifecycle receipt | `nee release-prepare` / W0 audit | `reports/release_receipt_<id>.json` | Digest-valid, approval-bound |
| 3 | `activation_receipt` | Active graph/index pair activation receipt | `nee release-activate` | `storage/runtime/active_pair.json` | Hash match with release receipt |
| 4 | `idempotent_noop_receipt` | Zero-mutation proof receipt | `nee intake-completion-noop` | `<runtime_root>/receipts/` | Immutable proof active pair remained unchanged (+0 delta) |
| 5 | `alr_signature` | Forensic Ambiguity Lock Register receipt | Output 01 transcript DAG | `hitl_decision_ledger.json` | Ed25519 signature over RFC 8785 canonical bytes |
| 6 | `pr_receipt` | PR & branch merge qualification receipt | `tools/verify_main_readiness.py` | `reports/main_readiness_receipt.json` | Gate 1-11 aggregate receipt |
| 7 | `learning_proposal_receipt` | Parameter adaptation & rollback receipt | `tools/govern_learning_proposals.py` | `reports/learning_proposals/` | Bidirectional rollback receipt |
| 8 | `meeting_isolated_receipt` | Isolated meeting deliverable receipts (4 files) | `nee ingest-meeting` | `output/<project>/<date>/<slug>/` | Required in universal meeting subfolder: `qa_gates_report.json`, `llm_judge_evaluation.json`, `final_release_decision.json`, `intake_package_*.json` |
| 9 | `qrels_eval_receipt` | Benchmark grading & NDCG@10 receipt | `tools/score_qrels_v2.py` | `reports/qrels_eval_receipt_*.json` | Mean NDCG@10 / MRR@10 digest |
| 10 | `retrieval_eval_receipt` | Pinned BGE-M3 11-gate retrieval evaluation | `tools/run_retrieval_eval.py` | `<runtime_root>/retrieval_evaluations/` | 11 non-compensating gate pass |
| 11 | `test_audit_receipt` | Test suite gap & dimension audit receipt | `tools/audit_testing_gaps.py` | `reports/testing_gap_audit.json` | 8-dimension score verification |
| 12 | `audit_event_envelope` | OpenTelemetry pipeline event envelope | `dual_pipeline/telemetry` | SQLite `audit_event` table | Monotonic sequence & trace ID |
| 13 | `graph_scout_receipt` | Zero-mutation graph health audit receipt | `tools/run_graph_scout.py` | `reports/graph_scout_receipt_*.json` | `PRAGMA query_only = ON` verification |
| 14 | `harmonization_receipt` | Governance harmonization audit report | `tools/audit_governance_harmonization.py` | `reports/governance-harmonization/` | Zero CRITICAL threshold drift findings |
| 15 | `active_ingestion_completion_receipt` | Production active ingestion completion receipt | `tools/run_active_ingestion_acceptance.py` / `publish_helper` | `receipts/active_graph_ingestion/` | Cryptographic canonical digest, table delta verification, active generation link |

---

## 3. The Isolated 4 Meeting Receipts

In meeting ingestion pipelines under the Universal Subfolder Architecture (`output/<project_name>/<meeting_date>/<standardized_slug>/`), exactly four supporting receipts are generated alongside the three primary deliverables (`01_*`, `02_*`, `03_*`):

1. **`qa_gates_report.json`**: Stage QA gates audit receipt validating transcript fidelity, speaker attribution consistency, and structural compliance.
2. **`llm_judge_evaluation.json`**: Multi-dimensional judge evaluation receipt scoring relevance, completeness, hallucination avoidance, and attribution accuracy.
3. **`final_release_decision.json`**: Release approval and activation decision receipt binding the reviewed projection to the graph mutation lifecycle.
4. **`intake_package_*.json`** (or `evidence_envelope_intake.json`): Governed intake package receipt formatted per `urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0`.

These 4 receipts MUST reside inside the dedicated meeting subfolder and MUST NEVER be emitted into `output/transcript_cache/` or the meeting root directory.
*Pre-Publication Boundary*: These 4 receipts certify artifact quality and release preparation; they do NOT prove active graph coverage or constitute completed ingestion.

---

## 4. Invariant Rules for All Receipts

1. **Cryptographic Integrity**: Every receipt must include an RFC 8259 canonical SHA-256 hash or Ed25519 signature over its payload contents.
2. **Parent Chaining**: Multi-phase operations (waves, releases) must include a `parent_receipt` path and `parent_receipt_digest` linking to the preceding terminal receipt.
3. **Immutability**: Once recorded, receipts must never be mutated. State transitions require minting a new receipt with an explicit transition link.
4. **Zero-Op Accountability**: Runs that result in zero mutations must emit an `idempotent_noop_receipt` rather than omitting documentation.
5. **Ingestion Completion Grounding**: An ingestion run may only emit an `active_ingestion_completion_receipt` or report `COMPLETE` when backed by verified active graph deltas (`COMPLETE_UPDATED`) or proven existing coverage (`COMPLETE_ALREADY_PRESENT`). Skipped publication or artifact-only QA PASS must never be recorded as completed ingestion.

