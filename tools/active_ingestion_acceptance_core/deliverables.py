"""
tools/active_ingestion_acceptance_core/deliverables.py

Single Source of Truth for Deliverable Manifests across Active Ingestion Waves 1-7.
"""
from __future__ import annotations

WAVE_1_DELIVERABLES = (
    "governance/policy_registry.yaml", "tools/generate_agents_index.py", "AGENTS.md",
    ".agents/rules/ingestion-lifecycle.md", ".agents/rules/receipt-taxonomies.md",
    ".agents/rules/hitl-decisions.md", ".agents/rules/wave-progression.md",
    ".agents/skills/nee-ingestion/SKILL.md", "PROJECT_INSTRUCTIONS.md",
    "cards/NEE_99_Project_Instructions.md",
    "contracts/dual_pipeline/schemas/active-ingestion-run.schema.json",
    "contracts/dual_pipeline/schemas/active-ingestion-approval-request.schema.json",
    "contracts/dual_pipeline/schemas/active-ingestion-completion-receipt.schema.json",
    "contracts/dual_pipeline/fixtures/valid/active-ingestion-run.json",
    "contracts/dual_pipeline/fixtures/valid/active-ingestion-approval-request.json",
    "contracts/dual_pipeline/fixtures/valid/active-ingestion-completion-receipt.json",
    "contracts/dual_pipeline/fixtures/invalid/active-ingestion-run-premature-complete.json",
    "contracts/dual_pipeline/fixtures/invalid/active-ingestion-approval-unscoped.json",
    "contracts/dual_pipeline/fixtures/invalid/active-ingestion-completion-no-coverage.json",
    "contracts/dual_pipeline/active_ingestion_status_mapping.json",
    "contracts/dual_pipeline/active_ingestion_entrypoint_inventory.json",
    "tests/manifests/active_graph_ingestion.json", "tools/run_active_ingestion_acceptance.py",
)

WAVE_2_DELIVERABLES = (
    "dual_pipeline/hitl_governance_core/runtime_tool_adapter.py",
    "dual_pipeline/hitl_governance_core/active_ingestion_approval.py",
    "dual_pipeline/hitl_ui_bridge.py", "dual_pipeline/cli/ingest.py",
    "dual_pipeline/cli/handlers.py", "dual_pipeline/completion_core/guard.py",
    "tests/manifests/active_graph_ingestion.json",
    "tests/end_to_end/test_active_ingestion_approval_e2e.py",
    "tests/adversarial/test_active_ingestion_approval_adversarial.py",
    "tools/run_active_ingestion_acceptance.py",
)

WAVE_3_DELIVERABLES = (
    "dual_pipeline/canonical_pipeline/pipeline.py",
    "dual_pipeline/canonical_pipeline/publish_helper.py",
    "dual_pipeline/canonical_pipeline/publication_journal.py",
    "dual_pipeline/canonical_pipeline/document_resume_binding.py",
    "dual_pipeline/cli/handlers.py", "dual_pipeline/cli/engine.py",
    "tests/manifests/active_graph_ingestion.json",
    "tests/end_to_end/test_active_ingestion_publication_e2e.py",
    "tests/adversarial/test_active_ingestion_recovery_adversarial.py",
    "tools/run_active_ingestion_acceptance.py",
)

WAVE_4_DELIVERABLES = (
    "dual_pipeline/cli/parser.py", "dual_pipeline/cli/ingest.py",
    "dual_pipeline/canonical_pipeline/publish_helper.py",
    "dual_pipeline/canonical_pipeline/pipeline.py",
    "dual_pipeline/notes_pipeline.py", "dual_pipeline/document_pipeline_core/pipeline.py",
    "dual_pipeline/transcript_pipeline_core/pipeline.py", "dual_pipeline/transcript_cli.py",
    "dual_pipeline/pipeline_runner.py", "dual_pipeline/runtime/pipeline_context.py",
    "scripts/ingest_meeting.sh", "dual_pipeline/cli/canonical_invocation.py",
    "tests/manifests/active_graph_ingestion.json",
    "tests/end_to_end/test_active_ingestion_entrypoints_e2e.py",
    "tests/adversarial/test_active_ingestion_modes_adversarial.py",
    "tools/run_active_ingestion_acceptance.py",
)

WAVE_5_DELIVERABLES = (
    "automations/prompts/weekly_market_intelligence_agent.md",
    "docs/scheduled_task_graph_governance_gaps.md",
    ".agents/hooks.json",
    "dual_pipeline/hitl_governance_core/scheduler_adapter.py",
    "tests/end_to_end/test_weekly_prompt_cli_parse_e2e.py",
    "tests/test_weekly_prompt_governance_edge_cases.py",
    "tests/end_to_end/test_active_ingestion_scheduled_e2e.py",
    "tests/adversarial/test_active_ingestion_dispatch_adversarial.py",
    "tests/manifests/active_graph_ingestion.json",
    "tools/run_active_ingestion_acceptance.py",
)

WAVE_6_DELIVERABLES = (
    "contracts/dual_pipeline/schemas/recovery-manifest.schema.json",
    "contracts/dual_pipeline/fixtures/valid/recovery-manifest.json",
    "contracts/dual_pipeline/fixtures/invalid/recovery-manifest-invalid-status.json",
    "contracts/dual_pipeline/contract_inventory.json",
    "tools/reconcile_ingestion_coverage.py",
    "tests/end_to_end/test_ingestion_coverage_recovery_e2e.py",
    "tests/adversarial/test_ingestion_coverage_recovery_adversarial.py",
    "tests/manifests/active_graph_ingestion.json",
    "tools/run_active_ingestion_acceptance.py",
)

WAVE_7_DELIVERABLES = (
    "config/active_ingestion_qualification_manifest.json",
    "config/active_ingestion_authorization_ref.json",
    "tests/end_to_end/test_active_ingestion_live_workflow_e2e.py",
    "tests/adversarial/test_active_ingestion_live_recovery_adversarial.py",
    "tests/manifests/active_graph_ingestion.json",
    "tools/run_active_ingestion_acceptance.py",
)


