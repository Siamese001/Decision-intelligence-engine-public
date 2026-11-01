"""tools/attestation_core

Out-of-tree attestation and DSSE envelope architecture for AI-DLC operational efficiency (Wave 6).
Binds readiness and PR verification to git tree digests excluding volatile reports/** paths.
"""

from __future__ import annotations

from .digest import (
    compute_branch_delta_digest,
    compute_git_tree_oid,
    compute_staged_tree_digest,
    compute_tree_digest,
)
from .envelope import (
    IN_TOTO_STATEMENT_TYPE,
    PR_REVIEW_PREDICATE_TYPE,
    READINESS_PREDICATE_TYPE,
    create_in_toto_statement,
    unwrap_dsse_envelope,
    verify_statement_subject,
    wrap_dsse_envelope,
)
from .storage import (
    _is_disjoint_rebase_equivalent,
    check_readiness_qualification,
    get_attestation_path,
    load_attestation_by_tree,
    save_attestation,
    save_readiness_attestation,
)

__all__ = [
    "IN_TOTO_STATEMENT_TYPE",
    "PR_REVIEW_PREDICATE_TYPE",
    "READINESS_PREDICATE_TYPE",
    "_is_disjoint_rebase_equivalent",
    "check_readiness_qualification",
    "compute_branch_delta_digest",
    "compute_git_tree_oid",
    "compute_staged_tree_digest",
    "compute_tree_digest",
    "create_in_toto_statement",
    "get_attestation_path",
    "load_attestation_by_tree",
    "save_attestation",
    "save_readiness_attestation",
    "unwrap_dsse_envelope",
    "verify_statement_subject",
    "wrap_dsse_envelope",
]
