"""Tree-OID and Branch-Delta DSSE Attestation Helpers for PR Governance.

Keys DSSE attestations by `(branch_tree_oid, main_tree_oid)` and `branch_delta_digest`
alongside commit SHA so disjoint rebases onto `main` preserve qualification validity
without mutating tracked JSON files in `reports/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from tools.attestation_core import (
    PR_REVIEW_PREDICATE_TYPE,
    READINESS_PREDICATE_TYPE,
    _is_disjoint_rebase_equivalent,
    check_readiness_qualification,
    compute_branch_delta_digest,
    compute_git_tree_oid,
    compute_tree_digest,
    get_attestation_path,
    load_attestation_by_tree,
    save_attestation,
    save_readiness_attestation,
    unwrap_dsse_envelope,
    verify_statement_subject,
)

PR_MERGE_PREDICATE_TYPE = "https://decision-intelligence.engine/attestation/pr-merge/v1"


def seal_branch_tree_attestation(
    repo_root: Path,
    branch: str,
    head_sha: str,
    predicate: Dict[str, Any],
    predicate_type: str = PR_REVIEW_PREDICATE_TYPE,
) -> Path:
    """Seals an out-of-tree DSSE attestation indexed by tree digest and branch delta digest."""
    enriched = dict(predicate)
    enriched.setdefault("branch", branch)
    enriched.setdefault("head_sha", head_sha)
    branch_tree_oid = compute_git_tree_oid(repo_root, ref=head_sha)
    main_tree_oid = compute_git_tree_oid(repo_root, ref="main")
    delta_digest = compute_branch_delta_digest(repo_root, ref=head_sha, base_ref="main")
    if branch_tree_oid:
        enriched["branch_tree_oid"] = branch_tree_oid
    if main_tree_oid:
        enriched["main_tree_oid"] = main_tree_oid
    if delta_digest:
        enriched["branch_delta_digest"] = delta_digest
    return save_attestation(
        repo_root=repo_root,
        predicate=enriched,
        head_sha=head_sha,
        predicate_type=predicate_type,
    )


def resolve_branch_tree_attestation(
    repo_root: Path,
    branch: str,
    head_sha: str,
    predicate_type: str = PR_REVIEW_PREDICATE_TYPE,
) -> Optional[Dict[str, Any]]:
    """Resolves a PR review or readiness predicate by exact SHA, tree OID, or disjoint delta OID."""
    try:
        tree_digest = compute_tree_digest(repo_root, ref=head_sha or "HEAD")
        stmt = load_attestation_by_tree(
            repo_root, tree_digest, head_sha=head_sha, predicate_type=predicate_type
        )
        if stmt and stmt.get("predicateType") == predicate_type:
            pred = stmt.get("predicate")
            if isinstance(pred, dict) and pred.get("branch") in (None, branch):
                rec_sha = str(pred.get("head_sha") or pred.get("head_commit_sha") or "")
                rec_tree = str(pred.get("branch_tree_oid") or "")
                cur_tree = compute_git_tree_oid(repo_root, ref=head_sha or "HEAD")
                if not head_sha or rec_sha == head_sha or (rec_tree and rec_tree == cur_tree):
                    rebound = dict(pred)
                    if head_sha:
                        rebound["head_sha"] = head_sha
                    return rebound

        delta_digest = compute_branch_delta_digest(repo_root, ref=head_sha or "HEAD", base_ref="main")
        if delta_digest:
            delta_path = get_attestation_path(
                repo_root, f"delta_{delta_digest}", predicate_type=predicate_type
            )
            if delta_path.is_file():
                import json

                data = json.loads(delta_path.read_text(encoding="utf-8"))
                d_stmt = unwrap_dsse_envelope(data)
                if verify_statement_subject(d_stmt, delta_digest):
                    pred = d_stmt.get("predicate")
                    if isinstance(pred, dict) and pred.get("branch") in (None, branch):
                        old_sha = str(pred.get("head_sha") or pred.get("head_commit_sha") or "")
                        old_base = str(pred.get("base_commit_sha") or pred.get("base_sha") or "")
                        is_disjoint, new_main = _is_disjoint_rebase_equivalent(
                            repo_root, branch, old_sha, head_sha, old_base
                        )
                        if is_disjoint:
                            rebound = dict(pred)
                            rebound["head_sha"] = head_sha
                            rebound["base_commit_sha"] = new_main
                            return rebound
    except Exception as exc:
        _ = exc
    return None


__all__ = [
    "PR_MERGE_PREDICATE_TYPE",
    "PR_REVIEW_PREDICATE_TYPE",
    "READINESS_PREDICATE_TYPE",
    "check_readiness_qualification",
    "compute_branch_delta_digest",
    "compute_git_tree_oid",
    "compute_tree_digest",
    "resolve_branch_tree_attestation",
    "seal_branch_tree_attestation",
]
