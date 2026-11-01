---
trigger: model_decision
---

# Conformance Map: AGENTS.md → Enforceable Artifacts

Traces `AGENTS.md` sections to enforceable rules, skills, hooks, and tests in `.agents/`.

## Control Traceability Matrix

| Section / Control | Primary Rule | Skill | Enforcement Hook / Linter | Test Coverage |
| :--- | :--- | :--- | :--- | :--- |
| **Ingestion intent is persistent** | `ingestion-lifecycle.md` | `nee-ingestion` | `nee-pre-mutation-guard` | `test_ingestion_directive_invariants` |
| **Mutation approval lifecycle** | `ingestion-lifecycle.md` | `nee-release`, `nee-graph-maintenance` | `nee-post-ingestion-receipt` | `tools/nee_pre_mutation_gate.py` |
| **Allowed terminal outcomes** | `ingestion-lifecycle.md` | `nee-ingestion` | `nee-terminal-outcome-guard` | `dual_pipeline/graph_delta.py`, `test_graph_mutation_summary.py`, `test_ingestion_completion_guard.py` |
| **Wave progression is execution-evidence-gated** | `wave-progression.md` | `nee-orchestration`, `nee-decision-intelligence` | `nee-wave-progression-guard`, `validate_implementation_plan.py` | `test_governance_rules.py`, `test_implementation_plan_governance.py` |
| **HITL decisions are atomic, optionated, and confidence-scored** | `hitl-decisions.md` | `nee-hitl-governance`, `nee-governance` | `nee-hitl-atomic-guard` | `test_hitl_governance_enforcement.py` |
| **Progress bars mandatory** | `progress-indicators.md` | `nee-testing-protocol` | `run_tests_with_progress.py` | `test_progress_governance_rules.py`, `test_test_progress_runner.py` |
| **Raw transcript synthesis contract-bound** | `transcript-synthesis.md` | `nee-transcript-synthesis` | `nee-transcript-synthesis-guard`, `RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md` | `test_raw_transcript_synthesis_prompt.py`, `test_lint_no_custom_ingest_scripts.py` |
| **Runtime evidence-bound** | `runtime-evidence.md` | `nee-release`, `nee-telemetry` | `dual_pipeline.runtime_evidence` | `test_runtime_evidence_claims.py` |
| **§"Skills, agents, sub-agents, hooks, and rules unified architecture"** | `unified-agent-architecture.md` | `agent-subagent-orchestration`, `agent-runtime-engineering` | `nee-architecture-lifecycle-guard`, `nee-subagent-delegation-guard` | `test_unified_agent_architecture.py` |
| **Reasoning & Telemetry Transparency** | `scratchpad-reasoning-transparency.md` | `agent-runtime-engineering` | `nee-scratchpad-guard` | `test_governance_rules.py` |
| **Engineering Output & Concise Reporting** | `engineering-output.md` | `nee-governance`, `nee-testing-protocol` | `nee-scratchpad-transparency-guard`, `tools/validate_turn_gates.py` | `test_engineering_output_policy.py` |
| **Model-Neutral Artifact Naming** | `model-neutral-artifact-naming.md` | `nee-governance` | `tools/lint_model_neutral_naming.py` | `test_model_neutral_naming.py`, `test_governance_rules.py` |
| **Production Derivation & Anti-Overfitting** | `production-derivation-and-anti-overfitting.md` | `nee-governance` | `tools/lint_no_transcript_overfitting.py` | `test_lint_no_transcript_overfitting.py` |
| **Production Directory Purity** | `production-purity.md` | `nee-governance` | `tools/lint_production_purity.py` | `test_lint_production_purity.py` |
| **Storage & Concurrency Safety** | `concurrency-and-storage-safety.md` | `skill-architectural-governance` | `tools/lint_ast_invariants.py` | `tests/conftest.py` |
| **Layer Architectural Integrity** | `layer-architectural-integrity.md` | `skill-architectural-governance` | `tools/lint_layer_imports.py` | `test_lint_layer_imports.py` |
| **Figma 4K Governance & Authoring** | `figma-visual-governance.md` | `figma-visual-studio` | `figma-pre-release-guard` | `test_figma_governance_integration.py` |
| **System Learning & Continuous Feedback** | `system-learning-and-feedback.md` | `nee-governance` | `nee-learning-proposal-guard` | `test_system_learning_governance.py` |
| **MCP Integration Engineering** | `contract-enforcement.md` | `mcp-engineering` | `scripts/validate_mcp_design.py` | `tests/test_skill_scripts_end_to_end.py` |
| **Design Stack & Portable Artifacts** | `production-purity.md` | `design-stack-orchestrator` | `scripts/score_routes.py` | `tests/test_skill_scripts_end_to_end.py` |
| **Visual Architecture & Diagramming** | `contract-enforcement.md` | `pretty-mermaid`, `lucidchart-document-workflows`, `miro-board-workflows` | `scripts/check_mermaid.py` | `tests/test_skill_scripts_end_to_end.py` |
| **Provider Resilience & Fault Injection** | `provider-resilience-and-healing.md` | `skill-provider-fault-injector` | `evals.run_provider_fault_injection` | `tests/test_deterministic_healing_mesh.py` |
| **Worktree & Filesystem Hygiene** | `runtime-evidence.md` | `skill-runtime-evidence-and-hygiene-auditor` | `tools/audit_worktree_hygiene.py` | `tests/test_runtime_evidence_claims.py` |
| **Receipt Taxonomies & Unified Prefixes** | `receipt-taxonomies.md` | `nee-governance`, `nee-release` | `tools/record_wave_receipt.py` | `tests/test_governance_rules.py` |
| **Governance Harmonization** | `receipt-taxonomies.md` | `governance-harmonizer` | `tools/audit_governance_harmonization.py` | `tests/test_governance_harmonization.py` |

## Summary Coverage Verification
| AGENTS.md Section | Rules | Skills | Hooks | Git Hooks | Tests |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Ingestion intent persistent | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mutation approval lifecycle | ✅ | ✅ | ✅ | ✅ | ✅ |
| Allowed terminal outcomes | ✅ | ✅ | ✅ | — | ✅ |
| Wave progression execution-evidence-gated | ✅ | ✅ | ✅ | ✅ | ✅ |
| HITL decisions atomic & optionated | ✅ | ✅ | ✅ | ✅ | ✅ |
| Progress bars mandatory | ✅ | ✅ | — | — | ✅ |
| Raw transcript synthesis contract-bound | ✅ | ✅ | ✅ | ✅ | ✅ |
| Runtime evidence-bound | ✅ | ✅ | — | ✅ | ✅ |
| Skills, agents, sub-agents, hooks & rules unified architecture | ✅ | ✅ | ✅ | ✅ | ✅ |
| Master architectural & implementation invariants | ✅ | ✅ | ✅ | ✅ | ✅ |
| System learning & continuous feedback | ✅ | ✅ | ✅ | — | ✅ |
