"""tests/test_antigravity_task_procedures.py

Requirement preservation tests for Antigravity dual-mode task procedures.
Verifies all 14 task/mode combinations across the 7 restored prompts:
1. Knowledge graph hygiene (scheduled discovery/preview, interactive atomic wave repair)
2. Storage repository hygiene (scheduled read-only inventory, interactive G0-G7 category flow)
3. Weekly market intelligence (scheduled active intent approval persistence, interactive continuation)
4. Worktree coverage cleanup (scheduled deliverable protection, interactive confirm-token flow)
5. Repository antipattern audit (scheduled 4-tool discovery & scorecard, interactive wave selection)
6. SSOT drift remediation (scheduled bounded scan, interactive 3-phase refactor worktree)
7. PR queue observer (scheduled quiet deduplication, interactive on-demand observation-only explanation)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def requirements_doc() -> dict[str, Any]:
    req_file = ROOT / "docs" / "plans" / "antigravity_task_requirements.json"
    assert req_file.is_file(), "Requirements inventory JSON must exist"
    return json.loads(req_file.read_text(encoding="utf-8"))


@pytest.fixture
def task_registry() -> dict[str, Any]:
    reg_file = ROOT / "config" / "scheduled_tasks" / "task_registry.json"
    assert reg_file.is_file(), "Task registry JSON must exist"
    return json.loads(reg_file.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# Test 1: Full Inventory Requirement Verification Token Coverage
# -----------------------------------------------------------------------------

def test_all_requirements_tokens_present_in_prompts(
    requirements_doc: dict[str, Any]
) -> None:
    """Verifies that every mandatory verification token across all 58 requirements is present."""
    tasks = requirements_doc.get("tasks", {})
    missing_tokens: list[str] = []

    for tid, tinfo in tasks.items():
        prompt_rel = tinfo["canonical_prompt_path"]
        prompt_file = ROOT / prompt_rel
        assert prompt_file.is_file(), f"Prompt file not found: {prompt_rel}"
        prompt_text = prompt_file.read_text(encoding="utf-8")

        for req in tinfo.get("requirements", []):
            rid = req["id"]
            for token in req.get("mandatory_verification_tokens", []):
                if token not in prompt_text:
                    missing_tokens.append(f"{tid}::{rid} missing token: '{token}'")

    assert len(missing_tokens) == 0, "Missing tokens in prompts:\n" + "\n".join(missing_tokens)


# -----------------------------------------------------------------------------
# Test 2: Storage Hygiene G0–G7 Category Flow & Mutation Safeguards
# -----------------------------------------------------------------------------

def test_storage_hygiene_g0_g7_category_flow() -> None:
    """Verifies storage hygiene prompt contains complete G0-G4, G6-G7 atomic gates and safeguards."""
    prompt = (ROOT / "automations" / "prompts" / "storage_hygiene_agent.md").read_text(encoding="utf-8")

    # Verify G0 through G4 and G6 through G7 gates (G5 / Scope C moved to pr_queue_observer_agent.md)
    for g in ("G0", "G1", "G2", "G3", "G4", "G6", "G7"):
        assert g in prompt, f"Storage hygiene prompt missing atomic gate {g}"

    # Verify target scopes A1, A2, B1, B2
    for scope in ("Scope A1", "Scope A2", "Scope B1", "Scope B2"):
        assert scope in prompt, f"Storage hygiene prompt missing scope '{scope}'"

    # Verify filtered plan and pre-apply drift re-check
    assert "Filtered plan" in prompt
    assert "Pre-apply drift and safety re-check" in prompt
    assert "DRIFT_DETECTED" in prompt

    # Verify non-destructive quarantine and rollback
    assert ".quarantine/" in prompt
    assert "quarantine receipt" in prompt
    assert "Rollback procedure" in prompt

    # Verify zero-graph-mutation proof
    assert "Zero-graph-mutation proof" in prompt
    assert "active_pair.json" in prompt

    # Verify autonomous 20% SSOT margin bypass for reversible quarantine gates (G0-G4, G6-G7)
    # and absence of hardcoded static narrow spreads that force redundant prompts
    assert "AUTONOMOUS" in prompt
    assert "needs_operator_decision" in prompt
    assert "calibrate_options" in prompt
    assert "[Confidence: MEDIUM (75%)]" not in prompt
    assert "[Confidence: MEDIUM (80%)]" not in prompt



# -----------------------------------------------------------------------------
# Test 3: PR Queue Observer Dual Modes & Observation-Only Authority
# -----------------------------------------------------------------------------

def test_pr_queue_observer_dual_modes_and_registry(
    task_registry: dict[str, Any]
) -> None:
    """Verifies PR observer prompt and registry support both scheduled and interactive modes."""
    # 1. Check registry allowed_modes includes interactive
    obs_reg = task_registry["tasks"]["pr_queue_observer"]
    assert "scheduled" in obs_reg["allowed_modes"]
    assert "interactive" in obs_reg["allowed_modes"]

    # 2. Check prompt content
    prompt = (ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md").read_text(encoding="utf-8")
    assert "Strict Observation-Only Authority" in prompt
    assert "Zero Mutation Guarantee" in prompt
    assert "forbidden from creating feature/repair branches" in prompt
    assert "Single-Run Lock" in prompt
    assert "observer.lock" in prompt
    assert "Notification Deduplication" in prompt
    assert "NO_NOTIFICATION" in prompt
    assert "NOTIFICATION_EMITTED" in prompt
    assert "interactive" in prompt.lower()
    assert "on-demand" in prompt.lower()


# -----------------------------------------------------------------------------
# Test 4: Weekly Market Intelligence Approval Continuation & Contradiction Fix
# -----------------------------------------------------------------------------

def test_weekly_market_intelligence_approval_continuation_and_no_contradiction() -> None:
    """Verifies weekly market intelligence retains approval continuation without decline contradiction."""
    prompt = (ROOT / "automations" / "prompts" / "weekly_market_intelligence_agent.md").read_text(encoding="utf-8")

    # Verify active intent ingestion
    assert "STAGE 3: GOVERNED INGEST" in prompt
    assert "market_intelligence" in prompt

    # Verify approval continuation
    assert "AWAITING_APPROVAL" in prompt
    assert "approval_requests/" in prompt
    assert "approval_request_id" in prompt

    # Verify decline contradiction was eliminated: decline must NOT be COMPLETE
    assert 'decline: `COMPLETE`' not in prompt
    assert 'decline: COMPLETE' not in prompt
    assert "DECLINED" in prompt


# -----------------------------------------------------------------------------
# Test 5: Worktree Coverage Deliverable Protection & Confirm Token
# -----------------------------------------------------------------------------

def test_worktree_coverage_deliverable_protection() -> None:
    """Verifies consolidated PR queue & worktree cleanup prompt protects deliverables and mandates confirm token."""
    prompt = (ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md").read_text(encoding="utf-8")

    assert "Zero Data Loss Invariant" in prompt
    assert "01_*" in prompt
    assert "output/**" in prompt
    assert "--confirm-token" in prompt
    assert "ORPHAN_DELIVERABLES" in prompt
    assert "EMPTY_STUB" in prompt
    assert "inventory --json" in prompt
    assert "prune-worktrees" not in prompt
    assert "list --worktree-only" not in prompt


# -----------------------------------------------------------------------------
# Test 6: Repository Antipattern Audit Discovery Suite & Interactive Selection
# -----------------------------------------------------------------------------

def test_repository_antipattern_audit_discovery_and_interactive_waves() -> None:
    """Verifies consolidated SSOT & architecture prompt runs discovery suite and provides interactive selection."""
    prompt = (ROOT / "automations" / "prompts" / "ssot_drift_remediation_agent.md").read_text(encoding="utf-8")

    assert "audit_agentic_boundaries.py" in prompt
    assert "run_governance_linters.py" in prompt
    assert "lint_anti_hardcoding.py" in prompt
    assert "audit_governance_harmonization.py" in prompt
    assert "Executive Health Scorecard" in prompt
    assert "Architecture Health" in prompt
    assert "Agentic Integrity" in prompt
    assert "Complexity Debt" in prompt
    assert "Refactor Risk" in prompt
    assert "remediation wave" in prompt


# -----------------------------------------------------------------------------
# Test 7: SSOT Drift Remediation 3-Phase Lifecycle & Reversion
# -----------------------------------------------------------------------------

def test_ssot_drift_remediation_lifecycle_and_reversion() -> None:
    """Verifies SSOT drift remediation prompt contains 3 phases, bounded scan, and test reversion."""
    prompt = (ROOT / "automations" / "prompts" / "ssot_drift_remediation_agent.md").read_text(encoding="utf-8")

    assert "Bounded Scan" in prompt
    assert "Config Drift" in prompt
    assert "SSOT Violations" in prompt
    assert "Respect Domain Boundaries" in prompt
    assert "Secret Flagging" in prompt
    assert "DO NOT auto-remediate" in prompt
    assert "refactor/daily-drift-fix-" in prompt
    assert "Mandatory Testing" in prompt
    assert "revert changes immediately" in prompt


# -----------------------------------------------------------------------------
# Test 8: Knowledge Graph Hygiene 5 Reviewers & 14-Table Accounting
# -----------------------------------------------------------------------------

def test_knowledge_graph_hygiene_five_reviewers_and_accounting() -> None:
    """Verifies knowledge graph hygiene prompt contains all 5 reviewers and 14-table accounting."""
    prompt = (ROOT / "automations" / "prompts" / "graph_hygiene_agent.md").read_text(encoding="utf-8")

    assert "Structural Inspector" in prompt
    assert "Timeline Detective" in prompt
    assert "Fact-Checker" in prompt
    assert "Security Officer" in prompt
    assert "Presiding Judge" in prompt
    assert "14 tables" in prompt
    assert "grounding ratio" in prompt
    assert "pre_snapshot_" in prompt
    assert "backups/hygiene_" in prompt
    assert "POST_VERIFICATION_FAILED" in prompt
    assert "after a WAL checkpoint" not in prompt  # Discovery must not checkpoint WAL


# -----------------------------------------------------------------------------
# Test 9: All Sidecar Prompts Eliminate Narrow Static Spreads & Bind Margin Bypass
# -----------------------------------------------------------------------------

def test_all_sidecar_prompts_eliminate_hardcoded_narrow_static_spreads_and_bind_margin_bypass() -> None:
    """Verifies no canonical prompt hardcodes 15%-20% narrow static second-option spreads that force redundant prompts."""
    prompts_dir = ROOT / "automations" / "prompts"
    forbidden_narrow_tokens = (
        "[Confidence: MEDIUM (75%)]",
        "[Confidence: MEDIUM (80%)]",
        "(Confidence: MEDIUM (70%))",
        "[72% — MEDIUM]",
    )
    violations: list[str] = []
    for p in sorted(prompts_dir.glob("*_agent.md")):
        txt = p.read_text(encoding="utf-8")
        for tok in forbidden_narrow_tokens:
            if tok in txt:
                violations.append(f"{p.name} contains hardcoded narrow static spread token '{tok}'")
        if "AUTONOMOUS" not in txt:
            violations.append(f"{p.name} missing 'AUTONOMOUS' 20% SSOT margin bypass binding")

    assert violations == [], "Sidecar prompt HITL streamlining violations:\n" + "\n".join(violations)

