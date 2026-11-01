"""tools/task_restoration_core/constants.py

Constants, enums, test scopes, and input file sets for task restoration waves.
"""
from __future__ import annotations

# Canonical Complete Inventory of Governed Scheduled Automation Tasks (7 Tasks).
# NOTE ON TAXONOMY RECONCILIATION:
# SEVEN_TASKS constitutes the definitive repository inventory of scheduled automation tasks.
# It is distinct from the 8-agent adversarial RCA debate panel (configured in
# tools/run_8_agent_rca_graphdb_vs_retrieval.py), which is an on-demand forensic council
# comprising 8 adversarial agent personas and 1 judicial arbiter for defect root cause analyses.
SEVEN_TASKS: set[str] = {
    "knowledge_graph_hygiene",
    "storage_repository_hygiene",
    "weekly_market_intelligence",
    "worktree_coverage_cleanup",
    "repository_antipattern_audit",
    "ssot_drift_remediation",
    "pr_queue_observer",
}

VALID_CATEGORIES: set[str] = {
    "stage",
    "check",
    "approval",
    "output",
    "recovery_rule",
}

VALID_DISPOSITIONS: set[str] = {
    "retained",
    "corrected",
    "explicitly_superseded",
}

VALID_TARGET_MODES: set[str] = {
    "scheduled",
    "interactive",
    "both",
}

WAVE_TEST_FILES: dict[int, list[str]] = {
    1: [
        "tests/test_verify_task_restoration_wave_adversarial.py",
        "tests/end_to_end/test_task_restoration_wave_e2e.py",
    ],
    2: [
        "tests/test_verify_task_restoration_wave_adversarial.py",
        "tests/end_to_end/test_task_restoration_wave_e2e.py",
        "tests/test_antigravity_task_procedures.py",
    ],
    3: [
        "tests/test_verify_task_restoration_wave_adversarial.py",
        "tests/end_to_end/test_task_restoration_wave_e2e.py",
        "tests/test_antigravity_task_procedures.py",
        "tests/end_to_end/test_antigravity_task_modes_e2e.py",
    ],
    4: [
        "tests/test_verify_task_restoration_wave_adversarial.py",
        "tests/end_to_end/test_task_restoration_wave_e2e.py",
        "tests/test_antigravity_task_procedures.py",
        "tests/end_to_end/test_antigravity_task_modes_e2e.py",
        "tests/test_antigravity_task_modes_adversarial.py",
    ],
    5: [
        "tests/test_verify_task_restoration_wave_adversarial.py",
        "tests/end_to_end/test_task_restoration_wave_e2e.py",
        "tests/test_antigravity_task_procedures.py",
        "tests/end_to_end/test_antigravity_task_modes_e2e.py",
        "tests/test_antigravity_task_modes_adversarial.py",
        "tests/end_to_end/test_antigravity_task_registration_wave5_e2e.py",
    ],
}

WAVE_1_INPUT_FILES: list[str] = [
    "docs/plans/antigravity_task_requirements.json",
    "docs/plans/docs_antigravity_dual_mode_restoration_rca.md",
    "docs/investigations/antigravity_task_restoration_evidence.json",
    "tools/verify_task_restoration_wave.py",
    "tools/task_restoration_core/__init__.py",
    "tools/task_restoration_core/constants.py",
    "tools/task_restoration_core/inventory.py",
    "tools/task_restoration_core/operational.py",
    "tests/test_verify_task_restoration_wave_adversarial.py",
    "tests/end_to_end/test_task_restoration_wave_e2e.py",
    "tests/manifests/antigravity_dual_mode_restoration_manifest.json",
    "automations/prompts/graph_hygiene_agent.md",
    "automations/prompts/pipeline_outcome_assurance_agent.md",
    "automations/prompts/pr_queue_observer_agent.md",
    "automations/prompts/ssot_drift_remediation_agent.md",
    "automations/prompts/weekly_market_intelligence_agent.md",
]

WAVE_2_INPUT_FILES: list[str] = WAVE_1_INPUT_FILES + [
    "tests/test_antigravity_task_procedures.py",
    "config/scheduled_tasks/task_registry.json",
]

WAVE_3_INPUT_FILES: list[str] = WAVE_2_INPUT_FILES + [
    "tools/scheduled_task_core/__init__.py",
    "tools/scheduled_task_core/lifecycle.py",
    "tools/scheduled_task_core/adapters.py",
    "tools/scheduled_task_core/adapters_core/__init__.py",
    "tools/scheduled_task_core/adapters_core/models.py",
    "tools/scheduled_task_core/adapters_core/hygiene.py",
    "tools/scheduled_task_core/adapters_core/intelligence.py",
    "tools/scheduled_task_core/adapters_core/audits.py",
    "tools/scheduled_task_core/launch_adapters.py",
    "tools/scheduled_task_core/authority.py",
    "tests/end_to_end/test_antigravity_task_modes_e2e.py",
]

WAVE_4_INPUT_FILES: list[str] = WAVE_3_INPUT_FILES + [
    "tools/generate_antigravity_task_exports.py",
    "tests/test_antigravity_task_modes_adversarial.py",
    "config/scheduled_tasks/rollout_package.json",
    "reports/scheduled_task_hardening/qualification_report.md",
]

WAVE_5_INPUT_FILES: list[str] = WAVE_4_INPUT_FILES + [
    "tools/reconcile_antigravity_registrations.py",
    "tests/end_to_end/test_antigravity_task_registration_wave5_e2e.py",
    "config/scheduled_tasks/registration_reconciliation.json",
    "artifacts/exports/rollback_snapshots/knowledge_graph_hygiene_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/pr_queue_observer_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/repository_antipattern_audit_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/ssot_drift_remediation_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/storage_repository_hygiene_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/weekly_market_intelligence_pre_restoration.md",
    "artifacts/exports/rollback_snapshots/worktree_coverage_cleanup_pre_restoration.md",
]

WAVE_INPUT_FILES: dict[int, list[str]] = {
    1: WAVE_1_INPUT_FILES,
    2: WAVE_2_INPUT_FILES,
    3: WAVE_3_INPUT_FILES,
    4: WAVE_4_INPUT_FILES,
    5: WAVE_5_INPUT_FILES,
}

