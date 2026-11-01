"""tools/readiness_planner.py

Versioned Gate Manifest & Proportional Readiness Stage Planner.
Connects validated risk classification to qualification gates:
- Lane 0: Fast hygiene linters, secret scan, format/plan validators, and affected smoke tests.
- Lane 1: Hygiene, code architecture, secret scan, contract validation, and impacted behavioral tests.
- Lane 2: All 32 registered gates (storage, releases, ingestion, HITL ledger, governance security controls).
- Fail-closed fallback: Any unrecognized path or conservative fallback escalates to Lane 2.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from tools.change_lane_classifier import classify_changes

DEFAULT_GATE_MANIFEST = Path("config/governance/gate_manifest.json")


@dataclass
class ReadinessPlan:
    risk_lane: int
    risk_lane_name: str
    touches_lane_2: bool
    manifest_version: str
    required_stage_indices: Set[int]
    total_registered_stages: int
    executed_stages_count: int
    exempt_stages_count: int
    proportional_applied: bool
    test_mode: str
    fallback_reason: Optional[str] = None
    exempt_stage_names: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_lane": self.risk_lane,
            "risk_lane_name": self.risk_lane_name,
            "touches_lane_2": self.touches_lane_2,
            "manifest_version": self.manifest_version,
            "required_stage_indices": sorted(list(self.required_stage_indices)),
            "total_registered_stages": self.total_registered_stages,
            "executed_stages_count": self.executed_stages_count,
            "exempt_stages_count": self.exempt_stages_count,
            "proportional_applied": self.proportional_applied,
            "test_mode": self.test_mode,
            "fallback_reason": self.fallback_reason,
            "exempt_stage_names": self.exempt_stage_names,
        }


ProportionalPlan = ReadinessPlan


def load_gate_manifest(manifest_path: Optional[Path] = None, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Loads the versioned gate manifest from repository governance config."""
    p = manifest_path or (
        (repo_root / DEFAULT_GATE_MANIFEST) if repo_root else DEFAULT_GATE_MANIFEST
    )
    if not p.is_file():
        # Fallback default: all 32 stages required
        all_32 = list(range(1, 33))
        return {
            "version": "1.0.0-fallback",
            "total_registered_stages": 32,
            "lane_profiles": {
                "0": {"name": "LANE_0_LOW_RISK", "required_stage_indices": [1, 6, 17, 20, 27, 28], "behavioral_test_mode": "smoke_only"},
                "1": {"name": "LANE_1_MEDIUM_RISK", "required_stage_indices": [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30, 31, 32], "behavioral_test_mode": "impacted_only"},
                "2": {"name": "LANE_2_HIGH_RISK", "required_stage_indices": all_32, "behavioral_test_mode": "full_suite"},
            },
            "stages": [{"index": i, "name": f"Stage {i}", "min_lane": 2} for i in range(1, 33)],
        }

    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        all_32 = list(range(1, 33))
        return {
            "version": "1.0.0-error-fallback",
            "total_registered_stages": 32,
            "lane_profiles": {
                "2": {"name": "LANE_2_HIGH_RISK", "required_stage_indices": all_32, "behavioral_test_mode": "full_suite"},
            },
            "stages": [{"index": i, "name": f"Stage {i}", "min_lane": 2} for i in range(1, 33)],
        }


def _build_full_fallback_plan(version: str, total_stages: int, reason: str) -> Tuple[ReadinessPlan, List[int]]:
    all_indices = set(range(1, total_stages + 1))
    plan = ReadinessPlan(
        risk_lane=2, risk_lane_name="LANE_2_HIGH_RISK", touches_lane_2=True,
        manifest_version=version, required_stage_indices=all_indices,
        total_registered_stages=total_stages, executed_stages_count=total_stages,
        exempt_stages_count=0, proportional_applied=False, test_mode="full_suite",
        fallback_reason=reason,
    )
    return plan, sorted(list(all_indices))


def plan_readiness(
    target_files: Sequence[str],
    repo_root: Optional[Path] = None,
    force_full: bool = False,
    manifest_path: Optional[Path] = None,
) -> Tuple[ReadinessPlan, List[int]]:
    """Evaluates target files against risk lanes and gate manifest to produce an optimal execution plan."""
    manifest_data = load_gate_manifest(manifest_path, repo_root=repo_root)
    version = manifest_data.get("version", "1.0.0")
    total_stages = int(manifest_data.get("total_registered_stages", 32))
    lane_profiles = manifest_data.get("lane_profiles", {})

    env_disabled = os.environ.get("PROPORTIONAL_GATES_DISABLED", "").strip().lower() in ("1", "true", "yes")
    if force_full or not target_files or env_disabled:
        reason = "Proportional gates disabled via PROPORTIONAL_GATES_DISABLED environment variable" if env_disabled else "Explicit full qualification or empty target files list"
        return _build_full_fallback_plan(version, total_stages, reason)

    lane_result = classify_changes(target_files)
    effective_lane = int(lane_result.get("effective_lane", 2))
    lane_name = lane_result.get("effective_lane_name", "LANE_2_HIGH_RISK")
    touches_lane_2 = bool(lane_result.get("touches_lane_2", True))

    str_lane = str(effective_lane)
    profile = lane_profiles.get(str_lane)
    if not profile:
        return _build_full_fallback_plan(version, total_stages, f"Unknown lane profile '{str_lane}'; fail-closed fallback to Lane 2")

    req_indices = set(int(x) for x in profile.get("required_stage_indices", range(1, total_stages + 1)))
    if effective_lane == 1 and any(
        f.startswith(("contracts/", "schemas/")) or "prompt" in f.lower() or "contract" in f.lower()
        for f in target_files
    ):
        req_indices.update({10, 11, 25})
    test_mode = profile.get("behavioral_test_mode", "full_suite")
    stages_meta = manifest_data.get("stages", [])
    exempt_names = [s.get("name", f"Stage {s.get('index')}") for s in stages_meta if int(s.get("index", 0)) not in req_indices]

    is_proportional = (effective_lane < 2) and (len(req_indices) < total_stages)
    plan = ReadinessPlan(
        risk_lane=effective_lane, risk_lane_name=lane_name, touches_lane_2=touches_lane_2,
        manifest_version=version, required_stage_indices=req_indices,
        total_registered_stages=total_stages, executed_stages_count=len(req_indices),
        exempt_stages_count=total_stages - len(req_indices),
        proportional_applied=is_proportional, test_mode=test_mode, exempt_stage_names=exempt_names,
    )
    return plan, sorted(list(req_indices))


RUNNER_AND_POLICY_PREFIXES: tuple[str, ...] = (
    "tools/run_tests",
    "tools/readiness",
    "tools/change_lane_classifier",
    "tools/scope_collector",
    "tools/readiness_scope_collector",
    "tools/test_discovery",
    "config/governance/",
    "governance/",
    "conftest.py",
    "tests/conftest.py",
    "pyproject.toml",
)

PACKAGE_TEST_MAPPING: dict[str, list[str]] = {
    "dual_pipeline/": [
        "tests.test_pipeline_a_high_signal_eval",
        "tests.test_pipeline_b_high_signal_eval",
        "tests.test_frontier_reasoning_pipeline_e2e",
        "tests.test_healing_mesh_governance",
    ],
    "storage/": [
        "tests.test_credentials",
        "tests.test_parameters",
        "tests.test_config_hasher",
    ],
    "config/": [
        "tests.test_configuration_contracts",
        "tests.test_parameters",
        "tests.test_config_hasher",
    ],
    "tools/pr_governance": [
        "tests.governance.test_pr_governance",
        "tests.governance.test_git_workflow_consolidation",
        "tests.adversarial.test_git_workflow_adversarial",
    ],
    "tools/lint_": [
        "tests.test_lint_layer_imports",
        "tests.test_cognitive_complexity_linter",
        "tests.test_strict_type_gate",
    ],
    "tools/coverage_report_reuse": [
        "tests.governance.test_local_coverage_report_reuse",
    ],
    "tools/attestation_core/": [
        "tests.test_runtime_evidence_claims",
        "tests.governance.test_pr_governance",
    ],
    "tools/audit_": [
        "tests.test_cognitive_complexity_linter",
        "tests.test_skills_budget_governance",
    ],
    "tools/benchmark_pr_promotion": [
        "tests.end_to_end.test_pr_latency_benchmark_e2e",
        "tests.test_pr_latency_benchmark_adversarial",
    ],
}

SAFE_NON_CODE_PREFIXES: tuple[str, ...] = (
    "docs/",
    "reports/",
    "plans/",
    ".agents/",
    "output/",
    "documents/",
    "documentation/",
)


def _dedup_list(items: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _collect_impacted_for_file(f: str, selected: List[str]) -> bool:
    is_known = f.startswith("tests/") or f.endswith(".md") or any(f.startswith(p) for p in SAFE_NON_CODE_PREFIXES)
    for prefix, tests in PACKAGE_TEST_MAPPING.items():
        if f.startswith(prefix):
            is_known = True
            for t in tests:
                if t not in selected:
                    selected.append(t)
    return is_known


def filter_impacted_test_modules(
    target_files: Sequence[str],
    test_mode: str,
    base_modules: Sequence[str],
    repo_root: Optional[Path] = None,
) -> List[str]:
    """Filters behavioral test modules based on change risk profile and impact mapping."""
    deduped_base = _dedup_list(base_modules)
    if test_mode == "full_suite" or not target_files:
        return deduped_base
    if any(any(f == p or f.startswith(p) for p in RUNNER_AND_POLICY_PREFIXES) for f in target_files):
        return deduped_base

    selected: List[str] = [
        "tests.test_runtime_evidence_claims",
        "tests.test_unified_agent_architecture",
        "tests.test_credentials",
    ]
    for f in target_files:
        if f.startswith("tests/") and f.endswith(".py") and not f.endswith("__init__.py"):
            mod = f[:-3].replace("/", ".")
            if mod not in selected:
                selected.append(mod)

    if test_mode == "smoke_only":
        return _dedup_list(selected)

    from tools.ast_dependency_cone import resolve_impacted_tests

    for tmod in resolve_impacted_tests(target_files, repo_root=repo_root):
        if tmod not in selected:
            selected.append(tmod)

    for f in target_files:
        if not _collect_impacted_for_file(f, selected):
            return deduped_base
    return _dedup_list(selected)


def get_mainline_diff_files(base_sha: str, current_base_sha: str, root: Path) -> Optional[List[str]]:
    """Retrieves file diffs between mainline base commits."""
    import subprocess
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..{current_base_sha}"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if res.returncode == 0:
            return res.stdout.splitlines()
    except Exception:
        pass
    return None


def _check_cached_base_disjoint(cached_entry: Dict[str, Any], cached_base: str, current_base_sha: str, root: Path) -> Tuple[bool, str]:
    try:
        main_diff_lines = get_mainline_diff_files(cached_base, current_base_sha, root)
    except Exception:
        main_diff_lines = None
    if main_diff_lines is None:
        return False, f"Base commit SHA mismatch: {cached_base} != {current_base_sha}"

    touched_files = set(cached_entry.get("target_files", []))
    for stage in cached_entry.get("stages", []):
        touched_files.update(stage.get("dependencies", []))
    if not touched_files:
        return False, f"Base commit SHA mismatch: {cached_base} != {current_base_sha}"
    if set(main_diff_lines).isdisjoint(touched_files):
        return True, "DISJOINT"
    return False, f"Base commit SHA mismatch with intersecting mainline changes: {cached_base} != {current_base_sha}"


def validate_readiness_cache_binding(
    cached_entry: Dict[str, Any],
    current_head_sha: str,
    current_base_sha: str,
    current_manifest_version: str,
    is_live_mode: bool = True,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Validates that a readiness cache entry strictly binds to inputs, tree, policy, and mode."""
    if not isinstance(cached_entry, dict):
        return False, "Corrupt checkpoint: cache entry is not a dictionary"
    if (cached_entry.get("terminal_outcome") or cached_entry.get("status")) != "PASS":
        return False, "Failure is never reusable as a passing cache entry"

    cached_head = cached_entry.get("head_commit_sha") or cached_entry.get("head_sha") or cached_entry.get("commit_sha")
    if cached_head != current_head_sha:
        return False, f"Head commit SHA mismatch: {cached_head} != {current_head_sha}"

    cached_base = (
        cached_entry.get("base_commit_sha") or cached_entry.get("base_sha")
        or cached_entry.get("main_base_sha") or cached_entry.get("base")
    )
    if cached_base and cached_base != current_base_sha:
        root = repo_root or Path(__file__).resolve().parents[1]
        ok, msg = _check_cached_base_disjoint(cached_entry, str(cached_base), current_base_sha, root)
        if not ok:
            return False, msg

    cached_ver = cached_entry.get("manifest_version")
    if cached_ver and cached_ver != current_manifest_version:
        return False, f"Policy manifest version mismatch: {cached_ver} != {current_manifest_version}"

    cached_is_live = bool(cached_entry.get("is_live", not cached_entry.get("is_mock", False)))
    if cached_is_live != is_live_mode:
        mode_str = "live" if is_live_mode else "mock"
        return False, f"Execution mode mismatch: cannot reuse cache across live/mock boundary ({mode_str})"
    return True, "VALID_REUSE"


SHARED_BASELINE_FILES: tuple[str, ...] = (
    "reports/file_budget_burndown_baseline.json",
    "config/governance/file_budget_exclusions.json",
    "config/governance/module_budget_policy.json",
    "tools/layer_hierarchy_manifest.json",
    "config/governance/gate_manifest.json",
)


def check_disjoint_base_advance_equivalence(
    old_base_sha: str,
    new_base_sha: str,
    branch_files: Sequence[str],
    repo_root: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Verifies whether mainline commits landed between old_base_sha and new_base_sha are disjoint from branch_files."""
    if not old_base_sha or not new_base_sha:
        return False, "Missing base SHA for equivalence check"
    if old_base_sha == new_base_sha:
        return True, "IDENTICAL_BASE"

    root = repo_root or Path(__file__).resolve().parents[1]
    main_diff = get_mainline_diff_files(old_base_sha, new_base_sha, root)
    if main_diff is None:
        return False, f"Unable to compute mainline diff {old_base_sha[:8]}..{new_base_sha[:8]}"

    branch_set = {f.strip() for f in branch_files if f.strip()}
    if not branch_set:
        return False, "Empty branch file scope cannot fast-path rebind"

    main_set = {f.strip() for f in main_diff if f.strip()}
    overlap = main_set & branch_set
    if overlap:
        return False, f"Intersecting modified files on main: {', '.join(sorted(overlap)[:3])}"

    for mf in main_set:
        if mf in SHARED_BASELINE_FILES or any(mf == p or mf.startswith(p) for p in RUNNER_AND_POLICY_PREFIXES):
            return False, f"Mainline modified shared policy or baseline: {mf}"

    from tools.ast_dependency_cone import check_import_cone_overlap

    has_cone_overlap, cone_overlap = check_import_cone_overlap(branch_set, main_set, repo_root=root)
    if has_cone_overlap:
        return (
            False,
            f"Overlapping AST import cone ({', '.join(sorted(cone_overlap)[:3])}); post-rebase caller verification required",
        )

    py_files = [f for f in (branch_set | main_set) if f.endswith(".py")]
    if (
        any(f.endswith(".py") for f in branch_set)
        and any(f.endswith(".py") for f in main_set)
        and not all((root / f).is_file() for f in py_files)
    ):
        return False, "Mainline and branch both modified Python modules; Stage 2 graph cycle re-verification required"
    return True, "DISJOINT_BASE_ADVANCE"


