"""tests/test_tokenomics_sidecar_registration.py

Unit and contract verification for the 11th Sidecar Automation Agent
(`tokenomics_cost_optimization` / Tokenomics Agent) and 8-Pillar Tokenomics Auditor.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.audit_tokenomics_gaps import run_tokenomics_audit
from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, run_prompt_ssot_lint
from tools.scheduled_task_core.contracts import TWELVE_GOVERNED_TASK_IDS, get_task_definition
from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
    resolve_canonical_task_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tokenomics_agent_registered_in_ssot_pointers() -> None:
    """Verify tokenomics_cost_optimization is registered in TASK_SIDECAR_MAP and resolves cleanly."""
    assert "tokenomics_cost_optimization" in TASK_SIDECAR_MAP
    assert len(TASK_SIDECAR_MAP) == 17
    info = TASK_SIDECAR_MAP["tokenomics_cost_optimization"]
    assert info["sidecar_dir"] == "tokenomics-cost-optimization-agent"
    assert info["canonical_prompt"] == "automations/prompts/tokenomics_agent.md"
    assert info["title"] == "Cost Optimization Agent"
    assert resolve_canonical_task_id("tokenomics_cost_optimization") == "tokenomics_cost_optimization"

    pointer = generate_ssot_pointer_prompt("tokenomics_cost_optimization", REPO_ROOT)
    assert is_valid_pointer_prompt(pointer)


def test_tokenomics_prompt_ssot_lint_passes() -> None:
    """Verify tools/lint_prompt_ssot.py passes with 0 violations across all 11 canonical prompts."""
    assert "tokenomics_cost_optimization" in EXPECTED_CANONICAL_PROMPTS
    ok, errors = run_prompt_ssot_lint(REPO_ROOT)
    assert ok, f"Prompt SSOT lint failed: {errors}"


def test_tokenomics_task_registry_and_launch_adapter(tmp_path: Path) -> None:
    """Verify task_registry.json definition and AntigravityHostLaunchAdapter dispatch for tokenomics_cost_optimization."""
    assert "tokenomics_cost_optimization" in TWELVE_GOVERNED_TASK_IDS
    tdef = get_task_definition("tokenomics_cost_optimization")
    assert tdef["prompt_path"] == "automations/prompts/tokenomics_agent.md"
    assert tdef["registration_status"]["status"] == "RECONCILED_ACTIVE"

    adapter = AntigravityHostLaunchAdapter(repo_root=REPO_ROOT)
    res_clean = adapter.launch_task("tokenomics_cost_optimization", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_clean.terminal_outcome == "COMPLETE"

    # Seed hotspot fixture to trigger HITL_REQUIRED in scheduled mode and generate valid wave plan
    (tmp_path / "unoptimized_token_hotspots.json").write_text("{}", encoding="utf-8")
    res_gap = adapter.launch_task("tokenomics_cost_optimization", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_gap.terminal_outcome == "HITL_REQUIRED"
    assert res_gap.implementation_plan_path is not None
    assert Path(res_gap.implementation_plan_path).is_file()


def test_audit_tokenomics_gaps_returns_eight_pillars() -> None:
    """Verify run_tokenomics_audit evaluates all 8 tokenomics pillars deterministically."""
    report = run_tokenomics_audit(REPO_ROOT)
    assert report["total_pillars"] == 8
    assert len(report["pillars"]) == 8
    pillar_ids = [p["pillar"] for p in report["pillars"]]
    assert pillar_ids == [
        "1_model_pinning_and_benchmarking",
        "2_prompt_caching",
        "3_semantic_caching",
        "4_other_deterministic_caches",
        "5_healer_agents",
        "6_judge_retries_and_feedback_loops",
        "7_system_learning",
        "8_frontier_thought_leadership",
    ]
    # Verify JSON serializability
    serialized = json.dumps(report)
    assert "urn:nee:tokenomics-8-pillar-audit:1.0.0" in serialized
