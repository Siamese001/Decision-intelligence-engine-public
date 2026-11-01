---
name: nee-orchestration
description: Task orchestration, multi-agent panels, and forensic RCA. Use for RCA investigations, adversarial debate, and multi-agent workflow decomposition.
---

# NEE Task Orchestration & Forensic Panels

This skill guides multi-agent decomposition, cross-pipeline workflows, and forensic root-cause analysis (RCA).

## When to use

- User says "RCA why", "forensic investigation", "5 adversarial agents", or "adversarial debate"
- Complex tasks requiring cross-cutting execution (ingest $\to$ evaluate $\to$ release $\to$ query)
- Multi-step workflows involving meeting transcript synthesis and knowledge graph updates
- Tasks requiring governance checks or HITL ambiguity gating interspersed with operations

---

## Specialized Sub-Agent Roster

### 1. Ingestion Sub-Agent
- **Responsibility**: Owns directive lifecycle from open to completion.
- **Cheatsheet**: [`nee-ingestion`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-ingestion/SKILL.md)

### 2. Transcript Synthesis Sub-Agent
- **Responsibility**: Processes raw meeting audio/transcripts into canonical graph deltas.
- **Cheatsheet**: [`nee-transcript-synthesis`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-transcript-synthesis/SKILL.md)

### 3. Decision Intelligence Sub-Agent
- **Responsibility**: Solves strategic, evidence-bound decision problems without retrieval rank bias.
- **Cheatsheet**: [`nee-decision-intelligence`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-decision-intelligence/SKILL.md)

### 4. Query Sub-Agent
- **Responsibility**: Owns retrieval, routing, and response synthesis across 8 query routes.
- **Cheatsheet**: [`nee-query`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-query/SKILL.md)

### 5. Retrieval Evaluation Sub-Agent
- **Responsibility**: Executes regression and qualification gates under real BGE-M3.
- **Cheatsheet**: [`nee-release`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-release/SKILL.md)

### 6. Governance & HITL Sub-Agent
- **Responsibility**: Runs audits, verifies conformance, evaluates SSOT margin ambiguity gating (`needs_operator_decision`), and validates approval origins.
- **Capabilities**: Pre-mutation gates, CI gate runner, atomic HITL presentation, `hitl_decision_ledger.json` storage, prior calibration.
- **Cheatsheets**: [`nee-governance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-governance/SKILL.md), [`nee-hitl-governance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-hitl-governance/SKILL.md)

### 7. Release & Maintenance Sub-Agent
- **Responsibility**: Manages graph updates, out-of-band maintenance, and activation receipts.
- **Cheatsheets**: [`nee-release`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-release/SKILL.md), [`nee-graph-maintenance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-graph-maintenance/SKILL.md)

---

## Core Orchestration Patterns

### Pattern 1: Meeting Transcript to Active Graph
```
1. Transcript Synthesis → Run dual_pipeline.transcript_pipeline to produce Artifacts 01, 02, 03
2. Ingestion → Prepare evidence envelope via tools/prepare_evidence_envelope_intake.py
3. Governance → Pre-mutation gate check (python tools/nee_pre_mutation_gate.py)
4. Ingestion → Open directive (nee intake-directive-open), bind operator approval
5. Ingestion → Execute intake stages (U0 through L4)
6. Release → Prepare release (nee release-prepare)
7. Retrieval Eval → Pass 11-gate BGE-M3 evaluation (nee retrieval-release-evaluate)
8. Release → Activate release (nee release-activate) and run E0 audit
9. Ingestion → Terminal completion receipt (nee intake-completion-gate)
10. Query → Verify retrievability of new graph entities
```

### Pattern 2: Strategic Decision Evaluation
```
1. Query → Retrieve current state snapshot for baseline context
2. Decision Intelligence → Construct typed DecisionFrame (options, criteria, constraints)
3. Decision Intelligence → Score options against evidence (isolating retrieval rank)
4. Decision Intelligence → Recommend top-supported option or escalate to HITL (if needs_operator_decision is True)
5. Telemetry → Record run telemetry and learning event in durable ledger
```

### Pattern 3: Out-of-Band Maintenance & Remediation
```
1. Governance → Identify drift, expired assertions, or deprecated sources
2. Maintenance → Run candidate cleanup or prepare combined remediation package
3. Release → Prepare remediation release with bound directive
4. Retrieval Eval → Verify retrieval regression gates pass
5. Release → Activate remediation release
6. Governance → Verify post-remediation graph integrity
```

### Pattern 4: Forensic RCA in Isolated Worktree ("RCA why", "adversarial agents")
```
1. Isolate: Provision dedicated worktree via `python tools/pr_governance.py start feat/rca-<slug>`.
   Read-only investigation — no code modifications during RCA phase.
2. Scope: Extract topic, boundaries, and agent count (default: 5 detectives).
3. Spawn Panel:
   - Detective 1: Ingress & Parameter Flow (CLI parsing, metadata construction)
   - Detective 2: Gate & Guard Integrity (HITL gates, attendee gates, C0 gates)
   - Detective 3: Data Flow & Transformation (Stage pipelines, transcript processing)
   - Detective 4: State & Concurrency (SQLite locks, file I/O, race conditions)
   - Detective 5: Release & Governance Lifecycle (Approval flow, receipts, merge gates)
4. Findings: Each detective reports code under examination, evidence chain, reproduction steps, root-cause contribution.
5. Synthesis: Merge findings, build collapse chain, identify consensus root cause.
6. Emit Report: Output structured RCA report with proposed fix (plan-only).
7. Transition Gate: Present options via `ask_user_question`:
   - Option A: Implement fix in this worktree (requires approved wave plan)
   - Option B: Close investigation and archive findings
   - Option C: Escalate with additional detectives / broader scope
```

---

## Cross-Cutting Rules

- **Approval Before Mutation**: Never execute graph-changing writes without bound human approval.
- **Wave Ledger Tracking**: For multi-turn wave executions, record wave status and receipts in durable internal files (`reports/`). User-facing turn completions follow [.agents/rules/engineering-output.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md) without printing all-wave status tables in chat.
- **Engineering Output Reporting**: Turn completion reports conform strictly to [.agents/rules/engineering-output.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md) (120 words target, 300 words max, observed effects required).
- **HITL Ambiguity Gating**: Only surface human prompts when $\Delta = c_{\text{top}} - c_{\text{second}} \le 20\%$ inclusive. If $\Delta > 20\%$, proceed autonomously.
- **Testing Protocol**: Enforce Class A (debug, single-test, `--timeout=60`) vs Class B (receipt, wave-scoped) test executions.

---

## Related Skills

- [`nee-governance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-governance/SKILL.md)
- [`nee-ingestion`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-ingestion/SKILL.md)
- [`nee-query`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-query/SKILL.md)
- [`nee-release`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-release/SKILL.md)
- [`nee-telemetry`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-telemetry/SKILL.md)
- [`nee-decision-intelligence`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-decision-intelligence/SKILL.md)
- [`nee-transcript-synthesis`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-transcript-synthesis/SKILL.md)
- [`nee-hitl-governance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-hitl-governance/SKILL.md)
- [`nee-graph-maintenance`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-graph-maintenance/SKILL.md)
- [`nee-testing-protocol`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/skills/nee-testing-protocol/SKILL.md)
