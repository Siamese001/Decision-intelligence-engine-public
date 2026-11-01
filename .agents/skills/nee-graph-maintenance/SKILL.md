---
name: nee-graph-maintenance
description: Out-of-band knowledge graph maintenance, candidate cleanup, source retirement, remediation closeout, card refactoring, and graph garbage collection.
---

# NEE Knowledge Graph Maintenance Operations

This skill guides out-of-band lifecycle maintenance, remediation, and structural card operations.

## When to use

- Running the **Graph Agent** (`knowledge-graph-hygiene-agent` sidecar): **MUST** first read `/Users/amitayer/.gemini/config/sidecars/knowledge-graph-hygiene-agent/sidecar.json` and execute its dereferenced canonical prompt [`automations/prompts/graph_hygiene_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/graph_hygiene_agent.md) end-to-end (`Step 0.1` isolated worktree creation via `tools/pr_governance.py start` -> 16-table pre-flight -> 8-Agent Council discovery -> wave plan in `docs/plans/<branch-slug>.md` -> atomic HITL cluster approvals -> generation promotion -> post-verification -> branch commit, `tools/pr_governance.py rebase` onto `main`, and governed PR merge via `tools/pr_governance.py submit` / `merge` to `main`). Never bypass the sidecar specification by invoking standalone scripts directly on `main`.
- Running the **Storage Agent** (`autonomous-storage-repository-clutter-hygiene-protocol` sidecar): **MUST** first read `/Users/amitayer/.gemini/config/sidecars/autonomous-storage-repository-clutter-hygiene-protocol/sidecar.json` and execute [`automations/prompts/storage_hygiene_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/storage_hygiene_agent.md) end-to-end (`Step 0.1` isolated worktree creation -> dry-run manifest -> wave plan in `docs/plans/<branch-slug>.md` -> atomic G0–G4/G6–G7 HITL approvals -> branch commit, `tools/pr_governance.py rebase`, and governed PR merge to `main`).
- Purging stale, unpromoted, or rejected candidate assertions
- Retiring deprecated or superseded sources with tombstone receipts
- Executing combined remediation packages across multiple graph components
- Closing out validation queue items via G1/C1/R1 decision surfaces
- Refactoring Markdown knowledge cards (`cards/NEE_*.md`) or regenerating card views
- Running graph garbage collection to prune unreferenced nodes

## Maintenance Engines

### 1. Candidate Cleanup ([`candidate_cleanup.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/candidate_cleanup.py))

Removes stale or rejected candidate assertions that were never promoted to the approved lane:

```bash
python -m dual_pipeline.candidate_cleanup \
  --runtime-root <runtime-root> \
  --max-age-days 30 \
  --output-package <package-dir>
```

Produces a candidate-cleanup release package ready for release activation.

### 2. Source Retirement ([`source_retirement.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/source_retirement.py))

Retires deprecated sources, moves them to cold storage, and generates cryptographic tombstone receipts:

```bash
# Prepare retirement control package
python tools/prepare_source_retirement_control.py \
  --source-id <source-id> \
  --retirement-reason "SUPERSEDED_BY_V55" \
  --output-dir <package-dir>
```

### 3. Combined Remediation ([`combined_remediation.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/combined_remediation.py))

Executes atomic multi-issue remediation packages:

```bash
# Run preflight check
python tools/prepare_combined_remediation_preflight.py

# Prepare remediation package
python tools/prepare_combined_remediation_control_package.py --output <package-dir>
```

### 4. Validation Closeout ([`validation_closeout.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/validation_closeout.py))

Closes open validation items using the three-table decision surface (G1 graph delta, C1 metrics, R1 card additions):

```bash
python -m dual_pipeline.validation_closeout \
  --directive-id <id> \
  --items <items.json>
```

### 5. Card Refactoring & View Synchronization ([`card_refactor_release.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/card_refactor_release.py))

Synchronizes governed Markdown cards (`cards/NEE_00_*.md` through `NEE_22_*.md`) with graph entities:

```bash
python tools/cards_refactor.py --verify-only
python tools/cards_refactor.py --apply
```

Regenerates read-only projections in [`card_views.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/card_views.py).

### 6. Graph Garbage Collection ([`graph_gc.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/graph_gc.py))

Prunes orphaned nodes and dangling relationship edges:

```bash
python tools/graph_gc.py --graph <path-to-graph.json> --dry-run
python tools/graph_gc.py --graph <path-to-graph.json> --execute
```

### 7. Graph Health & Remediation Scout ([`graph_scout.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/graph_scout.py))

Read-only diagnostic scout that scans for near-duplicate entities, temporal contradictions, and backlog clutter, emitting contract-sealed `learning-change-proposal:1.0.0` packages:

```bash
# Run via nee CLI
python nee.py graph-scout --runtime-root <runtime-root>

# Or run via standalone tool with custom thresholds
python tools/run_graph_scout.py --runtime-root <runtime-root> --similarity-threshold 0.75
```

## Release & Activation Lifecycle

All maintenance actions that mutate the graph must follow the standard release cycle documented in [`nee-ingestion`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-ingestion/SKILL.md) and [`nee-release`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-release/SKILL.md).

## Related Files

- Candidate cleanup: [`candidate_cleanup.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/candidate_cleanup.py)
- Source retirement: [`source_retirement.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/source_retirement.py)
- Combined remediation: [`combined_remediation.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/combined_remediation.py)
- Validation closeout: [`validation_closeout.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/validation_closeout.py)
- Card refactor: [`card_refactor_release.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/card_refactor_release.py)
- Graph GC: [`graph_gc.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/graph_gc.py)
- Graph Scout: [`graph_scout.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/graph_scout.py)

