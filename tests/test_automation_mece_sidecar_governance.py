"""tests/test_automation_mece_sidecar_governance.py

Comprehensive unit and adversarial test suite verifying MECE boundaries,
18-sidecar registry parity, runner exclusivity (INV-PROMPT-SSOT-10),
cron schedule parity (INV-PROMPT-SSOT-11), and Stage VI governance (INV-PROMPT-SSOT-12).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.lint_prompt_ssot import (
    EXPECTED_CANONICAL_PROMPTS,
    audit_canonical_prompts,
    audit_cron_and_description_parity,
    audit_mece_runner_exclusivity,
    audit_stage_vi_prompts,
    run_prompt_ssot_lint,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox_repo(tmp_path: Path) -> Path:
    """Creates a temporary isolated sandbox mirroring prompts and task configurations."""
    sandbox = tmp_path / "repo"
    sandbox.mkdir()

    # Mirror automations/prompts
    src_prompts = ROOT / "automations" / "prompts"
    dst_prompts = sandbox / "automations" / "prompts"
    shutil.copytree(src_prompts, dst_prompts)

    # Mirror config/scheduled_tasks
    src_config = ROOT / "config" / "scheduled_tasks"
    dst_config = sandbox / "config" / "scheduled_tasks"
    shutil.copytree(src_config, dst_config)

    # Mirror rule files for tripartite schema check
    rule_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
    if rule_file.is_file():
        dst_rule = sandbox / ".agents" / "rules" / "pr-queue-summary.md"
        dst_rule.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rule_file, dst_rule)

    render_file = ROOT / "tools" / "pr_governance_core" / "executive_render.py"
    if render_file.is_file():
        dst_render = sandbox / "tools" / "pr_governance_core" / "executive_render.py"
        dst_render.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(render_file, dst_render)

    return sandbox


# ==============================================================================
# 1. Baseline Unit Tests (18 Tasks & Master Sequence)
# ==============================================================================

def test_full_18_canonical_prompts_enrolled() -> None:
    """Verifies that exactly 17 canonical prompts are enrolled across Stages I through VI."""
    assert len(EXPECTED_CANONICAL_PROMPTS) == 17
    assert "nea_charter_portfolio_roster" in EXPECTED_CANONICAL_PROMPTS
    assert "google_partner_advisory_weekly_sync" in EXPECTED_CANONICAL_PROMPTS


def test_baseline_prompt_ssot_linter_passes() -> None:
    """Verifies that the repository passes full SSOT prompt linting without errors."""
    ok, errors = run_prompt_ssot_lint(ROOT)
    assert ok, f"Expected 0 prompt SSOT errors, got {len(errors)}: {errors}"


def test_stage_vi_prompts_exist_and_conform() -> None:
    """Verifies that Stage VI prompts satisfy common control matrix and query criteria."""
    errors = audit_stage_vi_prompts(ROOT)
    assert not errors, f"Stage VI errors: {errors}"


def test_cron_and_description_parity_baseline() -> None:
    """Verifies that task_registry.json declared crons match registration_reconciliation.json cadences."""
    errors = audit_cron_and_description_parity(ROOT)
    assert not errors, f"Cron parity errors: {errors}"


def test_mece_runner_exclusivity_baseline() -> None:
    """Verifies that critical runners are executed only by their designated owner prompt."""
    errors = audit_mece_runner_exclusivity(ROOT)
    assert not errors, f"Runner exclusivity errors: {errors}"


def test_task_registry_master_sequence_stages() -> None:
    """Verifies that all 17 tasks declare consecutive master sequence orders 1 through 17."""
    reg_path = ROOT / "config" / "scheduled_tasks" / "task_registry.json"
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    sidecar_map = data.get("sidecar_map", {})

    assert len(sidecar_map) == 17
    orders = sorted(info["master_sequence_order"] for info in sidecar_map.values())
    assert orders == list(range(1, 18)), f"Expected sequence 1..17, got {orders}"

    stages = {info["master_sequence_stage"] for info in sidecar_map.values()}
    assert any("Stage I" in s for s in stages)
    assert any("Stage II" in s for s in stages)
    assert any("Stage III" in s for s in stages)
    assert any("Stage IV" in s for s in stages)
    assert any("Stage V" in s for s in stages)
    assert any("Stage VI" in s for s in stages)


# ==============================================================================
# 2. Adversarial & Edge Case Tests
# ==============================================================================

def test_adversarial_runner_collision(sandbox_repo: Path) -> None:
    """Adversarial: injecting an exclusive runner execution into an unauthorized prompt fails closed."""
    agent_file = sandbox_repo / "automations" / "prompts" / "pipeline_outcome_assurance_agent.md"
    content = agent_file.read_text(encoding="utf-8")
    # Inject unauthorized execution of govern_learning_proposals.py
    malicious = content + "\n\nExecute runner: `.venv/bin/python tools/govern_learning_proposals.py --check`\n"
    agent_file.write_text(malicious, encoding="utf-8")

    errors = audit_mece_runner_exclusivity(sandbox_repo)
    assert any("govern_learning_proposals.py" in err for err in errors)
    assert any("pipeline_outcome_assurance_agent.md" in err for err in errors)


def test_adversarial_cron_mismatch(sandbox_repo: Path) -> None:
    """Adversarial: altering declared_cron in task_registry.json triggers cron parity failure."""
    reg_path = sandbox_repo / "config" / "scheduled_tasks" / "task_registry.json"
    reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
    # Corrupt one task's declared_cron
    reg_data["tasks"]["storage_repository_hygiene"]["cadence_specification"]["declared_cron"] = "0 0 * * *"
    reg_path.write_text(json.dumps(reg_data, indent=2), encoding="utf-8")

    errors = audit_cron_and_description_parity(sandbox_repo)
    assert any("storage_repository_hygiene" in err and "declared_cron mismatch" in err for err in errors)


def test_adversarial_stage_vi_em_dash_detected(sandbox_repo: Path) -> None:
    """Adversarial: introducing an em-dash into a Stage VI prompt fails Rule 9 check."""
    roster_file = sandbox_repo / "automations" / "prompts" / "sidecars" / "nea_charter_portfolio_roster_agent.md"
    content = roster_file.read_text(encoding="utf-8")
    corrupted = content + "\n\nReview gap — foundational lift\n"
    roster_file.write_text(corrupted, encoding="utf-8")

    errors = audit_stage_vi_prompts(sandbox_repo)
    assert any("violates Rule 9" in err for err in errors)


def test_adversarial_stage_vi_missing_canonical_eligible(sandbox_repo: Path) -> None:
    """Adversarial: omitting canonical_eligible = 1 filter from Stage VI queries fails closed."""
    sync_file = sandbox_repo / "automations" / "prompts" / "sidecars" / "google_partner_advisory_weekly_sync_agent.md"
    content = sync_file.read_text(encoding="utf-8")
    stripped = content.replace("canonical_eligible = 1", "")
    sync_file.write_text(stripped, encoding="utf-8")

    errors = audit_stage_vi_prompts(sandbox_repo)
    assert any("canonical_eligible = 1" in err for err in errors)


def test_adversarial_rogue_sidecar_prompt(sandbox_repo: Path) -> None:
    """Adversarial: placing an unmanaged prompt into automations/prompts triggers rogue prompt failure."""
    rogue_file = sandbox_repo / "automations" / "prompts" / "unauthorized_sidecar_agent.md"
    rogue_file.write_text("# Rogue Agent\n", encoding="utf-8")

    errors = audit_canonical_prompts(sandbox_repo)
    assert any("Rogue un-enrolled prompt files detected" in err for err in errors)


def test_all_18_prompts_enforce_persistent_learning_loops() -> None:
    """Verifies INV-PROMPT-SSOT-14 across all 18 canonical prompts and shared contract."""
    from tools.lint_prompt_ssot import audit_automation_persistent_learning_loops

    errors = audit_automation_persistent_learning_loops(ROOT)
    assert errors == [], f"Persistent learning loop errors: {errors}"


def test_adversarial_missing_persistent_learning_loop_token(sandbox_repo: Path) -> None:
    """Adversarial: stripping INV-AUTOMATION-PERSISTENT-LEARNING-LOOP fails INV-PROMPT-SSOT-14 closed."""
    from tools.lint_prompt_ssot import audit_automation_persistent_learning_loops

    target_prompt = sandbox_repo / "automations" / "prompts" / "tokenomics_agent.md"
    content = target_prompt.read_text(encoding="utf-8")
    target_prompt.write_text(
        content.replace("INV-AUTOMATION-PERSISTENT-LEARNING-LOOP", "REMOVED_TOKEN"),
        encoding="utf-8",
    )
    errors = audit_automation_persistent_learning_loops(sandbox_repo)
    assert any("INV-PROMPT-SSOT-14" in err and "tokenomics_agent.md" in err for err in errors)


def test_e2e_persistent_store_feedback_loop_calibrates_decision_confidence(tmp_path: Path) -> None:
    """E2E: Persistent-store feedback loop records outcomes in SQLite/JSON and calibrates subsequent confidence."""
    from tools.scheduled_task_core import (
         AuthorityContext,
        ExecutionMode,
        TriggerOrigin,
        calibrate_task_decision_options,
        record_task_decision_feedback,
    )
    from tools.scheduled_task_core.adapters_core.watchdogs import run_tokenomics_cost_optimization

    store_dir = tmp_path / "persistent_ledger"
    run_dir_1 = tmp_path / "run_1"
    run_dir_1.mkdir(parents=True, exist_ok=True)
    findings = [{"issue": "TOKEN_HOTSPOT", "observation_count": 2}]

    initial = calibrate_task_decision_options(
        task_name="tokenomics_cost_optimization",
        findings=findings,
        run_dir=run_dir_1,
        store_dir=store_dir,
    )
    assert initial["prior_sample_count"] == 0

    for idx in range(3):
        rdir = tmp_path / f"override_run_{idx}"
        rdir.mkdir(parents=True, exist_ok=True)
        receipt = record_task_decision_feedback(
            task_name="tokenomics_cost_optimization",
            findings=findings,
            run_dir=rdir,
            approval={"override_recommended": True},
            status="COMPLETE",
            store_dir=store_dir,
        )
        assert receipt["feedback_recorded"] is True
        assert receipt["outcome_type"] == "OVERRIDDEN"

    after_overrides = calibrate_task_decision_options(
        task_name="tokenomics_cost_optimization",
        findings=findings,
        run_dir=run_dir_1,
        store_dir=store_dir,
    )
    assert after_overrides["prior_sample_count"] == 3
    opts_by_key = {o["key"]: o for o in after_overrides["calibrated_options"]}
    init_by_key = {o["key"]: o for o in initial["calibrated_options"]}
    assert opts_by_key["defer_remediation_keep_open"]["confidence_score"] > init_by_key["defer_remediation_keep_open"]["confidence_score"]
    assert opts_by_key["approve_wave_remediation"]["confidence_score"] < init_by_key["approve_wave_remediation"]["confidence_score"]

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir(parents=True, exist_ok=True)
    adapter_run_dir = tmp_path / "adapter_run"
    adapter_run_dir.mkdir(parents=True, exist_ok=True)
    ctx = AuthorityContext(
        task_id="tokenomics_cost_optimization",
        run_id="run-tok-001",
        execution_mode=ExecutionMode.SCHEDULED,
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        actor="scheduler",
    )
    result = run_tokenomics_cost_optimization(ctx, fixture_root, adapter_run_dir)
    assert "learning_feedback_loop" in result.metadata
    loop_meta = result.metadata["learning_feedback_loop"]
    assert loop_meta["invariant_id"] == "INV-AUTOMATION-PERSISTENT-LEARNING-LOOP"
    assert Path(loop_meta["sqlite_path"]).is_file()
    assert Path(loop_meta["ledger_path"]).is_file()
    assert Path(loop_meta["receipt_path"]).is_file()

