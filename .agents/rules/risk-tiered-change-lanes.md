---
trigger: model_decision
description: Path-classified 3-lane risk tiering policy (Lane 0/1/2), fail-closed escalation, and batch sizing controls
---
# Risk-Tiered Change Lanes & Batch Sizing Governance Rules

Governs risk-tiered verification ceremony, path classification, fail-closed escalation, and merge batch limits (`config/governance/risk_lanes_policy.json`, `tools/change_lane_classifier.py`).

## 1. Three-Lane Risk Classification Architecture

Verification ceremony is proportional to the blast radius of the change:

- **Lane 0 (Low Risk)**:
  - *Scope*: Documentation (`docs/**`, `documentation/**`, `archive/**`, `*.md` except `AGENTS.md`), and unit/integration tests without pipeline modifications.
  - *Ceremony*: AST syntax check, fast ruff linter, credential firewall, and targeted test execution.
  - *Exemptions*: Exempt from heavy release activation receipts, 11 BGE-M3 gates, and blocking plan/summary formatting gates.
- **Lane 1 (Medium Risk)**:
  - *Scope*: Standard pipeline application code (`dual_pipeline/**`), diagnostic utilities, evaluation scripts, and general engineering tools.
  - *Ceremony*: Standard Tier 1 pre-commit, staged shift-left complexity checks, and deterministic test profile.
  - *Exemptions*: Exempt from 11 BGE-M3 gates and graph mutation release receipts.
- **Lane 2 (High / Critical Risk)**:
  - *Scope*: Knowledge graph storage (`graph/**`, `storage/**`), release activation (`dual_pipeline/release/**`), source ingestion (`dual_pipeline/ingestion/**`), retrieval weights, HITL ledger (`hitl_decision_ledger.json`), runtime evidence controls, and governance policies/gates themselves (`AGENTS.md`, `.agents/**`, `config/governance/**`, `tools/verify_*`, `tools/pr_governance*`).
  - *Ceremony*: Strict 100% enforcement of all 32 readiness gates, 11 BGE-M3 gates, signed DSSE out-of-tree attestations, cryptographic human approval, and granular graph mutation delta tables (`+0` or audited mutation).

## 2. Fail-Closed Security Escalation Invariants

1. **Unrecognized Path Escalation**: Any file path not explicitly mapped in `config/governance/risk_lanes_policy.json` strictly defaults to **Lane 2**.
2. **Mixed Change Escalation**: Any change set touching files across multiple lanes strictly escalates to the highest lane touched. If even one Lane 2 file is modified, the entire change is verified under Lane 2.
3. **Self-Referential Immutability**: The policy file (`config/governance/risk_lanes_policy.json`) and classifier (`tools/change_lane_classifier.py`) are classified as Lane 2 controls. They can never downgrade themselves.

## 3. Operational Sizing & Batch Limits

- **Sizing Soft Cap**: Changes should aim for $\le 400$ non-generated lines of code per merge. Exceeding 400 LOC emits an operational sizing advisory.
- **Worktree WIP Cap**: Active feature worktrees are capped at $\le 30$ concurrent workspaces to minimize merge conflict cross-sections.
- **Single Plan Location**: Governed implementation plans reside exclusively in `docs/plans/<branch-slug>.md`. Committing root `implementation_plan.md` to `main` is strictly prohibited.
