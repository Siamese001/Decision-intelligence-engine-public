"""tests.test_scheduled_task_adapters

Wave 5 test suite verifying adapter reconciliation, command compatibility,
prompt contract compliance, 84/84 common control mappings, and 21/21 domain dispositions.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent

SEVEN_TASKS: list[str] = [
    "knowledge_graph_hygiene",
    "storage_repository_hygiene",
    "weekly_market_intelligence",
    "worktree_coverage_cleanup",
    "repository_antipattern_audit",
    "ssot_drift_remediation",
    "pr_queue_observer",
]

TEN_TASKS: list[str] = [
    "knowledge_graph_hygiene",
    "storage_repository_hygiene",
    "weekly_market_intelligence",
    "worktree_coverage_cleanup",
    "repository_antipattern_audit",
    "ssot_drift_remediation",
    "pr_queue_observer",
    "cost_quality_review",
    "pipeline_outcome_assurance",
    "technical_debt_elimination",
]

COMMON_CLAUSES: list[str] = [
    "C01_TRUSTED_LAUNCH",
    "C02_BOUNDED_AUTHORITY",
    "C03_CHANGE_ACCOUNTING",
    "C04_EVIDENCE_ACTION_OUTCOME",
    "C05_DURABLE_IDENTITY_CLOSURE",
    "C06_REPRODUCIBLE_RECONCILIATION",
    "C07_HONEST_COVERAGE",
    "C08_SEALED_EVIDENCE",
    "C09_FAILURE_LIFECYCLE",
    "C10_BOUNDED_REPEAT_EXECUTION",
    "C11_TESTED_EXECUTABLE_BINDING",
    "C12_PRESERVE_GUARDRAILS",
]

DOMAIN_CLAUSES: list[str] = [
    "D01_QUEUE_REPAIR",
    "D02_INGESTION_RECOVERY",
    "D03_QUERY_COVERAGE",
]

EXPECTED_DISPOSITIONS: dict[str, dict[str, str]] = {
    "knowledge_graph_hygiene": {"D01_QUEUE_REPAIR": "OWNED", "D02_INGESTION_RECOVERY": "OWNED", "D03_QUERY_COVERAGE": "OWNED"},
    "storage_repository_hygiene": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "NOT_APPLICABLE"},
    "weekly_market_intelligence": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "worktree_coverage_cleanup": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "NOT_APPLICABLE"},
    "repository_antipattern_audit": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "ssot_drift_remediation": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "pr_queue_observer": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "cost_quality_review": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "pipeline_outcome_assurance": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
    "technical_debt_elimination": {"D01_QUEUE_REPAIR": "ROUTED", "D02_INGESTION_RECOVERY": "ROUTED", "D03_QUERY_COVERAGE": "ROUTED"},
}


def test_common_control_matrix_84_mappings() -> None:
    """Acceptance criterion: 84/84 common-control mappings (seven tasks by twelve clauses)."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    assert matrix_file.is_file(), "Matrix JSON file must exist"

    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    assert data.get("contract_id") == "urn:nee:scheduled-task-common-control-matrix:1.0.0"
    tasks_map = data.get("tasks", {})

    total_controls = 0
    for task_id in SEVEN_TASKS:
        assert task_id in tasks_map, f"Task '{task_id}' must be in matrix"
        task_info = tasks_map[task_id]
        controls = task_info.get("common_controls", {})

        for clause in COMMON_CLAUSES:
            assert clause in controls, f"Task '{task_id}' missing clause '{clause}'"
            entry = controls[clause]
            assert entry["implemented"] is True
            assert bool(entry["reference"].strip())
            assert bool(entry["assertion"].strip())
            total_controls += 1

    assert total_controls == 84, f"Expected exactly 84 common controls, got {total_controls}"


def test_common_control_matrix_120_mappings() -> None:
    """Acceptance criterion: 120/120 common-control mappings across all 10 governed tasks."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    assert matrix_file.is_file(), "Matrix JSON file must exist"

    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    assert data.get("contract_id") == "urn:nee:scheduled-task-common-control-matrix:1.0.0"
    tasks_map = data.get("tasks", {})

    total_controls = 0
    for task_id in TEN_TASKS:
        assert task_id in tasks_map, f"Task '{task_id}' must be in matrix"
        task_info = tasks_map[task_id]
        controls = task_info.get("common_controls", {})

        for clause in COMMON_CLAUSES:
            assert clause in controls, f"Task '{task_id}' missing clause '{clause}'"
            entry = controls[clause]
            assert entry["implemented"] is True
            assert bool(entry["reference"].strip())
            assert bool(entry["assertion"].strip())
            total_controls += 1

    assert total_controls == 120, f"Expected exactly 120 common controls, got {total_controls}"


def test_domain_dispositions_21_mappings() -> None:
    """Acceptance criterion: 21 domain dispositions matching scheduled_task_overwrite_coverage.md."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    tasks_map = data.get("tasks", {})

    total_domain_mappings = 0
    for task_id in SEVEN_TASKS:
        task_info = tasks_map[task_id]
        domain_map = task_info.get("domain_dispositions", {})

        for domain_clause in DOMAIN_CLAUSES:
            assert domain_clause in domain_map, f"Task '{task_id}' missing domain '{domain_clause}'"
            entry = domain_map[domain_clause]
            expected_disp = EXPECTED_DISPOSITIONS[task_id][domain_clause]
            assert entry["disposition"] == expected_disp
            assert bool(entry["reason"].strip())
            total_domain_mappings += 1

    assert total_domain_mappings == 21, f"Expected exactly 21 domain mappings, got {total_domain_mappings}"


def test_domain_dispositions_30_mappings() -> None:
    """Acceptance criterion: 30 domain dispositions matching all 10 tasks across D01-D03."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    tasks_map = data.get("tasks", {})

    total_domain_mappings = 0
    for task_id in TEN_TASKS:
        task_info = tasks_map[task_id]
        domain_map = task_info.get("domain_dispositions", {})

        for domain_clause in DOMAIN_CLAUSES:
            assert domain_clause in domain_map, f"Task '{task_id}' missing domain '{domain_clause}'"
            entry = domain_map[domain_clause]
            expected_disp = EXPECTED_DISPOSITIONS[task_id][domain_clause]
            assert entry["disposition"] == expected_disp
            assert bool(entry["reason"].strip())
            total_domain_mappings += 1

    assert total_domain_mappings == 30, f"Expected exactly 30 domain mappings, got {total_domain_mappings}"


def test_prompt_contracts_and_mode_resolution() -> None:
    """Acceptance criterion: Every task definition has one source for mode; no prompt-level mode markers."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    tasks_map = data.get("tasks", {})

    for task_id in TEN_TASKS:
        rel_path = tasks_map[task_id]["prompt_path"]
        prompt_file = ROOT / rel_path
        assert prompt_file.is_file(), f"Prompt file not found: {rel_path}"

        lines = prompt_file.read_text(encoding="utf-8").splitlines()
        first_line = lines[0].strip() if lines else ""

        # First line MUST NOT be a prompt-level mode marker
        assert not first_line.startswith("MODE:"), f"Prompt '{rel_path}' must not use first-line mode marker"

        content = prompt_file.read_text(encoding="utf-8")
        assert "urn:nee:scheduled-task-common-control-matrix:1.0.0" in content
        assert "AuthorityContext" in content

        # Check references to C01-C12
        for idx in range(1, 13):
            c_tag = f"C{idx:02d}"
            assert c_tag in content, f"Prompt '{rel_path}' missing common control tag '{c_tag}'"


def test_all_ten_prompts_require_validated_implementation_plan_and_approval_gate() -> None:
    """Acceptance criterion: All 10 prompts mandate wave-based implementation plan, validation, and approval gate."""
    matrix_file = ROOT / "config" / "scheduled_tasks" / "common_control_matrix.json"
    data = json.loads(matrix_file.read_text(encoding="utf-8"))
    tasks_map = data.get("tasks", {})

    for task_id in TEN_TASKS:
        rel_path = tasks_map[task_id]["prompt_path"]
        prompt_file = ROOT / rel_path
        assert prompt_file.is_file(), f"Prompt file not found: {rel_path}"

        content = prompt_file.read_text(encoding="utf-8")
        assert "docs/plans/<branch-slug>.md" in content, (
            f"Prompt '{rel_path}' missing standardized plan path 'docs/plans/<branch-slug>.md'"
        )
        assert "validate_implementation_plan.py --file" in content, (
            f"Prompt '{rel_path}' missing validation command 'validate_implementation_plan.py --file'"
        )
        assert "ask_user_question" in content, (
            f"Prompt '{rel_path}' missing Plan Approval Gate using 'ask_user_question'"
        )
        assert "main" in content and any(w in content.lower() for w in ["bar", "prohibit", "never"]), (
            f"Prompt '{rel_path}' missing pre-merge live DB write / main mutation prohibition"
        )


def test_command_compatibility_and_no_stale_flags() -> None:
    """Acceptance criterion: Every referenced executable/flag resolves; stale interfaces removed."""
    # 1. pr_governance inventory exists
    pr_gov = ROOT / "tools" / "pr_governance.py"
    assert pr_gov.is_file()

    # 2. Check consolidated PR queue & worktree coverage prompt does not reference non-existent prune-worktrees or list --worktree-only
    wt_prompt = (ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md").read_text(encoding="utf-8")
    assert "prune-worktrees" not in wt_prompt
    assert "list --worktree-only" not in wt_prompt
    assert "inventory --json" in wt_prompt

    # 3. Check graph hygiene prompt does not checkpoint WAL during discovery
    gh_prompt = (ROOT / "automations" / "prompts" / "graph_hygiene_agent.md").read_text(encoding="utf-8")
    assert "after a WAL checkpoint" not in gh_prompt
    assert "READ-ONLY" in gh_prompt


def test_pr_queue_observer_strictly_observation_only() -> None:
    """Acceptance criterion: PR observer never creates repair branches or changes directives."""
    obs_prompt = (ROOT / "automations" / "prompts" / "pr_queue_observer_agent.md").read_text(encoding="utf-8")
    assert "Strict Observation-Only Authority" in obs_prompt
    assert "Zero Mutation Guarantee" in obs_prompt
    assert "forbidden from creating feature/repair branches" in obs_prompt


def test_weekly_market_intelligence_hitl_cluster_partitioning_and_acronym_expansion() -> None:
    """Acceptance criterion: Candidate signals partition into atomic HITL clusters with expanded acronyms."""
    from tools.scheduled_task_core.adapters_core.intelligence_engine import (
        evaluate_candidate_signals,
        expand_market_acronyms,
        partition_hitl_clusters,
    )

    passed, _ = evaluate_candidate_signals(None)
    clusters = partition_hitl_clusters(passed)
    assert len(clusters) == 2
    cluster_ids = [c["cluster_id"] for c in clusters]
    assert "cluster-broader-market-trends" in cluster_ids
    assert "cluster-apex_global-apex-implications" in cluster_ids

    for c in clusters:
        assert c["finding_count"] >= 1
        assert isinstance(c["plain_english_summary"], list)
        assert len(c["plain_english_summary"]) == c["finding_count"]
        q = c["ready_to_ask_question"]
        assert "question" in q and "options" in q
        assert q["options"][0].startswith("(Recommended)")
        for line in c["plain_english_summary"]:
            assert "FPSC" not in line
            assert "FERC" not in line
            assert "APEX" not in line
            assert "BESS" not in line

    expanded = expand_market_acronyms("APEX and NEE filed FPSC and FERC dockets for PJM and ERCOT BESS PPAs.")
    assert "Apex Power & Grid" in expanded
    assert "Apex Global Enterprises" in expanded
    assert "Florida Public Service Commission" in expanded
    assert "Federal Energy Regulatory Commission" in expanded
    assert "Pennsylvania-New Jersey-Maryland Interconnection" in expanded
    assert "Electric Reliability Council of Texas" in expanded
    assert "Battery Energy Storage System" in expanded
    assert "Power Purchase Agreements" in expanded
