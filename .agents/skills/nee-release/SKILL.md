---
name: nee-release
description: NEE release lifecycle, release preparation, W0/E0 audits, BGE-M3 retrieval evaluation (E3 gate), activation receipts, and Universal Write Gateway.
---

# NEE Release Lifecycle

This skill guides the release lifecycle for NEE knowledge-graph updates.

## When to use

- After ingestion stages COMPLETE and graph changes are approved
- When preparing a new graph/index release
- When activating a release to become the active pair
- When validating release integrity and generating receipts
- When running out-of-band maintenance releases (candidate cleanup, source retirement, remediation)

## Release Lifecycle Stages

```
Intake COMPLETE → Prepare Release → W0 Audit → Retrieval Evaluation (E3) → Activate → E0 Audit → Receipt
```

### 1. Prepare Release

The release manager prepares a release bound to the approved directive:

```bash
python nee.py release-prepare --directive-id <id>
```

This creates a release artifact with:
- Exact package hashes matching the open directive
- Run IDs from the completed intake stages
- Proposed graph/index pair
- Bound operator approval

### 2. W0 Release Lifecycle Audit

The W0 audit validates:
- Release is digest-valid
- Release is approval-bound
- Package hashes and run IDs match the directive
- All required intake stages completed

### 3. Retrieval Release Evaluation (E3 Gate)

Before activation, a graph-changing release must pass a receipt-bound retrieval release evaluation under pinned BGE-M3:

```bash
# Qualify BGE-M3 runtime
python tools/qualify_bge_m3_runtime.py

# Run E3 Gate evaluation
python nee.py retrieval-release-evaluate \
  --receipt <runtime-root>/releases/release-<generation-id>.json \
  --runtime-root <runtime-root> \
  --graph <seed-graph> \
  --embedding-backend bge-m3-dev \
  --allow-dev-bge-m3 \
  --model-path <pinned-model-path> \
  --model-manifest <manifest-path> \
  --model-revision <40-hex-revision>
```

#### The 11 BGE-M3 Non-Compensating Gates

Defined in [`requirements/retrieval-release-evaluation-policy.json`](file:///Users/amitayer/Git/decision-intelligence-engine/requirements/retrieval-release-evaluation-policy.json). All 11 must pass (`PASS`):

| # | Gate Metric | Threshold | Rationale |
|---|-------------|-----------|-----------|
| 1 | `min_core_recall_at_k` | `1.0` | Zero recall loss on baseline queries |
| 2 | `min_core_mrr` | `1.0` | Zero mean reciprocal rank degradation |
| 3 | `min_core_ndcg_at_k` | `1.0` | Preserves normalized discounted cumulative gain |
| 4 | `min_delta_recall_at_k` | `1.0` | Perfect recall on newly introduced entities |
| 5 | `min_delta_mrr` | `1.0` | Top-rank accuracy on new knowledge |
| 6 | `min_delta_ndcg_at_k` | `1.0` | Ranked relevance on new delta queries |
| 7 | `critical_top_k` | `1` | High-materiality entities appear at rank 1 |
| 8 | `max_rank_regression` | `0` | No existing entity may regress in rank |
| 9 | `max_repeat_rank_drift` | `0` | Repeat runs produce identical ranks |
| 10 | `max_latency_ms` | `7,500 ms` | Strict per-query latency cap |
| 11 | `max_forbidden_leakage` | `0` | Zero ACL boundary or lane isolation breaches |

- **Strict BGE-M3 Ban**: Synthetic/mock embeddings strictly banned in release qualification paths (`python tools/validate_no_synthetic_embeddings.py --repository-root .`).
- **Scoped release deltas**: Added/removed test cases scoped strictly to component runs.
- **Query encoding cache**: Encodings cached across baseline, warm-up, and repeat runs.
- **Vectorized MaxSim**: Multi-vector scoring uses NumPy matrix operations upholding $\le 7,500\text{ ms}$.

### 4. Activate Release

```bash
python nee.py release-activate --release-id <id>
```

This validates the retrieval release evaluation receipt via `validate_activation_evaluation`, activates the new graph/index pair, and updates `active_pair.json`.

### 5. E0 Release Lifecycle Audit

The E0 audit validates:
- Activation receipt matches the release
- Activation receipt matches current `active_pair.json`
- Active graph/index pair validates
- Positive graph operations produced a changed graph hash

### 6. Terminal Completion Gate

```bash
python nee.py intake-completion-gate --directive-id <id>
```

Produces the final receipt proving the release is COMPLETE.

## Universal Write Gateway (UWG)

The UWG ([`dual_pipeline.uwg`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/uwg.py)) provides the governed write surface for all graph operations. All writes flow through this gateway:
- Graph change set application
- Index rebuilding
- Sidecar publication
- Embedding generation

## Release Types

| Type | Description | Tool |
|------|------------|------|
| Standard intake release | Normal ingestion $\to$ release | `nee.py release-prepare` |
| Candidate cleanup release | Remove stale/rejected candidates | [`candidate_cleanup.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/candidate_cleanup.py) |
| Source retirement release | Retire deprecated sources | [`source_retirement.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/source_retirement.py) |
| Combined remediation release | Multi-issue remediation | [`combined_remediation.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/combined_remediation.py) |
| Validation closeout release | Close validation items | [`validation_closeout.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/validation_closeout.py) |
| Card refactor release | Card structure changes | [`card_refactor_release.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/card_refactor_release.py) |

*(For out-of-band releases, see [`nee-graph-maintenance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-graph-maintenance/SKILL.md)).*

## Batch Admission

For candidate-only batch operations:

```bash
python tools/build_batch_admission_manifest.py
```

## Receipt Validation

A valid completion receipt must include:
- Directive ID
- Run IDs
- Receipt digest (SHA-256)
- Terminal outcome (`COMPLETE`, `BLOCKED`, `CANCELLED`)
- Active generation and graph hash
- W0 and E0 audit results

## Key Constraints

- **No empty releases**: If zero graph operations proposed, use `python nee.py intake-completion-noop` instead of manufacturing an empty release.
- **Digest binding**: Release receipt must be digest-valid.
- **Approval binding**: Release must be bound to the exact approved directive.
- **Active pair validation**: Activation receipt must match `active_pair.json`.
- **Changed hash**: Positive operations must produce a changed graph hash.

## Related Files

- Release manager: [`release.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/release.py)
- UWG: [`uwg.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/uwg.py)
- Completion guard: [`completion.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/completion.py)
- Retrieval release: [`retrieval_release.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/retrieval_release.py)
