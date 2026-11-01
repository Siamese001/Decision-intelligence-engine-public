# Unified Agentic Architecture & Governance Specification

> **SVP+ Agentic Engineering Reference Architecture (Amit Ayer)**
> This specification codifies the **engineering best practices, architectural foresight, and novel agentic systems engineering patterns** that govern the 6,250+ module Enterprise Decision Intelligence Engine.

## 1. Five Core Architectural Pillars
- **Skills (`.agents/skills/`)**: 25+ reusable, composable domain capabilities (multi-format document & acoustic ingestion, dialectical query & counterfactual traversal, Bayesian HITL governance, 11-gate release qualification, 5-agent graph hygiene discovery, and visual studio delivery).
- **Agents**: Goal-driven orchestrators maintaining cross-turn state, 8-surface closed-loop learning feedback, and delegating bounded work packages.
- **Sub-Agents**: Specialized executors operating under a 10-field Delegation Contract (`task_id`, `parent_task_id`, `objective`, `scope_in`, `scope_out`, `allowed_tools`, `deliverable`, `definition_of_done`, `budget`, `escalation_rule`) with mandatory teardown verification.
- **Hooks (`.githooks/`, `.agents/hooks.json`, `scripts/`)**: Deterministic `pre_tool_call`, `post_tool_call`, `pre-commit`, and `pre-push` interceptors enforcing per-test timeouts (`--timeout=60`), worktree isolation, E2E test coverage, layman clarity guards, and credential hygiene.
- **Rules (`.agents/rules/`)**: Immutable operational invariants governing storage concurrency, 5-check physical graph proof, risk-tiered change lanes, and plain-language executive delivery.

## 2. Unified L0–L6 Layer Hierarchy
1. **L0 (`storage/`, `schemas/`, `contracts/`, `config/`)**: Zero-contention SQLite WAL storage (`busy_timeout=30000`, `synchronous=NORMAL`, `foreign_keys=ON`), RFC-8785 JCS canonical hashing, `CredentialManager` secret isolation, and 3-way SSOT configuration parity.
2. **L1 (`dual_pipeline/audio_ingestion_core/`, `dual_pipeline/live_meeting_recorder_core/`)**: `<200ms` pre-CLI audio spooling, 100% local Apple Silicon Metal (`MLX Whisper`) transcription, platform per-channel diarization fusion, Stage-0 speaker preloading, and constrained global voiceprint clustering.
3. **L2 (`pipeline_b_extraction/`, `dual_pipeline/document_pipeline_core/`, `dual_pipeline/transcript_qa_core/`)**: Local pre-egress sanitization (`LocalPreEgressSanitizer`), document signal routing & tabular extraction, 3-artifact synthesis (`01`, `02`, `03`), and the 3-tier self-healing mesh (quote pointer, graph topology, and action handoff healers).
4. **L3 (`pipeline_c_retrieval/`, `dual_pipeline/graph_retrieval_core/`, `dual_pipeline/graph_scout_core/`)**: Bi-temporal assertion lifecycle management, SQLite FTS5 BM25 + 1024-d BGE-M3 hybrid RRF retrieval, cross-workstream capability bridging, 5-agent graph hygiene discovery, and Merkle-invalidated semantic caching.
5. **L4 (`evals/`, `dual_pipeline/hitl_governance_core/`, `dual_pipeline/learning_loop_core/`, `dual_pipeline/decision_engine_core/`)**: 11-gate retrieval qualification against a 210-query graded QREL benchmark, Bayesian Dirichlet-Laplace conjugate-prior ambiguity gating ($\Delta > 20\%$), 8-surface closed-loop system learning with canary nDCG auto-rollback, and 18-role W1–W7 strategic decision synthesis.
6. **L5 (`unified_pipeline/`, `dual_pipeline/graph_writer_core/`, `dual_pipeline/uwg_core/`)**: Universal Write Gateway (`UniversalWriteGateway`), `<2ms` pre-query inline candidate fast promotion (`fast_promotion.py`), atomic generational snapshot swaps (`active_pair.json`), and 5-check physical disk proof verification across both graph mutation and canonical promotion.
7. **L6 (`nee.py`, `dual_pipeline/live_meeting_recorder_core/`, `dual_pipeline/query_core/`, `ui/`, `tools/`)**: Dual-track Live Executive Copilot (`<250ms` Flash-Snap + `<1200ms` streaming reasoning), Two-Stage Coherence & Clause Speak Gate, 5-wave Counterfactual Constraint Navigator, Topological Blind-Spot Foresight Engine, Knowledge Graph Studio UI, and 34 CI/CD readiness gates.

## 3. Zero-Contention Storage & 5-Check Physical Custody Contract
All SQLite connections route through `storage/connection.py` with mandatory WAL pragmas and serialized write coordination, verified under a 16-worker concurrent stress harness (`storage/stress_harness.py`). Every graph mutation and canonical promotion produces a cryptographic proof receipt verifying:
1. Primary database file size, `mtime`, and `SHA-256` digest delta.
2. Raw disk page byte scan locating committed and promoted UUIDs directly on disk (excluding `-wal` journals).
3. `PRAGMA integrity_check` and `PRAGMA foreign_key_check` verification.
4. Independent read-only connection (`mode=ro`) row and field readback.
5. Exact per-table row count delta reconciliation.
