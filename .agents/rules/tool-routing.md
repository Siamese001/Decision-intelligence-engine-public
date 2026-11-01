---
trigger: always_on
---

# Tool Routing Rules

Governs command surfaces and interfaces, preventing governance bypass.

## Governed CLI vs. API Surface
The `nee` CLI (`python nee.py` or `python -m dual_pipeline.cli`) is the governed command surface:
- **Write Operations (CLI Only)**: `nee intake-directive-*`, `nee intake-completion-*`, `nee release-*`, `nee activate-*` (or Python in `IngestionCompletionGuard`).
- **Read-Only (CLI or Python)**: `nee query`, `nee intake-status`, `nee active-pair`, `nee runtime-evidence`, `nee preflight`.
- **Validation**: `tools/validate_*`, `tools/run_*`, `tools/prepare_*`.

## Boundaries
- **Provider-Free**: Ingestion L1, normalization, identity resolution, routing, disposition, approval, and graph/index writes.
- **Provider-Permitted**: L2D/PA/L2 reasoning (when selected), advisory reasoning, and LLM Judge evaluations.

## Output Filesystem Bypass Prohibition (`INV-GROUNDING-NO-OUTPUT-FILESYSTEM-BYPASS`)
- **Prohibited Directory Scans**: Running `ls`, `tree`, `find`, `stat`, or inline Python directory scans (`os.listdir`, `glob.glob`) targeting `output/` for knowledge retrieval, query-drift investigations, or deliverable drafting is strictly prohibited.
- **Fail-Closed Gate**: Enforced by `ProhibitedCommandGuard` and `hitl_pre_tool_guard.py` (exit code 2). All knowledge retrieval must route strictly through GraphDB traversal (`nee ask`, `query_router.py`, or `ReadOnlyGraphSnapshot`).
- **Unpromoted Run & DLQ Diagnostic (`INV-QUERY-UNPROMOTED-WORKTREE-DLQ-CHECK`)**: When a requested dated meeting returns zero records in `graph.sqlite`, state **"Not in Knowledge Graph"** and inspect `.local/dual_pipeline/dlq/` and `git worktree list` (`verify_temporal_source_existence()`) without scanning `output/` directly.

## Operator-Directed Live Execution Invariant
When the human operator explicitly requests execution under a specific model tier (e.g. `gpt-5.6-sol (high)`) or specifies `env_di`:
1. Harness must set `allow_mock=False` and connect to live endpoint using `env_di` credentials.
2. Silent fallback to mock modes or offline heuristics is strictly prohibited.
3. Record live API telemetry (`model_id`, `reasoning_effort`, `prompt_tokens`, `completion_tokens`, `latency_ms`) in durable JSON receipt.
4. On failure, fail closed immediately with an explicit error code.
