---
trigger: always_on
---

# Layer Architectural Integrity & Module Budget Rules

Governs unidirectional layer dependencies and modular complexity budgets.

## 1. Unidirectional Layer Dependency DAG
All components must adhere strictly to the unidirectional hierarchy L0 -> L6:
- **L0**: Storage, Database, Schema, Contracts
- **L1**: Normalization, Acoustic STT, Transcripts, Splicing
- **L2**: Extraction, Entities, Assertion Proposals, Pre-Egress Sanitization
- **L3**: Knowledge Graph, Vector Indexes, Hybrid Search, Sidecars
- **L4**: Reasoning, Dialectical Judge, Retrieval Evaluation, Query Routing
- **L5**: Pipeline Orchestration, Services, Batch Admission, Ingestion Coords
- **L6**: User Interface, Governed CLI, MCP Server Tools, Exports

### Invariants:
1. **Zero Upward Dependencies**: Lower layers must never import higher layers (e.g. L0 importing L4 or L2 is a blocking defect).
2. **Zero Lateral Pipeline Coupling**: Sub-pipelines communicate solely via contracts in `contracts/dual_pipeline/` or shared core interfaces. Direct cross-pipeline module imports are barred.
3. **AST Linter**: Enforced via `tools/lint_layer_imports.py`.

## 2. Module Size Budget & Cognitive Complexity Ceilings
1. **Module Budget Ceiling (400 Lines Clean Budget)**: 400 lines serves as the primary review signal across `dual_pipeline/`, `storage/`, `pipeline_*`, and `evals/` (excluding tests and generated schemas).
2. **Nonblocking Review Allowances**:
   - Modules between 401–650 lines of ordinary implementation emit a nonblocking review warning when cognitive complexity (<= 15) and layer dependencies pass. Review cohesion within normal PRs; zero forced micro-compression or premature fragmentation.
   - Declarative tables and data models validated against executable AST structure (zero branch logic, CC <= 5) emit a nonblocking review warning up to 800 lines. Filename substrings cannot grant this allowance.
   - Modules above applicable warning ceilings require an approved exception (`config/governance/file_budget_exclusions.json`) or a concern-based refactor.
3. **Complexity & Invariant Invariance**: Size allowances cannot waive independent blocking checks: cognitive complexity ceiling (<= 15), unidirectional L0–L6 imports, and monotonic baseline ratchets (`reports/file_budget_burndown_baseline.json`).
4. **Function Complexity**: Max 60 lines per function, max cognitive complexity 15. Enforced via `tools/lint_file_budgets.py` and `tools/file_budget_core/policy.py`.
