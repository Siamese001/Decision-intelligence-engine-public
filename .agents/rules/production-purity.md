---
trigger: always_on
---

# Production Directory Anti-Pollution & Synthetic Fixture Quarantine

Governs the boundary of `output/` and enforces quarantine of synthetic fixtures under AGENTS.md Invariant 9.

## Core Invariants
1. **Sacred Production Domain**: `output/` is strictly reserved for authentic production meeting deliverables and canonical knowledge graph projections derived from real source recordings or human intake packages.
2. **Evaluator & Test Sandboxing**: Tests, benchmarks, and evaluators are strictly prohibited from defaulting writes to `output/`. Output destinations must use ephemeral sandbox directories via `tempfile.mkdtemp(prefix="eval_sandbox_")` or dedicated fixture paths outside `output/`.
3. **Prohibition of Synthetic Personas and Dates**: Mock transcripts, fabricated personas (e.g. "Alex Rivera", "Jordan Taylor"), and fictitious dates must never be written to `output/`. Doing so is a P0 blocking defect.
4. **Mechanical Linters**: Enforced fail-closed by `tools/lint_production_purity.py`, `tools/validate_output_hierarchy.py`, and `.githooks/pre-commit`.
