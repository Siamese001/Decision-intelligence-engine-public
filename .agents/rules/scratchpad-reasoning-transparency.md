---
trigger: model_decision
---
# Agent Reasoning, Scratchpad & Telemetry Transparency Rules

Governs execution visibility, reasoning transparency, cognitive deliberation, tool auditing, and LLM telemetry. Grounded in `AGENTS.md` and Architectural Invariant 7.

## Canonical Engineering Output Governance & Direct-Answer Exceptions

1. **Engineering Turns Governance**: User-facing responses for engineering turns (implementation, refactoring, debugging, wave execution) are canonically governed by [.agents/rules/engineering-output.md](engineering-output.md). Ordinary engineering completions MUST be concise (target ~120 words, max 300 words) and strictly omit visible scratchpads, model telemetry, tool call audits, and all-wave status tables.
2. **Copilot & Knowledge Queries**: For `nee copilot` and user-facing NEE knowledge queries, the response must begin with the direct answer. Do not print scratchpads, model telemetry, or tool logs. Copilot emits only one fenced block of grounded, speakable comments. Knowledge queries use normal prose with specific evidence citations.
3. **Internal Records & Requested Diagnostics**: Operational telemetry, execution history, and the Four Pillars of execution reflection are preserved in internal audit files (`reports/`, ledgers). A visible scratchpad or diagnostic execution summary is emitted ONLY when the operator explicitly requests diagnostic or telemetry details.

## 1. The Four Pillars of Internal Execution Reflection
For non-trivial tasks, autonomous agents maintain internal execution tracking covering:
1. **Cognitive Breakdown & Thinking Deliberation**: Operational context, constraints, trade-offs, evaluated hypotheses, and risk mitigation.
2. **Reasoning Steps**: Ordered, logical breakdown of deductions, task decomposition, and verification criteria.
3. **API & Tool Invocations**: Audit of tools invoked with key parameters, execution status (`SUCCESS`, `FAILURE`, `BLOCKED`), and concise outcome summaries.
4. **LLM & Model Telemetry**: Active model identifier, provider backend, reasoning budget/tier, and invocation role.

---

## 2. Standard Scratchpad Format
```markdown
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: [Active Model Identifier from Host Metadata, or "Unavailable / Unverified (Host Metadata Inaccessible)"]
- **Invocation Role**: [e.g. Code Refactoring / L2 Query Reasoning / Architectural Audit]
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: [Operational constraints and invariant boundaries]
  - *Hypotheses & Evaluation*: [Alternative approaches considered and selected rationale]
  - *Risk & Failure Modes*: [Potential regressions, edge cases, or concurrency risks]
- **Reasoning Steps**:
  1. [Logical deduction or intent]
  2. [Subsequent action]
- **API & Tool Invocations**:
  - Tool: `<tool_name>` | Args: `<params>` | Status: `<SUCCESS/FAILURE>` | Outcome: `<summary>`
```

---

## 3. Universal System Prompt Directive (Prompt to Implement)
```text
CRITICAL GOVERNANCE DIRECTIVE: AGENT REASONING & SCRATCHPAD TRANSPARENCY
1. ACTIVE MODEL & API TELEMETRY: Model identifier, provider, reasoning tier, and role.
2. COGNITIVE THINKING & DELIBERATION: Reflection, constraints, hypotheses, risks before action.
3. REASONING STEPS: Ordered, logical breakdown of problem-solving steps.
4. API & TOOL CALL AUDITING: Tool calls with parameters, status, and observed outcomes.
INVARIANT BOUNDARY: Model identifiers belong exclusively in runtime telemetry; never in persistent filenames.
```

---

## 4. Architectural Boundaries & Cross-Cutting Invariants
1. **Persistent Filename Neutrality (AGENTS.md Invariant 7)**: Telemetry is ephemeral; strictly prohibited from persistent filenames or directory paths on disk.
2. **Credential & Privacy Isolation (Invariant 4 & Rule C-19)**: Never log secrets, bearer tokens, or confidential career strategy (`INTERNAL_ADVISORY`).
3. **Provider-Free Boundaries**: Deterministic stages (L1, identity resolution, routing, graph writes) declare `Provider: None (Provider-Free)`.
4. **Silent Event Exception (Route 0c)**: Silent session events (speaker attribution hints, background acoustic telemetry) are strictly exempt from user-visible scratchpad emission, conversational replies, and inline telemetry rendering to preserve chat signal during active meetings.
