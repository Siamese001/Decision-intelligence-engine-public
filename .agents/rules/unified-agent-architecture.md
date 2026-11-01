---
trigger: model_decision
---
# Unified Agent Architecture: Skills, Agents, Sub-Agents, Hooks & Rules

Governs how skills, agents, sub-agents, hooks, and rules intersect, as mandated by `AGENTS.md`.

## 1. The Five Core Architectural Pillars
- **Skills ("What work gets done")**: Reusable, atomic or composite capabilities registered in `.agents/skills/`. Stateless or stateful. Model & tool agnostic, discoverable, and composable.
- **Agents ("Who decides & orchestrates")**: Goal-driven coordinators. Own overall strategy & control, maintain context & state across turns, and delegate to specialized sub-agents.
- **Sub-Agents ("Specialized executors")**: Targeted executors focused on bounded domains. Lightweight autonomy within delegated scope; report structured results upstream to parent agent (never mutate global state unilaterally).
- **Hooks ("When to extend")**: Event-driven lifecycle interceptors executing at deterministic points (`pre_tool_call`, `post_tool_call`, `around_action`). Address cross-cutting concerns non-intrusively.
- **Rules ("What must be true")**: Immutable governance constraints, pre/post-conditions, and compliance/safety boundaries.

## 2. SHARED FOUNDATIONS
1. **Memory**: Internal execution ledgers, persistent ledgers (`hitl_decision_ledger.json`), and entity graphs.
2. **Knowledge Bases & Data Sources**: SQLite graph databases, vector sidecars, contract inventory schemas.
3. **Models & Tools**: Foundation models, approved provider APIs, and deterministic CLI utilities (`nee` CLI).
4. **Observability**: Distributed OpenTelemetry event envelopes, structured audit logs, gate progress indicators, and test metrics.

## 3. Delegation Contract Protocol
When an Agent delegates work to a Sub-Agent, it MUST formulate an explicit delegation payload:
- **Required Fields**: `task_id`, `parent_task_id`, `objective`, `scope_in`, `scope_out`, `allowed_tools`, `deliverable`, `definition_of_done`, `budget`, `escalation_rule`.
- **Upstream Return Protocol**: Sub-agents return `task_id`, `status` (`COMPLETE`, `BLOCKED`, `ESCALATED`, `FAILED`), `deliverable`, `evidence_refs`, and `metrics`.

## 4. End-to-End Execution Flow (7-Step Lifecycle)
1. **Step 1 — Goal & Rule Verification**: Evaluate active rules, policies, and classifications.
2. **Step 2 — Planning & Skill Selection**: Decompose goal and select required skills.
3. **Step 3 — Sub-Agent Delegation**: Delegate bounded subtasks under Delegation Contract Protocol.
4. **Step 4 — Skill Execution**: Execute domain tasks and produce deliverables.
5. **Step 5 — Hook Triggers**: Intercept lifecycle points for telemetry, caching, and guardrails.
6. **Step 6 — Continuous Rule Validation**: Validate inputs, outputs, and side effects.
7. **Step 7 — Upstream Synthesis & Sub-Agent Teardown**: Reconcile, synthesize, and return final verified response adhering to [.agents/rules/engineering-output.md](engineering-output.md) for engineering turns, while executing the Sub-Agent Teardown Contract (actively reaping and terminating all spawned sub-agents via `manage_subagents` `kill_all` to prevent orphaned execution nodes and zombie background processes).

## 5. Architectural Invariants
- Skills are the building blocks; Agents orchestrate and decide; Sub-Agents specialize and scale; Hooks extend behavior; Rules govern.
- Single Pipeline SSOT: The platform operates as a single Unified Agentic Pipeline (`U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS`). "Dual-Pipeline" terminology is formally retired from active architectural taxonomy; the package `unified_pipeline` is the canonical SSOT namespace, with `dual_pipeline` retained exclusively as a legacy backward-compatibility alias.
