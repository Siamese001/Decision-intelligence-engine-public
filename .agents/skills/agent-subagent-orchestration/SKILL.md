---
name: agent-subagent-orchestration
description: Unified Architecture orchestration patterns across Skills, Agents, Sub-Agents, Hooks, and Rules. Covers goal decomposition, contract delegation, state memory, and 7-step lifecycle validation.
---

# Agent & Sub-Agent Orchestration Architecture

This skill provides practical guidance and operational patterns for coordinating
workflows under the **Unified Architecture for Skills, Agents, Sub-Agents, Hooks,
and Rules** (`AGENTS.md` and `.agents/rules/unified-agent-architecture.md`).

## 1. When to Use This Skill

- Orchestrating complex, multi-stage workflows across specialized capabilities.
- Decomposing an open-ended goal into bounded subtasks with isolated contexts.
- Formulating binding delegation contracts when spawning or assigning sub-agents.
- Registering non-intrusive event hooks for logging, metrics, caching, or moderation.
- Enforcing governance rules, pre-conditions, and post-conditions before and after execution.

---

## 2. Architecture & Lifecycle SSOT

The conceptual foundation and execution lifecycle are canonically defined in:
- [`.agents/rules/unified-agent-architecture.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/unified-agent-architecture.md) (The Five Pillars, Shared Foundations, 10-field Delegation Protocol, 7-Step Lifecycle, and Sub-Agent Teardown Contract)
- [`AGENTS.md`](file:///Users/amitayer/Git/decision-intelligence-engine/AGENTS.md)

This skill provides practical delegation templates, return schemas, and hook interception code patterns.

---

## 4. Sub-Agent Delegation Contract Template

When delegating work to a sub-agent, use this canonical JSON structure:

```json
{
  "task_id": "task_sub_extract_001",
  "parent_task_id": "task_root_orchestrator_001",
  "objective": "Extract and normalize entities from meeting transcript section 04:00 - 12:00",
  "scope_in": [
    "output/project_nexus/09-10-2026/01_hitl_resolved_transcript.md"
  ],
  "scope_out": [
    "No external network calls",
    "No direct graph mutations"
  ],
  "allowed_tools": [
    "view_file",
    "replace_file_content",
    "run_command"
  ],
  "deliverable": {
    "schema": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
    "format": "JSON",
    "target_path": "output/project_nexus/09-10-2026/03_canonical_signal_projection.json"
  },
  "definition_of_done": "All candidate entities validate against W2 Semantic Spine allowlist without schema errors.",
  "budget": {
    "max_turns": 5,
    "max_tool_calls": 12,
    "timeout_seconds": 180
  },
  "escalation_rule": "Escalate to parent if confidence margin between top two entity merge candidates satisfies needs_operator_decision (<= 20% inclusive)."
}
```

### Upstream Return Template

Sub-agents must report back to the parent agent using this structure:

```json
{
  "task_id": "task_sub_extract_001",
  "status": "COMPLETE",
  "deliverable": {
    "extracted_entities_count": 8,
    "normalized_nodes": ["person:marcus_balk", "org:partner_advisory"]
  },
  "evidence_refs": [
    "01_hitl_resolved_transcript.md#L45-L68"
  ],
  "metrics": {
    "tool_calls_used": 4,
    "elapsed_seconds": 18.2,
    "remaining_turns": 4
  }
}
```

---

## 5. Non-Intrusive Hook Extension Protocol

Hooks intercept execution around actions without altering core logic:

```python
# Conceptual Pre/Post/Around Hook Interception
def execute_with_hooks(action, context):
    # 1. Trigger PRE Hooks (guardrails, cache checks, authentication)
    trigger_hooks(timing="PRE", context=context)
    
    # 2. Execute Core Logic
    result = action(context)
    
    # 3. Trigger POST Hooks (metrics, logging, notifications, audit events)
    trigger_hooks(timing="POST", context=context, result=result)
    
    return result
```

- **Pre-Hooks**: Abort or block execution if preconditions or safety checks fail.
- **Post-Hooks**: Capture telemetry, update caches, and log audit receipts.
- **Around-Hooks**: Measure end-to-end latency, enforce timeouts, and record resource consumption.
