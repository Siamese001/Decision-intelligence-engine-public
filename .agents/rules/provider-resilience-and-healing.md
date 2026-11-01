---
trigger: glob
globs: "{dual_pipeline/healing_*.py,dual_pipeline/provider_*.py}"
---

# Provider Resilience & Healing Mesh Rules

Governs cloud LLM API invocations and outbound communication across pipelines.

## 1. Governed Provider Gateway & Resilience
1. **Central Gateway**: All external provider egress must route through `UnifiedL2HealerService` or `dual_pipeline/query_reasoning.py`. Ad-hoc client instantiations are forbidden.
2. **Error Handling**: Jittered exponential backoff (min 3 retries on 429/5xx), 45s hard timeout per turn, strict schema verification.
3. **Partial JSON Repair**: Truncated LLM responses undergo deterministic partial JSON repair (closing quotes, braces, brackets) before fallback escalation.
4. **Fallback Cascade**: (1) Alternate model tier -> (2) Semantic cache match -> (3) Deterministic heuristic extraction with degradation receipt. Unhandled provider failure must never leave partial unsealed graph mutations.

## 2. Strict Provider-Free Boundaries
1. **Provider-Free Stages**: Pure deterministic Python only for:
   - Ingestion L1 normalization, acoustic chunking, VAD stitching.
   - Deterministic identity resolution and entity deduplication.
   - Deterministic query routing and intent dispatch.
   - Graph mutation disposition, approval binding, and SQLite database writes.
2. **L2 Query Constraints**: Query reasoning stages (L2D, PA, L2) emit only `CANDIDATE`, `QUARANTINE`, `REJECTED_NOOP`, or `HITL_REQUIRED`. They never mutate identity, approve facts, or alter graph state.
