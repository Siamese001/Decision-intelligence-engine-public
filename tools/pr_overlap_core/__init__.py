"""
PR Overlap Core: Code-Level Evidence Layer for hunks, symbols, and duplicate detection,
and pairwise merge simulation and merge/rebase order planning.

SSOT Evidence Schema URN: urn:nee:pr-overlap-evidence:1.0.0
SSOT Plan Schema URN: urn:nee:pr-overlap-plan:1.0.0
"""
from __future__ import annotations

from .cache import EvidenceCache
from .cherry_pick import (
    assess_branch_cherry_pick,
    verify_scratch_agreement,
)
from .duplicates import (
    check_identical_file_changes,
    compute_blob_sha,
    compute_commit_patch_id,
    detect_duplicate_blobs,
    detect_duplicate_commits,
)
from .engine import (
    build_evidence_report,
    compare_branches,
    extract_branch_evidence,
)
from .hunks import (
    compare_file_hunks,
    compute_normalized_hunk_hash,
    parse_unified_diff_u0,
    ranges_overlap,
)
from .models import (
    CHERRY_PICK_SCHEMA_VERSION,
    SCHEMA_VERSION,
    BranchCherryPickPlan,
    BranchEvidence,
    BranchFileSymbols,
    CherryPickClass,
    CommitCherryPickVerdict,
    CommitPatchEvidence,
    EvidenceReport,
    FileHunkDiff,
    HunkRange,
    OverlapVerdict,
    PairwiseEvidence,
    SymbolSpan,
)
from .order_planner import (
    PLAN_SCHEMA_VERSION,
    MergeOrderPlan,
    MergeStep,
    compute_repo_state_digest,
    evaluate_merge_sequence,
    plan_merge_order,
)
from .pairwise import (
    PairwiseMergeResult,
    compute_pairwise_merge_matrix,
    simulate_pairwise_merge,
)
from .rebase_plan import (
    RebaseRecommendation,
    build_rebase_recommendation,
    compute_range_diff,
    detect_stacked_lineage,
)
from .symbols import (
    analyze_file_symbols,
    extract_python_symbols,
    find_touched_symbols,
    is_python_file,
)

__all__ = [
    "CHERRY_PICK_SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "BranchCherryPickPlan",
    "BranchEvidence",
    "BranchFileSymbols",
    "CherryPickClass",
    "CommitCherryPickVerdict",
    "CommitPatchEvidence",
    "EvidenceCache",
    "EvidenceReport",
    "FileHunkDiff",
    "HunkRange",
    "MergeOrderPlan",
    "MergeStep",
    "OverlapVerdict",
    "PairwiseEvidence",
    "PairwiseMergeResult",
    "RebaseRecommendation",
    "SymbolSpan",
    "analyze_file_symbols",
    "assess_branch_cherry_pick",
    "build_evidence_report",
    "build_rebase_recommendation",
    "check_identical_file_changes",
    "compare_branches",
    "compare_file_hunks",
    "compute_blob_sha",
    "compute_commit_patch_id",
    "compute_normalized_hunk_hash",
    "compute_pairwise_merge_matrix",
    "compute_range_diff",
    "compute_repo_state_digest",
    "detect_duplicate_blobs",
    "detect_duplicate_commits",
    "detect_stacked_lineage",
    "evaluate_merge_sequence",
    "extract_branch_evidence",
    "extract_python_symbols",
    "find_touched_symbols",
    "is_python_file",
    "parse_unified_diff_u0",
    "plan_merge_order",
    "ranges_overlap",
    "simulate_pairwise_merge",
    "verify_scratch_agreement",
]
