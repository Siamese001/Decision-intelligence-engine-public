"""tools/scheduled_task_core/adapters.py

Public facade re-exporting domain task adapters and result models
from tools.scheduled_task_core.adapters_core.
"""
from __future__ import annotations

from .adapters_core import (
    TaskExecutionResult,
    run_agentic_repo_best_practices_scout,
    run_corner_foresight_blindspot_scout,
    run_cost_quality_review,
    run_e2e_critical_functionality_assurance,
    run_evals_surface_assurance,
    run_frontier_llm_research_scout,
    run_google_partner_advisory_weekly_sync,
    run_hotspot_value_enhancement,
    run_knowledge_graph_hygiene,
    run_pipeline_outcome_assurance,
    run_pr_queue_observer,
    run_repository_antipattern_audit,
    run_retrieval_surface_assurance,
    run_ssot_drift_remediation,
    run_storage_repository_hygiene,
    run_system_learning_assurance,
    run_technical_debt_elimination,
    run_testing_gaps_assurance,
    run_third_party_tool_consolidation,
    run_timeout_lock_watchdog,
    run_tokenomics_cost_optimization,
    run_weekly_market_intelligence,
    run_worktree_coverage_cleanup,
)

__all__ = [
    "TaskExecutionResult",
    "run_agentic_repo_best_practices_scout",
    "run_corner_foresight_blindspot_scout",
    "run_cost_quality_review",
    "run_e2e_critical_functionality_assurance",
    "run_evals_surface_assurance",
    "run_frontier_llm_research_scout",
    "run_hotspot_value_enhancement",
    "run_knowledge_graph_hygiene",
    "run_pipeline_outcome_assurance",
    "run_pr_queue_observer",
    "run_repository_antipattern_audit",
    "run_retrieval_surface_assurance",
    "run_ssot_drift_remediation",
    "run_storage_repository_hygiene",
    "run_system_learning_assurance",
    "run_technical_debt_elimination",
    "run_testing_gaps_assurance",
    "run_third_party_tool_consolidation",
    "run_timeout_lock_watchdog",
    "run_tokenomics_cost_optimization",
    "run_weekly_market_intelligence",
    "run_worktree_coverage_cleanup",
]
