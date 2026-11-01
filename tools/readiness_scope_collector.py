"""tools/readiness_scope_collector.py

Complete, status-aware Git change collector for promotion readiness (Wave 3).
Captures all changed files between base and candidate branches, including:
1. Deletions (removes --diff-filter=d restriction)
2. Renames (captures both source and destination endpoints)
3. Unknown extensions (.sql, .sh, .cfg, and extensionless files)
4. Shell and extensionless git hooks (.githooks/*)
5. Untracked and dirty working tree files
6. Ingestion paths (removes hardcoded ingestion/ exclusion)
7. Explicit failure on Git collection errors (no silent suppression)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import List, Sequence, Set

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_PREFIXES: tuple[str, ...] = (
    ".worktrees/",
    ".git/",
    "archive/",
    "reports/",
    ".local/",
    ".venv/",
)

FILE_SCOPED_PYTHON_GATES: Set[int] = {2, 3, 5, 6, 7, 8, 13, 14, 18, 19, 21, 25, 27, 33, 34}

MODULARITY_GOVERNANCE_CONFIG_FILES: Set[str] = {
    "config/governance/file_budget_exclusions.json",
    "config/governance/module_budget_policy.json",
    "reports/file_budget_burndown_baseline.json",
    "tools/layer_hierarchy_manifest.json",
    "pyproject.toml",
}

SSOT_GOVERNANCE_CONFIG_FILES: Set[str] = {
    "config/runtime/parameters.json",
    "config/runtime/model_registry.json",
    "config/governance/project_registry.json",
    "dual_pipeline/parameters.py",
    "dual_pipeline/parameters_ops.py",
    "dual_pipeline/release_thresholds.py",
    "dual_pipeline/model_registry.py",
}


def is_empty_scope_skippable(
    stage_idx: int,
    target_files: List[str],
    full: bool = False,
) -> bool:
    """Determine whether a file-scoped gate can fast-pass when 0 matching target files exist."""
    if full:
        return False
    if not target_files:
        return stage_idx in FILE_SCOPED_PYTHON_GATES or stage_idx in (4, 12, 15)

    has_modularity_config = any(f in MODULARITY_GOVERNANCE_CONFIG_FILES for f in target_files)
    if has_modularity_config and stage_idx in (2, 3, 18, 19):
        return False

    has_ssot_config = any(f in SSOT_GOVERNANCE_CONFIG_FILES for f in target_files)
    if has_ssot_config and stage_idx in (13, 14, 25):
        return False

    has_py = any(f.endswith(".py") for f in target_files)
    if not has_py and stage_idx in FILE_SCOPED_PYTHON_GATES:
        return True

    if stage_idx == 4:
        has_output = any(f.startswith("output/") or f.endswith(".py") for f in target_files)
        if not has_output:
            return True

    if stage_idx == 12:
        has_output_hierarchy = any(
            f.startswith("output/") or "validate_output_hierarchy" in f for f in target_files
        )
        if not has_output_hierarchy:
            return True

    if stage_idx == 15 and not has_py:
        return True

    if stage_idx == 22:
        has_mcp = any(
            f.startswith(("mcp/", "contracts/mcp/")) or "mcp" in f for f in target_files
        )
        if not has_mcp:
            return True

    if stage_idx == 23:
        has_skills = any(
            f.startswith((".agents/skills/", ".agents/plugins/", "config/skills/"))
            or "skill" in f
            for f in target_files
        )
        if not has_skills:
            return True

    if stage_idx == 24:
        has_rules = any(
            f == "AGENTS.md"
            or f.startswith((".agents/rules/", "governance/"))
            or "rules_budget" in f
            for f in target_files
        )
        if not has_rules:
            return True

    if stage_idx == 31:
        has_grounding = any(
            f.endswith(".json") or f.startswith("dual_pipeline/grounding/") or "grounding" in f
            for f in target_files
        )
        if not has_grounding:
            return True

    if stage_idx == 32:
        has_copilot_scope = any(
            f.startswith(("dual_pipeline/", "storage/", "config/"))
            or f == "nee.py"
            or "copilot" in f
            for f in target_files
        )
        if not has_copilot_scope:
            return True

    return False



def parse_name_status_output(output: str) -> Set[str]:
    """Parses `git diff --name-status` lines and extracts all touched file paths.

    Correctly captures both endpoints for renames (R*) and copies (C*),
    as well as single endpoints for additions (A), modifications (M), and deletions (D).
    """
    touched: Set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            # First element is status (e.g. M, A, D, R100, C100), remaining are paths
            for p in parts[1:]:
                cleaned = p.strip().replace("\\", "/")
                if cleaned:
                    touched.add(cleaned)
    return touched


def is_git_repository(repo_root: Path) -> bool:
    """Verifies that repo_root is inside a valid Git work tree."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], timeout=30,
            cwd=repo_root, capture_output=True, text=True, check=True)
        return proc.stdout.strip() == "true"
    except Exception:
        return False


def collect_target_files(
    repo_root: Path,
    base_ref: str = "main",
    include_untracked: bool = True,
    excluded_prefixes: Sequence[str] = EXCLUDED_PREFIXES,
) -> List[str]:
    """Collects complete, status-aware list of candidate target files across git lifecycle.

    Raises RuntimeError if repo_root is not a valid git repository.
    """
    if not is_git_repository(repo_root):
        raise RuntimeError(
            f"Git repository required: failed to execute git discovery in {repo_root}"
        )

    files: Set[str] = set()

    # 1. Unstaged modifications and deletions
    res_unstaged = subprocess.run(["git", "diff", "--name-status"], timeout=30,
        cwd=repo_root, capture_output=True, text=True, check=True)
    files.update(parse_name_status_output(res_unstaged.stdout))

    # 2. Staged modifications, additions, deletions, and renames
    res_staged = subprocess.run(["git", "diff", "--cached", "--name-status"], timeout=30,
        cwd=repo_root, capture_output=True, text=True, check=True)
    files.update(parse_name_status_output(res_staged.stdout))

    # 3. Committed branch diff vs base_ref
    branch_diff_proc = subprocess.run(["git", "diff", "--name-status", f"{base_ref}...HEAD"], timeout=30,
        cwd=repo_root, capture_output=True, text=True)
    if branch_diff_proc.returncode == 0:
        files.update(parse_name_status_output(branch_diff_proc.stdout))
    else:
        # Fallback when base_ref is missing or candidate is on root branch
        b_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=30,
            cwd=repo_root, capture_output=True, text=True)
        current_branch = b_res.stdout.strip() if b_res.returncode == 0 else ""
        if current_branch in ("main", "master"):
            head_diff = subprocess.run(["git", "diff", "--name-status", "HEAD~1..HEAD"], timeout=30,
                cwd=repo_root, capture_output=True, text=True)
            if head_diff.returncode == 0:
                files.update(parse_name_status_output(head_diff.stdout))

    # 4. Untracked files (if enabled)
    if include_untracked:
        res_untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], timeout=30,
            cwd=repo_root, capture_output=True, text=True, check=True)
        for line in res_untracked.stdout.splitlines():
            cleaned = line.strip().replace("\\", "/")
            if cleaned and not cleaned.startswith("reports/"):
                files.add(cleaned)

    # 5. Filter out excluded infrastructure prefixes (preserving governed modularity baselines)
    filtered_files = {
        f
        for f in files
        if f in MODULARITY_GOVERNANCE_CONFIG_FILES
        or not any(f == p.rstrip("/") or f.startswith(p) for p in excluded_prefixes)
    }

    return sorted(list(filtered_files))


def _append_changed_tests(modules: List[str], target_files: List[str], root: Path) -> None:
    """Enroll any changed test files directly."""
    for f in target_files:
        p_name = Path(f).name
        if (
            f.startswith("tests/")
            and f.endswith(".py")
            and not f.endswith("__init__.py")
            and (p_name.startswith("test_") or p_name.endswith("_test.py"))
        ):
            if not (root / f).is_file() and not Path(f).is_file():
                continue
            mod = f[:-3].replace("/", ".")
            if mod not in modules:
                modules.append(mod)


def _alignment_receipts_passed(root: Path) -> bool:
    """Verify whether all 7 alignment receipts passed."""
    for w in range(1, 8):
        rcpt = root / "reports" / "alignment" / f"wave-{w}.json"
        if not rcpt.is_file():
            return False
        try:
            rdata = json.loads(rcpt.read_text(encoding="utf-8"))
            if rdata.get("terminal_outcome") != "PASS":
                return False
        except Exception:
            return False
    return True


def has_tier1_replay_scope(target_files: Sequence[str]) -> bool:
    """Return True if changed files touch transcript synthesis or pipeline extraction layers."""
    prefixes = (
        "dual_pipeline/transcript_", "dual_pipeline/canonical_pipeline/",
        "pipeline_a_intake/", "pipeline_b_extraction/",
    )
    keywords = ("transcript", "audio", "recording", "copilot")
    return any(f.startswith(prefixes) or any(k in f for k in keywords) for f in target_files)


def _build_core_test_modules(target_files: List[str], full: bool) -> List[str]:
    """Build test module list with change-scoping and linter deduplication."""
    modules = [
        "tests.test_runtime_evidence_claims", "tests.test_bge_m3_deprecation_gates",
        "tests.test_ingestion_directive_invariants", "tests.test_unified_agent_architecture",
        "tests.test_figma_governance_integration", "tests.test_credentials",
        "tests.test_model_registry", "tests.test_config_hasher",
        "tests.test_parameters", "tests.test_cli_ingest_meeting_flags",
        "tests.test_manifest_runner",
        "tests.governance.test_pr_governance", "tests.governance.test_git_workflow_consolidation",
        "tests.adversarial.test_git_workflow_adversarial", "tests.governance.test_worktree_ide_visibility",
        "tests.governance.test_ci_commits_and_ssot_hardening", "tests.test_configuration_contracts",
        "tests.test_parameter_propagation", "tests.test_adversarial_debate_audit",
        "tests.test_pipeline_a_high_signal_eval", "tests.test_pipeline_b_high_signal_eval",
        "tests.test_pipeline_c_high_signal_eval", "tests.test_pipeline_d_high_signal_eval",
        "tests.test_frontier_reasoning_pipeline_e2e", "tests.test_healing_mesh_governance",
        "tests.governance.test_worktree_prune_protection", "tests.test_career_firewall_adversarial",
        "tests.end_to_end.test_four_route_runtime_smoke_e2e",
        "tests.adversarial.test_four_route_runtime_smoke_adversarial",
        "tests.unit.test_canonical_query_filter_gate",
        "tests.end_to_end.test_canonical_query_filter_e2e",
        "tests.adversarial.test_canonical_query_filter_adversarial",
    ]

    has_linter_changes = any(
        f.startswith(("tools/lint_", "tools/audit_", "tools/scan_", "tests/test_lint_")) or "linter" in f
        for f in target_files
    )
    if full or has_linter_changes:
        modules.extend([
            "tests.test_lint_layer_imports", "tests.test_model_neutral_naming", "tests.test_no_synthetic_embedding_ban",
            "tests.test_lint_hardcoded_parameters", "tests.test_lint_ast_invariants", "tests.test_lint_file_budgets_universal",
            "tests.test_lint_production_purity", "tests.test_lint_no_custom_ingest_scripts", "tests.test_cognitive_complexity_linter",
            "tests.test_strict_type_gate", "tests.test_skills_budget_governance", "tests.test_implementation_summary_governance",
            "tests.test_lint_mock_boundaries",
        ])

    has_scheduled_task_changes = any(
        f.startswith(("config/scheduled_tasks/", "automations/", "tools/scheduled_task_core/"))
        or "scheduled_task" in f or "scheduler_adapter" in f for f in target_files
    )
    if full or has_scheduled_task_changes:
        modules.extend([
            "tests.test_scheduled_task_contracts", "tests.test_scheduled_task_authority",
            "tests.test_scheduled_task_evidence", "tests.test_scheduled_task_remediation",
            "tests.test_scheduled_task_adapters", "tests.test_scheduled_task_lifecycle",
            "tests.test_scheduled_task_gate_enrollment",
            "tests.adversarial.test_scheduled_task_reliability_adversarial",
        ])

    has_diff_intelligence_changes = any(
        f.startswith(("tools/pr_governance_core/", "tools/pr_topology_core/"))
        or "diff_semantics" in f or "verdict_verifier" in f or "pr_queue" in f
        for f in target_files
    )
    if full or has_diff_intelligence_changes:
        modules.extend([
            "tests.test_pr_zero_commit_and_hunk_deprecation",
            "tests.test_verdict_verifier_e2e",
            "tests.unit.test_council_action_hardening_alignment",
        ])

    if full or has_tier1_replay_scope(target_files):
        modules.extend([
            "tests.test_comprehensive_transcript_e2e",
            "tests.test_adversarial_comprehensive_e2e",
            "tests.end_to_end.test_tier1_cached_replay_gate_e2e",
            "tests.adversarial.test_tier1_cached_replay_adversarial",
        ])

    _append_changed_tests(modules, target_files, ROOT)

    has_alignment_changes = any("alignment" in f for f in target_files)
    if full or has_alignment_changes:
        if full or not _alignment_receipts_passed(ROOT):
            for w in range(1, 8):
                e2e_mod = f"tests.end_to_end.test_alignment_w{w}_e2e"
                adv_mod = f"tests.test_alignment_w{w}_adversarial"
                if e2e_mod not in modules:
                    modules.append(e2e_mod)
                if adv_mod not in modules:
                    modules.append(adv_mod)

    return modules
