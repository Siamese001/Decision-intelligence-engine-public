---
name: nee-governance
description: NEE governance, PR reviews, worktrees, and release lifecycle. Use for PR queue, worktree branches, 32-gate readiness, and pre-mutation audits.
---

# NEE Governance & PR Operations

This skill guides governance, PR lifecycle, worktree management, and audit operations.

## When to use

- User says "PR to local main", "pr", "pr2", or "commit and PR"
- User requests "new worktree branch" or asks to start development in an isolated worktree
- User asks for "PR queue", "branch summary", "branch status", or "what branches do I have"
- User asks to audit, validate, or check governance compliance
- Before any graph mutation (pre-mutation gate)
- When evaluating HITL decision ambiguity (SSOT margin threshold via `needs_operator_decision`)
- When validating operator approval origin (anti-synthetic approval check)
- When reviewing release lifecycle integrity or 32-gate readiness
- When running CI/CD governance gates or pytest protocols

---

## 1. Worktree Provisioning ("new worktree branch")

When starting any new feature, investigation, or bugfix:

```bash
python tools/pr_governance.py start feat/<slug>
```

When ingesting documents or meetings, use the dedicated `ingest/` namespace:
- **Meeting Transcripts (Outputs 1–3)**: `python tools/pr_governance.py start ingest/meeting-<project>-<slug>`
- **Written Documents & Memos (Output 3 only)**: `python tools/pr_governance.py start ingest/doc-<project>-<slug>`
- **Tabular Data & Spreadsheets (Output 3 only)**: `python tools/pr_governance.py start ingest/sheet-<project>-<slug>`
- **Org Charts & Visual Diagrams (Output 3 only)**: `python tools/pr_governance.py start ingest/chart-<project>-<slug>`

Creates: `.worktrees/<slug>/` on branch `feat/<slug>` or `ingest/<modality>-<project>-<slug>`. All development occurs in worktrees. Direct commits to `main` are blocked.

---

## 2. Governed PR & Merge Lifecycle ("PR to local main")

When the user says **"PR to local main"**, **"pr"**, **"pr2"**, or **"commit and PR"**, execute the governed two-stage sequence:

### Stage A: Branch Alignment & Post-Rebase Readiness
1. Check `git status --porcelain` for modified and untracked files.
2. Present a summary of changes for confirmation.
3. Stage task-relevant files **explicitly**: `git -C <worktree> add <specific_paths...>`  
   *(Strict prohibition: NEVER use `git add -A` or `git add .` to avoid dirty-dumping logs or ephemeral receipts).*
4. Commit with Conventional Commit message: `git -C <worktree> commit -m "<type>(<scope>): <msg>"`
5. Synchronize with `main` (serialized rebase + post-rebase readiness qualification):
```bash
python tools/pr_governance.py sync <branch>
```
*(Executes serialized rebase onto latest `main` under lock, confirms linear history, and qualifies readiness without dirtying git tracking).*

### Stage B: Governed PR Review & Merge (32 Gates, Proportional Risk Lanes)
6. Pre-Merge Review Audit:
```bash
python tools/pr_governance.py review <branch>
```
Audits mainline alignment, diff syntax, and qualifies proportional readiness suite (Lane 0: 6 gates, Lane 1: 25 gates, Lane 2: 32 gates; <5s when cached).
- Skip if receipt exists: `python tools/pr_governance.py review <branch> --skip-readiness-run`
- Re-check failed only: `python tools/pr_governance.py review <branch> --recheck-failed-only`
- Fast fail: `python tools/pr_governance.py review <branch> --fail-fast`
- Force full rerun: `python tools/pr_governance.py review <branch> --full`

7. Governed Merge:
```bash
python tools/pr_governance.py merge <branch> --title "<title>"
```
Performs a `--no-ff` merge commit with the `GOVERNED_PR_MERGE` authorization token.

8. Cleanup & Teardown:
```bash
python tools/pr_governance.py finish <branch>
```
Removes `.worktrees/<slug>/` and deletes the feature branch.

9. Confirm:
```bash
git log --oneline -3 main
git worktree list
```

---

## 3. PR Queue & Branch Summary ("PR queue", "branch summary")

When requested as an interactive or scheduled observer run, produce the versioned 10-column table-only contract in `automations/prompts/pr_queue_observer_agent.md` and `.agents/rules/pr-queue-summary.md`. Legacy `pr_governance.py list` views keep their documented caller-selected format.

**Mandatory Turn-Level Live Execution (Anti-Stale Cache Guard)**:
You MUST execute these live queries in the exact turn the table is requested. Reusing cached projection data is strictly prohibited and fails the Truth Invariant.

```bash
python tools/pr_governance.py list
python tools/pr_governance.py list --view decision
python tools/pr_governance.py worktrees
python tools/pr_topology_auditor.py
```

**Decision Contract & Presentation Invariants**:
1. **Primary Decision View**: Preserves Priority, Branch & Domain, Next Action, Why This Position (Ranking Rationale), Blocker & Eligibility, and Value Statement. Either compact view (7-column table + Diagnostic Ledger) or dedicated decision view (`--view decision`) may be used.
2. **Conversational Summary Protection**: Assistants are strictly prohibited from silently dropping value statements, blockers, or next action recommendations in chat prose.
3. **Linked Detail Artifact**: All detail links and collapsed stashes must reference an on-disk detail artifact (`reports/pr_queue_detail.json`) preserving complete wave progress, review verdicts, and stable git stash commit hashes.
4. **Accurate Coverage Footer**: Worktree children and git stashes must be reported with accurate numerator and denominator measurements (`Worktree coverage: N/N children · M/M stashes`).
5. **Dirty Gate Invariant**: Branches with `ahead==0` that hold uncommitted authored work or active stashes MUST receive `[COMMIT_FIRST]` from `compute_dispositions()`. Never recommend `[PRUNE]` on dirty worktrees. Branches with open waves receive `[KEEP]`. All Next Action recommendations must come directly from the tool.

---

## 4. Pre-Mutation Gate

Before any graph mutation, the pre-mutation gate must pass:

```bash
python tools/nee_pre_mutation_gate.py
```

Enforces "approval before mutation" from `PROJECT_INSTRUCTIONS.md` §2.

---

## 5. HITL Governance & Ambiguity Gating

Governed canonically by [AGENTS.md](file:///Users/amitayer/Git/decision-intelligence-engine/AGENTS.md) §1 and [.agents/rules/hitl-decisions.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/hitl-decisions.md):
1. **Interactive Ambiguity Gating**: If `needs_operator_decision(c_top, c_second)` is True ($\Delta \le 20\%$ inclusive): Genuine ambiguity exists $\to$ must surface to operator via `ask_user_question`. When $\Delta > 20\%$, autonomous resolution is permitted.
2. **Autonomous Progression**: If $\Delta > 30\%$: Decisively resolved $\to$ proceed autonomously with top option under audit receipt.
3. **Authentic Approval Origin**: Approvals must originate from explicit human messages. Synthetic approvals are rejected.

---

## 6. Engineering Output Reporting Contract

Turn completion reports for engineering tasks must strictly conform to [.agents/rules/engineering-output.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md):
- **Concise Budget**: 120 words target, 300 words maximum (`INV-OUTPUT-MAX-WORDS`), 3,500 characters maximum (`INV-OUTPUT-MAX-CHARS`).
- **Observed Behavior**: Lead with action, input, and observed effect; state environment (local/sandbox/production) and remaining limitations.
- **No Process Inventory**: Omit visible scratchpads, model telemetry, tool logs, and all-wave tables from chat; record operational receipts in durable internal files (`reports/`).

---

## 7. CI/CD Governance Gates

### Local (Pre-commit)
```bash
python -B -m dual_pipeline.runtime_evidence enforce-controls --repository-root .
python -B tools/validate_no_synthetic_embeddings.py --repository-root .
python -B -m unittest tests.test_runtime_evidence_claims
python -B -m unittest tests.test_ingestion_directive_invariants
python -B -m unittest tests.test_graph_model_integrity
python -B -m unittest tests.test_node_type_policy
```

### CI Gate Runner
```bash
python tools/run_ci_gates.py --profile deterministic
python tools/run_ci_gates.py --profile bge
```

---

## 8. Sidecar Automation Agents & Full Lifecycle Enforcement

Whenever the operator requests running any of the 15 registered core sidecar automation agents (or the 2 Stage VI executive briefing sidecars), you **MUST** first read the registered sidecar definition in `/Users/amitayer/.gemini/config/sidecars/<sidecar-dir>/sidecar.json`, dereference its canonical specification in `automations/prompts/*_agent.md`, and execute the full interactive lifecycle (`Step 0.1` isolated worktree creation via `tools/pr_governance.py start` -> pre-flight -> discovery -> validated wave plan at `docs/plans/<branch-slug>.md` -> atomic HITL via `ask_user_question` -> branch commit -> `tools/pr_governance.py rebase` -> governed PR merge via `tools/pr_governance.py submit` / `tools/pr_governance.py merge` into local `main`):
1. **Graph Hygiene Agent** (*Knowledge Graph Hygiene Agent*): `/Users/amitayer/.gemini/config/sidecars/knowledge-graph-hygiene-agent/sidecar.json` -> [`automations/prompts/graph_hygiene_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/graph_hygiene_agent.md)
2. **Storage Hygiene Agent** (*Storage & Repository Clutter Hygiene Agent*): `/Users/amitayer/.gemini/config/sidecars/autonomous-storage-repository-clutter-hygiene-protocol/sidecar.json` -> [`automations/prompts/storage_hygiene_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/storage_hygiene_agent.md)
3. **SSOT Remediation Agent** (*Codebase Architecture, Debt & SSOT Governance Agent*): `/Users/amitayer/.gemini/config/sidecars/ssot-configuration-deduplication-model-pinning-anti-hardcoding/sidecar.json` -> [`automations/prompts/ssot_drift_remediation_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/ssot_drift_remediation_agent.md)
4. **Pipeline Assurance Agent** (*Pipeline Outcome, Retrieval, Cost & Watchdog Assurance Agent*): `/Users/amitayer/.gemini/config/sidecars/pipeline-outcome-retrieval-assurance/sidecar.json` -> [`automations/prompts/pipeline_outcome_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/pipeline_outcome_assurance_agent.md)
5. **Testing Gaps Agent** (*Repository Testing Gaps & Verification Architecture Assurance Agent*): `/Users/amitayer/.gemini/config/sidecars/testing-gaps-assurance-agent/sidecar.json` -> [`automations/prompts/testing_gaps_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/testing_gaps_assurance_agent.md)
6. **Functionality Assurance Agent** (*All-Encompassing End-to-End Critical Functionality Assurance Agent*): `/Users/amitayer/.gemini/config/sidecars/e2e-critical-functionality-assurance-agent/sidecar.json` -> [`automations/prompts/e2e_critical_functionality_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/e2e_critical_functionality_assurance_agent.md)
7. **PR Queue Agent** (*Antigravity PR Queue Observer Agent*): `/Users/amitayer/.gemini/config/sidecars/pr-queue-governance-observer/sidecar.json` -> [`automations/prompts/pr_queue_observer_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/pr_queue_observer_agent.md)
8. **Energy Intelligence Agent** (*Weekly Energy & Utilities Market Intelligence Agent*): `/Users/amitayer/.gemini/config/sidecars/energy-utilities-best-practices/sidecar.json` -> [`automations/prompts/weekly_market_intelligence_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/weekly_market_intelligence_agent.md)
9. **Corner Foresight Agent** (*Look-Around-the-Corner Foresight & Blind-Spot Radar Scout*): `/Users/amitayer/.gemini/config/sidecars/corner-foresight-blindspot-scout/sidecar.json` -> [`automations/prompts/corner_foresight_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/corner_foresight_agent.md)
10. **Hotspot Enhancement Agent** (*High-Value Pipeline & Repository Hotspot Enhancement Agent*): `/Users/amitayer/.gemini/config/sidecars/hotspot-value-enhancement-agent/sidecar.json` -> [`automations/prompts/hotspot_enhancement_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/hotspot_enhancement_agent.md)
11. **Cost Optimization Agent** (*8-Pillar Repository Cost Optimization & Efficiency Auditor*): `/Users/amitayer/.gemini/config/sidecars/tokenomics-cost-optimization-agent/sidecar.json` -> [`automations/prompts/tokenomics_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/tokenomics_agent.md)
12. **Agentic Frontier Architecture Agent** (*Unified Frontier LLM Intelligence, Research & Agentic Repository Architecture Agent*, absorbs `frontier_llm_research_scout`): `/Users/amitayer/.gemini/config/sidecars/agentic-repo-best-practices-scout/sidecar.json` -> [`automations/prompts/agentic_repo_best_practices_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/agentic_repo_best_practices_agent.md)
13. **Retrieval Surface Agent** (*Exhaustive 7-Surface Retrieval Functionality Gap & High-ROI Enhancement Agent*): `/Users/amitayer/.gemini/config/sidecars/retrieval-surface-assurance-agent/sidecar.json` -> [`automations/prompts/retrieval_surface_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/retrieval_surface_agent.md)
14. **System Learning Agent** (*Exhaustive 6-Surface Closed-Loop System Learning Gap Remediation & High-ROI Enhancement Agent*): `/Users/amitayer/.gemini/config/sidecars/system-learning-assurance-agent/sidecar.json` -> [`automations/prompts/system_learning_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/system_learning_agent.md)
15. **Evals Surface Agent** (*Exhaustive 7-Surface Evaluation Construct-Validity & Anti-Tautology Remediation Agent*): `/Users/amitayer/.gemini/config/sidecars/evals-surface-assurance-agent/sidecar.json` -> [`automations/prompts/evals_surface_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/evals_surface_agent.md)

---

## 9. Related Files

- Engineering output policy: [`.agents/rules/engineering-output.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md)
- PR governance: [`tools/pr_governance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/pr_governance.py)
- Main readiness: [`tools/verify_main_readiness.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/verify_main_readiness.py)
- Pre-mutation gate: [`tools/nee_pre_mutation_gate.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/nee_pre_mutation_gate.py)
- HITL governance: [`dual_pipeline/hitl_governance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance.py)
- CI gates: [`tools/run_ci_gates.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/run_ci_gates.py)
- Runtime evidence: [`dual_pipeline/runtime_evidence.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/runtime_evidence.py)
