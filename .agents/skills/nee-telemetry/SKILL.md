---
name: nee-telemetry
description: NEE pipeline telemetry and observability, RunRecorder, OpenTelemetry envelopes, durable learning ledger, and runtime state inspection. Use for health audits.
---

# NEE Telemetry & Observability

This skill provides access to pipeline telemetry, runtime evidence, and health state.

## When to use

- Running the **Pipeline Agent** (`pipeline-outcome-retrieval-assurance` sidecar): **MUST** first read `/Users/amitayer/.gemini/config/sidecars/pipeline-outcome-retrieval-assurance/sidecar.json` and execute [`automations/prompts/pipeline_outcome_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/pipeline_outcome_assurance_agent.md) end-to-end (`Step 0.1` isolated worktree creation via `tools/pr_governance.py start` -> Stage 0–4 discovery -> wave plan in `docs/plans/<branch-slug>.md` -> atomic HITL -> branch commit, `tools/pr_governance.py rebase`, and governed PR merge to `main`).
- Checking pipeline health or run history
- Inspecting the current runtime state (active pair, generation, backend)
- Reviewing telemetry events from intake or query runs
- Checking durable learning ledger entries and learning change proposals
- Inspecting assurance tiers and debugging pipeline failures

## Runtime State Inspection

### Active Pair

```bash
python nee.py active-pair
```

Returns the current graph/index pair: generation ID, graph hash, index path, and runtime root.

### Runtime Evidence Inventory

```bash
python nee.py runtime-evidence inventory
```

Returns: runtime root, active generation, embedding backend, model ID/revision, query-run bindings, and evidence paths.

### Preflight Checks

```bash
python nee.py preflight
```

Checks: CPU compatibility, pip dependencies, runtime contract, locked environment.

### Internal Operational Status

```bash
python nee.py internal-operational-status
```

Returns operational health summary including assurance tiers.

## Telemetry Architecture

### RunRecorder ([`dual_pipeline/telemetry.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/telemetry.py))

The `RunRecorder` captures structured run telemetry:
- Run ID and timestamp
- Use case (`intake`, `query`, `shared`)
- Stage progression (`U0` through `L4`)
- Decision outcomes
- Error and retry counts
- Latency and duration metrics

### OpenTelemetry Event Envelope

Per the `otel-event-envelope` contract, only safe operational attributes are recorded:

| Attribute | Purpose |
|-----------|---------|
| `run_id` | Correlate spans in one run |
| `use_case` | `intake`, `query`, or `shared` |
| `stage` | `U0` through `L4` identity |
| `route` | Selected route |
| `lane` | `approved`, `candidate`, or `external-public` |
| `decision_status` | Outcome without raw content |
| `model_id` | Model identifier |
| `generation_id` | Index/model generation |
| `graph_snapshot` | Exact graph binding |
| `input_digest` / `output_digest` | Content bindings |

### Privacy Boundary

**Strictly forbidden in telemetry**: Prompts, response text, raw evidence content, secrets/credentials, and meeting content.

### Durable Learning Ledger

Every `learning-event` records:
- Event, trace, span, and run identifiers
- Policy, graph, model, or index bindings
- Input and output digests
- Complete correlation object
- Governed payload reference and digest

### Learning Change Proposals

`learning-change-proposal` artifacts must adhere to contract boundaries:
- `status`: must always be `proposed`
- `approval_required`: must always be `true`
- `auto_apply`: must always be `false` (runtime self-modification is strictly forbidden)

## Pipeline Assurance Tiers

Check assurance levels programmatically or via CLI:

```python
from dual_pipeline.assurance import pair_assurance_tier, backend_assurance_tier
from dual_pipeline.core import read_json

active_pair = read_json("runtime/active_pair.json")
p_tier = pair_assurance_tier(active_pair)
b_tier = backend_assurance_tier(active_pair.get("embedding_backend"))
print(f"Pair Assurance: {p_tier}, Backend Assurance: {b_tier}")
```

## Prompt Caching Diagnostics
Token consumption and cache miss diagnostics are automatically captured under the OpenTelemetry event envelope.
- **Span Attributes**: Look for `gen_ai.prompt_cache.type` (e.g., `cache_miss`), `gen_ai.prompt_cache.reason` (e.g., `tools_changed`), `gen_ai.prompt_cache.reusable_tokens`, and `gen_ai.usage.cached_tokens`.
- **Querying Miss Ratios**: When diagnosing token bloat, query the trace logs for `gen_ai.prompt_cache.reason` to identify unstable tool schemas or dynamically shifting prefixes that broke the prompt cache.

## Related Files

- Telemetry recorder: [`telemetry.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/telemetry.py)
- Assurance engine: [`assurance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/assurance.py)
- Runtime evidence: [`runtime_evidence.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/runtime_evidence.py)
- Preflight: [`preflight.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/preflight.py)
- Internal ops: [`internal_operational.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/internal_operational.py)
