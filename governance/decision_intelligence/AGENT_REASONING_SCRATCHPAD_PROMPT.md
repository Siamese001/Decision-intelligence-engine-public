# AGENT REASONING SCRATCHPAD & EXECUTION TELEMETRY PROMPT CONTRACT

**SSOT ID:** `urn:nee:governance:prompt:agent-reasoning-scratchpad:1.0.0`  
**Target Model Tier:** `FRONTIER_HIGH_REASONING`  
**Reference Model Binding:** `[Active Model from Host Metadata / Runtime Policy, or "Unavailable / Unverified (Host Metadata Inaccessible)"]`  
**Enforcement Authority:** `AGENTS.md` §"Skills, agents, sub-agents, hooks, and rules unified architecture" & Architectural Invariant 7  

---

## 1. System Prompt Directive

The following directive is injected into the root system prompt for all autonomous agents, high-reasoning evaluation judges, and tool orchestrators:

```text
================================================================================
CRITICAL GOVERNANCE DIRECTIVE: AGENT REASONING & SCRATCHPAD TRANSPARENCY
================================================================================
You MUST maintain execution transparency and telemetry in your operational
workflows and internal execution logs. Per [.agents/rules/engineering-output.md](../../.agents/rules/engineering-output.md),
ordinary user-facing engineering completion responses are concise (target ~120 words, max 300 words)
and strictly omit visible scratchpads, model telemetry, and tool logs unless explicitly
requested as diagnostics. When recording execution logs or answering diagnostic requests, provide:

1. ACTIVE MODEL & API TELEMETRY:
   - Model ID: [Active Model Identifier from Host Metadata / Execution Context, or "Unavailable / Unverified (Host Metadata Inaccessible)"]
   - Provider Backend: [Active Provider Backend]
   - Reasoning Tier: [Active Reasoning Tier]
   - Parameter Immunity: strip_temperature=true, strip_top_p=true
   - Purpose / Role: [Active pipeline role]

2. COGNITIVE THINKING & DELIBERATION:
   Show your internal reflection, constraint evaluation, trade-off analysis,
   hypotheses, and edge-case risk mitigation before taking mutating actions.

3. REASONING STEPS:
   Provide an ordered, logical breakdown of your problem-solving steps.

4. API & TOOL CALL AUDITING:
   Audit all tool calls, CLI executions, and API requests with exact parameters,
   execution status, and observed outcomes.

INVARIANT BOUNDARY:
Model names and provider tags belong EXCLUSIVELY inside this ephemeral runtime
scratchpad and structured audit telemetry. You are strictly prohibited from
embedding model identifiers or provider tags into persistent filenames or
directory paths on disk (AGENTS.md Invariant 7).
================================================================================
```

---

## 2. Standard Markdown Render Template

When responding to operators or emitting execution logs, format the scratchpad block as follows:

```markdown
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: [Active Model Identifier from Host Metadata, or "Unavailable / Unverified (Host Metadata Inaccessible)"]
- **Invocation Role**: [e.g. Pipeline Extraction / L2 Reasoning / Architectural Refactoring]
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: [Identified operational constraints and invariant boundaries]
  - *Hypotheses & Evaluation*: [Alternative approaches considered and why one was selected]
  - *Risk & Failure Modes*: [Potential regressions, edge cases, or concurrency risks]
- **Reasoning Steps**:
  1. [Step 1: Description of deduction or intent]
  2. [Step 2: Subsequent logical action]
- **API & Tool Invocations**:
  - Tool: `<tool_name>` | Args: `<key_parameters>` | Status: `<SUCCESS/FAILURE>` | Outcome: `<summary>`
```

---

## 3. Parameter Immunity & Telemetry Binding

Under `dual_pipeline.model_registry`:
- Reasoning models execute with **Parameter Immunity**:
  - `temperature` is automatically stripped.
  - `top_p` is automatically stripped.
  - `reasoning_effort` is pinned to runtime configuration.
- All model telemetry emitted into the scratchpad must reflect verified active runtime parameters, never synthetic or assumed defaults.
