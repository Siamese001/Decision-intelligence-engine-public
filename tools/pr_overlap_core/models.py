"""
Typed data models and schema definitions for PR overlap and code review evidence.

SSOT Schema URN: urn:nee:pr-overlap-evidence:1.0.0
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "urn:nee:pr-overlap-evidence:1.0.0"


class OverlapVerdict(str, Enum):
    """Categorical classification of pairwise branch overlap."""
    IDENTICAL = "IDENTICAL"
    DUPLICATE_OF = "DUPLICATE_OF"
    OVERLAPPING_HUNKS = "OVERLAPPING_HUNKS"
    SAME_SYMBOL_DISJOINT_HUNKS = "SAME_SYMBOL_DISJOINT_HUNKS"
    DISJOINT_HUNKS = "DISJOINT_HUNKS"
    DISJOINT_FILES = "DISJOINT_FILES"


@dataclass
class HunkRange:
    """Represents a single unified diff hunk header coordinate range."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    hunk_header: str = ""

    @property
    def old_end(self) -> int:
        """1-based inclusive end line in the base file."""
        return self.old_start + max(0, self.old_lines - 1)

    @property
    def new_end(self) -> int:
        """1-based inclusive end line in the target file."""
        return self.new_start + max(0, self.new_lines - 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "old_start": self.old_start,
            "old_lines": self.old_lines,
            "new_start": self.new_start,
            "new_lines": self.new_lines,
            "old_end": self.old_end,
            "new_end": self.new_end,
            "hunk_header": self.hunk_header,
        }


@dataclass
class SymbolSpan:
    """Represents an AST symbol (function, class, method) and its line span."""
    symbol_name: str
    symbol_type: str  # function, class, async_function, method
    start_line: int
    end_line: int
    parent_symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileHunkDiff:
    """Represents hunk and blob metadata for a single changed file in a branch."""
    path: str
    status: str  # A, M, D, R
    old_path: Optional[str] = None
    hunks: List[HunkRange] = field(default_factory=list)
    blob_sha: Optional[str] = None
    hunk_hashes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "old_path": self.old_path,
            "hunks": [h.to_dict() for h in self.hunks],
            "blob_sha": self.blob_sha,
            "hunk_hashes": self.hunk_hashes,
        }


@dataclass
class BranchFileSymbols:
    """Represents symbol inventory and touched symbols for a file in a branch."""
    path: str
    all_symbols: List[SymbolSpan] = field(default_factory=list)
    touched_symbols: List[SymbolSpan] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "all_symbols": [s.to_dict() for s in self.all_symbols],
            "touched_symbols": [s.to_dict() for s in self.touched_symbols],
        }


@dataclass
class CommitPatchEvidence:
    """Represents a single commit's patch-id and metadata."""
    commit_sha: str
    patch_id: str
    subject: str
    author: str = ""
    timestamp: str = ""
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BranchEvidence:
    """Complete code-level evidence for a single branch against merge base."""
    branch: str
    merge_base: str
    head_sha: str
    commit_count: int
    commits: List[CommitPatchEvidence] = field(default_factory=list)
    files: Dict[str, FileHunkDiff] = field(default_factory=dict)
    symbols: Dict[str, BranchFileSymbols] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "merge_base": self.merge_base,
            "head_sha": self.head_sha,
            "commit_count": self.commit_count,
            "commits": [c.to_dict() for c in self.commits],
            "files": {k: v.to_dict() for k, v in self.files.items()},
            "symbols": {k: v.to_dict() for k, v in self.symbols.items()},
        }


@dataclass
class PairwiseEvidence:
    """Pairwise code-level overlap evidence between two branches."""
    branch_a: str
    branch_b: str
    shared_files: List[str] = field(default_factory=list)
    disjoint_hunk_files: List[str] = field(default_factory=list)
    overlapping_hunk_files: List[str] = field(default_factory=list)
    same_symbol_files: List[str] = field(default_factory=list)
    duplicate_commits: List[Dict[str, str]] = field(default_factory=list)
    duplicate_blobs: List[Dict[str, str]] = field(default_factory=list)
    verdict: OverlapVerdict = OverlapVerdict.DISJOINT_FILES
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch_a": self.branch_a,
            "branch_b": self.branch_b,
            "shared_files": self.shared_files,
            "disjoint_hunk_files": self.disjoint_hunk_files,
            "overlapping_hunk_files": self.overlapping_hunk_files,
            "same_symbol_files": self.same_symbol_files,
            "duplicate_commits": self.duplicate_commits,
            "duplicate_blobs": self.duplicate_blobs,
            "verdict": self.verdict.value if isinstance(self.verdict, OverlapVerdict) else str(self.verdict),
            "details": self.details,
        }


@dataclass
class EvidenceReport:
    """Full evidence report over a set of candidate branches."""
    repo_root: str
    branches: Dict[str, BranchEvidence] = field(default_factory=dict)
    pairwise_matrix: Dict[str, Dict[str, PairwiseEvidence]] = field(default_factory=dict)
    summary_counts: Dict[str, int] = field(default_factory=dict)
    cached_items: int = 0
    execution_time_seconds: float = 0.0
    generated_at: str = ""
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        matrix_dict = {}
        for b_a, pairs in self.pairwise_matrix.items():
            matrix_dict[b_a] = {b_b: p.to_dict() for b_b, p in pairs.items()}

        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "repo_root": self.repo_root,
            "summary_counts": self.summary_counts,
            "cached_items": self.cached_items,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "branches": {k: v.to_dict() for k, v in self.branches.items()},
            "pairwise_matrix": matrix_dict,
        }


CHERRY_PICK_SCHEMA_VERSION = "urn:nee:pr-cherry-pick-plan:1.0.0"


class CherryPickClass(str, Enum):
    """Per-commit classification for cherry-pick suitability (§5.5)."""
    ALREADY_IN_MAIN = "ALREADY_IN_MAIN"
    DUPLICATE_IN_SIBLING = "DUPLICATE_IN_SIBLING"
    PICK_CLEAN = "PICK_CLEAN"
    PICK_NEEDS_PREREQ = "PICK_NEEDS_PREREQ"
    PICK_CONFLICTS = "PICK_CONFLICTS"
    SPLIT_CANDIDATE = "SPLIT_CANDIDATE"
    MERGE_COMMIT = "MERGE_COMMIT"
    EMPTY_COMMIT = "EMPTY_COMMIT"


@dataclass
class CommitCherryPickVerdict:
    """Detailed cherry-pick evaluation for a single commit."""
    commit_sha: str
    short_sha: str
    subject: str
    verdict: CherryPickClass
    patch_id: Optional[str] = None
    prerequisites: List[str] = field(default_factory=list)
    conflict_files: List[str] = field(default_factory=list)
    effort_band: str = "NONE"  # NONE, LOW, MEDIUM, HIGH
    sibling_matches: List[Dict[str, str]] = field(default_factory=list)
    rationale: str = ""
    is_extractable: bool = False
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value if isinstance(self.verdict, CherryPickClass) else str(self.verdict)
        return d


@dataclass
class BranchCherryPickPlan:
    """Complete cherry-pick assessment and extract set for a branch."""
    schema_version: str = CHERRY_PICK_SCHEMA_VERSION
    generated_at: str = ""
    repo_root: str = ""
    branch: str = ""
    base_ref: str = "main"
    total_commits: int = 0
    verdicts: List[CommitCherryPickVerdict] = field(default_factory=list)
    extract_set: List[str] = field(default_factory=list)
    recommended_commands: List[str] = field(default_factory=list)
    scratch_agreement_verified: bool = False
    repo_state_digest_before: str = ""
    repo_state_digest_after: str = ""
    zero_mutation_verified: bool = True
    execution_time_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "base_ref": self.base_ref,
            "total_commits": self.total_commits,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "extract_set": self.extract_set,
            "recommended_commands": self.recommended_commands,
            "scratch_agreement_verified": self.scratch_agreement_verified,
            "repo_state_digest_before": self.repo_state_digest_before,
            "repo_state_digest_after": self.repo_state_digest_after,
            "zero_mutation_verified": self.zero_mutation_verified,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
        }

