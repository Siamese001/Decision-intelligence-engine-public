"""
Code Review Rules and Taxonomy for Governed PR Queue (Wave 3).
Defines ChangeClass, ReviewVerdict, DeterministicSignals, and rule evaluation logic.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class ChangeClass(str, Enum):
    PRODUCTION_CODE = "production_code"
    TEST_CODE = "test_code"
    DOCUMENTATION = "documentation"
    GENERATED_ARTIFACTS = "generated_artifacts"
    GOVERNANCE_RECEIPTS = "governance_receipts"
    SCRATCH_FILES = "scratch_files"
    CONFIG = "config"
    OTHER = "other"


class ReviewVerdict(str, Enum):
    MERGE_READY = "MERGE_READY"
    REVIEW_READY = "REVIEW_READY"
    RESOLVE_CONFLICTS = "RESOLVE_CONFLICTS"
    REBASE_RETARGET = "REBASE_RETARGET"
    GOVERNANCE_BLOCK = "GOVERNANCE_BLOCK"
    SPLIT_REQUIRED = "SPLIT_REQUIRED"
    SUPERSEDED_TOMBSTONE = "SUPERSEDED_TOMBSTONE"


GENERATED_ARTIFACT_NAMES: Set[str] = {
    "test_timing_db.json",
    "testing_gaps_audit_report.json",
    "qa_gates_report.json",
    "llm_judge_evaluation.json",
    "final_release_decision.json",
    "intake_package.json",
}

SCRATCH_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"^revert_ruff.*"),
    re.compile(r"^probe_cache\.py$"),
    re.compile(r"^inject_align\.py$"),
    re.compile(r"^fix_registry\.py$"),
    re.compile(r"^patch_output2_contract\.py$"),
    re.compile(r"^scratch/.*"),
    re.compile(r"^tmp/.*"),
]


def classify_file_path(file_path: str) -> ChangeClass:
    """Classifies a repo relative path into one of the closed ChangeClass categories."""
    p = Path(file_path)
    filename = p.name

    # Scratch files
    for pat in SCRATCH_FILE_PATTERNS:
        if pat.match(file_path):
            return ChangeClass.SCRATCH_FILES

    # Receipts
    if p.parts and p.parts[0] == "reports" and (
        filename.endswith("_receipt.json")
        or "readiness" in p.parts
        or filename.startswith("pr_review_")
        or filename.startswith("merge_")
    ):
        return ChangeClass.GOVERNANCE_RECEIPTS

    # Generated artifacts
    if filename in GENERATED_ARTIFACT_NAMES:
        return ChangeClass.GENERATED_ARTIFACTS

    if p.parts and p.parts[0] == "output" and filename.endswith(".json"):
        return ChangeClass.GENERATED_ARTIFACTS

    # Governance code & policies (never documentation)
    if p.parts and p.parts[0] == "governance":
        return ChangeClass.PRODUCTION_CODE

    # Core agents, AGENTS.md, and configuration
    if filename == "AGENTS.md" or (p.parts and p.parts[0] in (".agents", "config", ".github")):
        return ChangeClass.CONFIG

    # Tests
    if (
        (p.parts and p.parts[0] in ("tests", "retrieval_tests"))
        or filename.startswith("test_")
        or filename.endswith("_test.py")
        or "_test_" in filename
    ):
        return ChangeClass.TEST_CODE

    # Executable prompts and prompt contracts (never documentation)
    if "prompt" in filename.lower() or (p.parts and "prompts" in p.parts):
        prod_roots = {
            "dual_pipeline",
            "storage",
            "unified_pipeline",
            "pipeline_a_intake",
            "pipeline_b_extraction",
            "agentic_core",
            "system_learning",
            "ui",
            "governance",
            "tools",
        }
        if p.parts and p.parts[0] in prod_roots:
            return ChangeClass.PRODUCTION_CODE
        return ChangeClass.CONFIG

    # Documentation (strictly non-governance, non-prompt documentation)
    if (
        filename.endswith((".md", ".rst", ".txt"))
        or (p.parts and p.parts[0] in ("docs", "plans", "documentation"))
        or filename == "implementation_plan.md"
    ):
        return ChangeClass.DOCUMENTATION

    # Config
    if (
        filename in (".gitignore", ".gitattributes", "pyproject.toml", "requirements.txt")
        or filename.endswith((".yaml", ".yml", ".toml"))
    ):
        return ChangeClass.CONFIG

    # Production code
    prod_roots = {
        "dual_pipeline",
        "storage",
        "unified_pipeline",
        "pipeline_a_intake",
        "pipeline_b_extraction",
        "agentic_core",
        "system_learning",
        "ui",
        "tools",
    }
    if p.parts and p.parts[0] in prod_roots:
        return ChangeClass.PRODUCTION_CODE

    if filename.endswith(".py"):
        return ChangeClass.PRODUCTION_CODE

    return ChangeClass.OTHER


@dataclass
class DeterministicSignals:
    # S1: Numstat
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    binary_files: int = 0

    # S2: Change classes count
    class_counts: Dict[str, int] = field(default_factory=dict)
    class_lines: Dict[str, int] = field(default_factory=dict)

    # S3: Trivial / whitespace / formatting
    is_whitespace_or_import_only: bool = False
    substantive_files_changed: int = 0

    # S4: Commits in main (cherry)
    total_commits: int = 0
    commits_in_main: int = 0
    unique_commits: int = 0
    unique_production_commits: int = 0
    patch_equivalent_ratio: float = 0.0

    # S5: Per-commit footprint
    max_files_in_single_commit: int = 0
    has_mass_reformat_commit: bool = False

    # S6: Stale paths (paths modified by branch that main deleted, moved, or archived)
    stale_paths: List[str] = field(default_factory=list)

    # S7: Test coverage
    has_production_changes: bool = False
    has_test_changes: bool = False
    missing_test_coverage: bool = False

    # S8: Governance hazards
    governance_hazards: List[str] = field(default_factory=list)

    # S9: Name vs content mismatch
    name_content_mismatch: bool = False
    mismatch_description: str = ""

    # S10: Reverts
    revert_commits: List[str] = field(default_factory=list)

    # S11: 3-dot diff coordinates
    merge_base_sha: str = ""
    main_sha: str = ""
    branch_sha: str = ""

    # S12: Per-file hunk breakdowns & AST symbols
    file_hunks: List[Dict[str, Any]] = field(default_factory=list)
    hunk_absorption_ratio: float = 0.0
    modified_ast_symbols: List[str] = field(default_factory=list)
    symbol_test_mappings: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BranchCodeReview:
    branch: str
    verdict: ReviewVerdict
    verdict_reason: str
    recommended_action: str
    signals: DeterministicSignals
    evidence_digest: str = ""
    llm_intent_summary: Optional[str] = None
    semantic_review: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def evaluate_review_verdict(
    signals: DeterministicSignals,
    is_clean_merge: bool,
    branch_name: str,
) -> Tuple[ReviewVerdict, str, str]:
    """
    Evaluates deterministic signals to produce an authoritative closed ReviewVerdict.
    Rules evaluated in priority order:
    1. SPLIT_REQUIRED: Single commit touching >300 files, or mixed mass reformat with feature.
    2. GOVERNANCE_BLOCK: Unsigned ledger entries, direct SQL execution.
    3. SUPERSEDED_TOMBSTONE: Net substantive code is 0, all code commits in main, or whitespace already fixed in main.
    4. REBASE_RETARGET: Stale paths (imports or files moved to archive/deleted by main).
    5. RESOLVE_CONFLICTS: Unclean merge.
    6. REVIEW_READY / MERGE_READY: Clean merge, normal signals.
    """
    # 1. Split required (structural block takes precedence)
    if signals.has_mass_reformat_commit or (
        signals.files_changed > 300 and signals.class_counts.get(ChangeClass.SCRATCH_FILES.value, 0) > 0
    ):
        return (
            ReviewVerdict.SPLIT_REQUIRED,
            f"Oversized multi-concern branch: single commit touched {signals.max_files_in_single_commit} files",
            f"[SPLIT_REQUIRED] Separate mass reformat/scratch scripts from core feature commits ({signals.files_changed} files total)",
        )

    # 2. Governance block
    if signals.governance_hazards:
        reasons = "; ".join(signals.governance_hazards)
        return (
            ReviewVerdict.GOVERNANCE_BLOCK,
            f"Governance violations detected: {reasons}",
            f"[GOVERNANCE_BLOCK] Remediate {len(signals.governance_hazards)} hazard(s) before proceeding",
        )

    # 3. Superseded / Tombstone
    prod_count = signals.class_counts.get(ChangeClass.PRODUCTION_CODE.value, 0)
    if signals.is_whitespace_or_import_only and prod_count > 0:
        return (
            ReviewVerdict.SUPERSEDED_TOMBSTONE,
            "Branch diff contains only whitespace or formatting modifications",
            "[SUPERSEDED_TOMBSTONE] Close branch; whitespace/formatting changes are already satisfied or obsolete",
        )

    # If all production code commits are already in main (remaining unique commits are only docs or generated reports)
    if signals.commits_in_main > 0 and signals.unique_production_commits == 0:
        return (
            ReviewVerdict.SUPERSEDED_TOMBSTONE,
            f"All production code commits already absorbed into main ({signals.commits_in_main}/{signals.total_commits} commits in main; remaining commits are docs/artifacts)",
            "[SUPERSEDED_TOMBSTONE] All code changes present in main; archive plan doc if needed and retire branch",
        )

    # High hunk absorption (>=85% of code diff hunks already in main)
    if signals.hunk_absorption_ratio >= 0.85:
        pct = int(signals.hunk_absorption_ratio * 100)
        return (
            ReviewVerdict.SUPERSEDED_TOMBSTONE,
            f"Code changes already absorbed into main ({pct}% hunk absorption across modified files)",
            f"[SUPERSEDED_TOMBSTONE] Close branch; {pct}% of code diff is already present in main",
        )

    # 4. Rebase / Retarget
    if signals.stale_paths:
        stale_str = ", ".join(signals.stale_paths[:3])
        if len(signals.stale_paths) > 3:
            stale_str += f" (+{len(signals.stale_paths) - 3} more)"
        return (
            ReviewVerdict.REBASE_RETARGET,
            f"Branch touches paths deleted or archived by main since fork: {stale_str}",
            f"[REBASE_RETARGET] Rebase onto main and retarget imports for {len(signals.stale_paths)} archived/deleted path(s)",
        )

    # 5. Resolve conflicts
    if not is_clean_merge:
        return (
            ReviewVerdict.RESOLVE_CONFLICTS,
            "Branch has merge conflicts against main",
            "[RESOLVE_CONFLICTS] Rebase onto main and resolve file conflicts",
        )

    # Partial hunk absorption (30% - 84% absorbed) -> Salvage subset
    if 0.30 <= signals.hunk_absorption_ratio < 0.85:
        pct = int(signals.hunk_absorption_ratio * 100)
        return (
            ReviewVerdict.REVIEW_READY,
            f"Partial hunk absorption detected ({pct}% absorbed into main)",
            f"[SALVAGE_DELIVERABLES] Salvage remaining {100 - pct}% unabsorbed changes; {pct}% already absorbed into main",
        )

    # 6. Missing test coverage warning vs ready
    if signals.missing_test_coverage:
        return (
            ReviewVerdict.REVIEW_READY,
            f"Production code modified ({prod_count} file(s)) without accompanying tests",
            "[REVIEW_READY] Manual review required: add regression tests for modified production modules",
        )

    return (
        ReviewVerdict.MERGE_READY,
        "Clean merge, test coverage verified, zero governance hazards",
        "[MERGE_READY] Approved for merge queue",
    )
