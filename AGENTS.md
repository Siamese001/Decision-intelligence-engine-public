# NEE Knowledge Graph & Decision Intelligence Agent Controls

> [!IMPORTANT]
> **Canonical Invariant Index:** Generated from `governance/policy_registry.yaml`.

## Core Operational Invariants

| ID | Invariant Title | SSOT Threshold | Enforcer / Verification | Governed Rule |
| :--- | :--- | :--- | :--- | :--- |
| `INV-BUDGET-FILE-LINES` | File Size Line Budget | `400 lines` | `policy.py` | [layer-architectural-integrity.md](.agents/rules/layer-architectural-integrity.md) |
| `INV-COMPLEXITY-MAX` | Maximum Cyclomatic Complexity | `15 mccabe_score` | `pyproject.toml` | [layer-architectural-integrity.md](.agents/rules/layer-architectural-integrity.md) |
| `INV-HITL-AMBIGUITY-MARGIN` | HITL Ambiguity Margin Threshold | `0.2 fraction` | `parameters.py` | [hitl-decisions.md](.agents/rules/hitl-decisions.md) |
| `INV-HITL-UNRESOLVED-ENTITY-MARGIN` | HITL Unresolved Entity Margin Threshold | `0.15 fraction` | `detector_d6_entity.py` | [hitl-decisions.md](.agents/rules/hitl-decisions.md) |
| `INV-HITL-DIVERGENCE-MARGIN` | HITL Divergence Margin Threshold | `0.2 fraction` | `parameters.py` | [hitl-decisions.md](.agents/rules/hitl-decisions.md) |
| `INV-AGENTS-MD-MAX-SIZE` | AGENTS.md Size Injection Ceiling | `12288 bytes` | `generate_agents_index.py` | [AGENTS.md](AGENTS.md) |
| `INV-TEST-TIMEOUT-HARD` | Hard Pytest Execution Timeout | `60 seconds` | `pyproject.toml` | [wave-progression.md](.agents/rules/wave-progression.md) |
| `INV-WAVE-PLAN-MAX-WORDS` | Maximum New Wave Plan Length | `1000 words` | `validate_implementation_plan.py` | [wave-progression.md](.agents/rules/wave-progression.md) |
| `INV-TEST-TIMEOUT-CLASS-B` | Class B Fast Verification Timeout | `60 seconds` | `run_tests_with_progress.py` | [wave-progression.md](.agents/rules/wave-progression.md) |
| `INV-MIN-E2E-COVERAGE-SCORE` | Minimum E2E Testing Dimension Score | `80.0 percent` | `audit_testing_gaps.py` | [e2e-and-edge-case-coverage.md](.agents/rules/e2e-and-edge-case-coverage.md) |
| `INV-HITL-BAYESIAN-PRIOR-STRENGTH` | HITL Bayesian Prior Strength Default | `4.0 float` | `parameters.py` | [system-learning-and-feedback.md](.agents/rules/system-learning-and-feedback.md) |
| `INV-HITL-MIN-CONFIDENCE-FLOOR` | Minimum Autonomous Confidence Floor | `0.65 fraction` | `parameters.py` | [hitl-decisions.md](.agents/rules/hitl-decisions.md) |
| `INV-HITL-ENTROPY-CEILING` | Maximum Uncertainty Entropy Ceiling | `0.75 fraction` | `parameters.py` | [hitl-decisions.md](.agents/rules/hitl-decisions.md) |
| `INV-GROUNDING-MANDATORY-ASSERTION` | Mandatory GraphDB Assertion Grounding | `1.0 ratio` | `delivery_boundary.py` | [runtime-evidence.md](.agents/rules/runtime-evidence.md) |
| `INV-GROUNDING-STRICT-METRICS` | Deterministic Text Metric Limits | `500 words` | `metrics.py` | [runtime-evidence.md](.agents/rules/runtime-evidence.md) |
| `INV-GROUNDING-DELIVERY-IMMUTABILITY` | Post-Verification Delivery Immutability | `1.0 hash_parity` | `delivery_boundary.py` | [runtime-evidence.md](.agents/rules/runtime-evidence.md) |
| `INV-GROUNDING-SAFE-FAILURE-RESPONSE` | Safe Failure Response Sanitization | `1.0 suppression_ratio` | `delivery_boundary.py` | [runtime-evidence.md](.agents/rules/runtime-evidence.md) |
| `INV-INGESTION-ACTIVE-COMPLETION` | Production Ingestion Active Completion | `1.0 ratio` | `publish_helper.py` | [ingestion-lifecycle.md](.agents/rules/ingestion-lifecycle.md) |
| `INV-INGESTION-ENGINEERING-ISOLATION` | Engineering Graph Isolation | `1.0 isolation_parity` | `connection.py` | [concurrency-and-storage-safety.md](.agents/rules/concurrency-and-storage-safety.md) |
| `INV-OUTPUT-MAX-WORDS` | Maximum Engineering Completion Word Ceiling | `300 words` | `verifier.py` | [engineering-output.md](.agents/rules/engineering-output.md) |
| `INV-OUTPUT-MAX-CHARS` | Maximum Engineering Completion Character Ceiling | `3500 characters` | `verifier.py` | [engineering-output.md](.agents/rules/engineering-output.md) |
| `INV-OUTPUT-PROGRESS-MAX-WORDS` | Maximum Progress Update Word Ceiling | `20 words` | `verifier.py` | [engineering-output.md](.agents/rules/engineering-output.md) |
| `INV-GRAPH-MUTATION-PHYSICAL-PROOF` | Mandatory Physical Proof for Graph Mutations | `5 checks` | `proof.py` | [graph-mutation-physical-proof.md](.agents/rules/graph-mutation-physical-proof.md) |

## Primary Execution Principles

1. **Wave Gating:** Advance only after observed behavior passes. See [wave-progression.md](.agents/rules/wave-progression.md).
2. **Human Decisions:** Confirm via `ask_user_question` when margin $\Delta \le 30\%$. See [hitl-decisions.md](.agents/rules/hitl-decisions.md).
3. **Plain Language:** Plain English overrides code citations. Translate behavior into business meaning.
4. **Ingestion & Isolation:** Ingestion requires approval; tests use sandboxes.
5. **Worktree Isolation:** Branch into `.worktrees/<slug>` via `pr_governance.py start`. See [git-worktree-governance.md](.agents/rules/git-worktree-governance.md).
6. **Progress Tracking:** Operations (>3s) use progress bars. See [progress-indicators.md](.agents/rules/progress-indicators.md).
7. **Fast Verification:** Receipt verification requires `--timeout=60`. See [e2e-and-edge-case-coverage.md](.agents/rules/e2e-and-edge-case-coverage.md).
8. **Concise Output:** Target ~120 words (max 300). See [engineering-output.md](.agents/rules/engineering-output.md).

## Runtime and retrieval claims are evidence-bound
<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->

Before stating that an embedding backend is active or inactive, or that retrieval testing/evidence is present or absent, run `python -m dual_pipeline.runtime_evidence inventory` and inspect runtime, model, generation, query-run, and evidence bindings.
- Every `active_pair.json` is scoped to its explicit runtime root; never treat a default-looking directory as the single global runtime.
- Follow every existing `runtime_root` and `evidence_root` pointer in governed receipts and include deployment-owned roots.
- An empty filename or pattern search is never proof that runtime or retrieval evidence is absent. Report it only as “not found in the explicitly searched scope.”
- `production_qualified: false` is an authorization boundary; it does not mean that a model is inactive, unexecuted, or untested.
- Sealed retrieval release evaluations are retrieval evidence. Inventory `<runtime_root>/retrieval_evaluations/` and bind exact status, pair, model, receipt, gates, digest, and path before making presence/absence claims.
- Bind positive claims with `python -m dual_pipeline.runtime_evidence claim`.
- Negative claims require explicit `--scope-root` values plus `--complete-scope`; if claim fails, do not make the claim.
- Report exact runtime root, active generation, embedding backend, model ID/revision, relevant run IDs, terminal outcomes, and evidence paths.

The tracked pre-commit and pre-push hooks enforce the continued presence of this control and its focused regression tests.

<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->

## Skills, agents, sub-agents, hooks, and rules unified architecture
See [.agents/rules/unified-agent-architecture.md](.agents/rules/unified-agent-architecture.md).

### Five Core Architectural Pillars
- **Skills (What work gets done)**: Skills are the building blocks.
- **Agents (Who decides & orchestrates)**: Agents orchestrate and decide.
- **Sub-Agents (Specialized executors)**: Sub-Agents specialize and scale.
- **Hooks (When to extend)**: Hooks extend behavior.
- **Rules (What must be true)**: Rules govern.

### Shared Foundations (Common to All)
Memory, ledgers, graphs, telemetry, learning.

### Structural Interactions & Delegation Contracts
Agents use skills, delegate via mandatory contracts.

### End-to-End Execution Flow (7-Step Lifecycle)
1. Verify -> 2. Plan -> 3. Delegate -> 4. Execute -> 5. Validate -> 6. Seal -> 7. Teardown.

## Governed Rule Registry Index

The repository defines **36 modular rules** in `.agents/rules/`, activated deterministically by trigger configuration:

| Rule Document | Trigger Mode | Scope & Core Responsibility |
| :--- | :--- | :--- |
| [agent-routing.md](.agents/rules/agent-routing.md) | `model_decision` | Agent Routing |
| [career-leverage-firewall.md](.agents/rules/career-leverage-firewall.md) | `model_decision` | Career Leverage |
| [concurrency-and-storage-safety.md](.agents/rules/concurrency-and-storage-safety.md) | `always_on` | Concurrency |
| [conformance-map.md](.agents/rules/conformance-map.md) | `model_decision` | Conformance Map |
| [contract-enforcement.md](.agents/rules/contract-enforcement.md) | `glob` | Contract Enforcement |
| [conversational-communication-style.md](.agents/rules/conversational-communication-style.md) | `model_decision` | Conversational Commu |
| [credential-isolation-and-egress-sanitization.md](.agents/rules/credential-isolation-and-egress-sanitization.md) | `always_on` | Credential Isolation |
| [data-classification.md](.agents/rules/data-classification.md) | `model_decision` | Data Classification |
| [e2e-and-edge-case-coverage.md](.agents/rules/e2e-and-edge-case-coverage.md) | `model_decision` | E2E and Edge-Case Co |
| [engineering-output.md](.agents/rules/engineering-output.md) | `always_on` | Engineering Output |
| [figma-visual-governance.md](.agents/rules/figma-visual-governance.md) | `glob` | Figma 4K Visual |
| [git-worktree-governance.md](.agents/rules/git-worktree-governance.md) | `always_on` | Git & Worktree |
| [graph-mutation-physical-proof.md](.agents/rules/graph-mutation-physical-proof.md) | `always_on` | Mandatory Physical P |
| [graphdb-context-prompt-governance.md](.agents/rules/graphdb-context-prompt-governance.md) | `model_decision` | GraphDB Context Prom |
| [hitl-decisions.md](.agents/rules/hitl-decisions.md) | `always_on` | HITL Decision |
| [ingestion-lifecycle.md](.agents/rules/ingestion-lifecycle.md) | `glob` | Ingestion Lifecycle |
| [layer-architectural-integrity.md](.agents/rules/layer-architectural-integrity.md) | `always_on` | Layer Architectural |
| [model-neutral-artifact-naming.md](.agents/rules/model-neutral-artifact-naming.md) | `always_on` | Model-Agnostic Artif |
| [pr-governance.md](.agents/rules/pr-governance.md) | `glob` | PR Governance |
| [pr-queue-summary.md](.agents/rules/pr-queue-summary.md) | `model_decision` | PR Queue / Branch Su |
| [production-derivation-and-anti-overfitting.md](.agents/rules/production-derivation-and-anti-overfitting.md) | `model_decision` | Production Derivatio |
| [production-purity.md](.agents/rules/production-purity.md) | `always_on` | Production Directory |
| [progress-indicators.md](.agents/rules/progress-indicators.md) | `always_on` | Progress Indicators |
| [prompt-cache-optimization.md](.agents/rules/prompt-cache-optimization.md) | `glob` | Prompt Cache Optimiz |
| [provider-resilience-and-healing.md](.agents/rules/provider-resilience-and-healing.md) | `glob` | Provider Resilience |
| [receipt-taxonomies.md](.agents/rules/receipt-taxonomies.md) | `glob` | Receipt Taxonomies |
| [risk-tiered-change-lanes.md](.agents/rules/risk-tiered-change-lanes.md) | `model_decision` | Risk-Tiered Change L |
| [runtime-evidence.md](.agents/rules/runtime-evidence.md) | `glob` | Runtime and Retrieva |
| [scratchpad-reasoning-transparency.md](.agents/rules/scratchpad-reasoning-transparency.md) | `model_decision` | Agent Reasoning, Scr |
| [streaming-lifecycle-and-resilience.md](.agents/rules/streaming-lifecycle-and-resilience.md) | `glob` | Streaming Lifecycle |
| [system-learning-and-feedback.md](.agents/rules/system-learning-and-feedback.md) | `glob` | System Learning & |
| [temporal-representation-ssot.md](.agents/rules/temporal-representation-ssot.md) | `glob` | Temporal Representat |
| [tool-routing.md](.agents/rules/tool-routing.md) | `always_on` | Tool Routing |
| [transcript-synthesis.md](.agents/rules/transcript-synthesis.md) | `glob` | Raw Transcript Synth |
| [unified-agent-architecture.md](.agents/rules/unified-agent-architecture.md) | `model_decision` | Unified Agent |
| [wave-progression.md](.agents/rules/wave-progression.md) | `always_on` | Wave Progression |

---
*Generated by `tools/generate_agents_index.py`. Enforces `INV-AGENTS-MD-MAX-SIZE` (<= 12 KB).* 
