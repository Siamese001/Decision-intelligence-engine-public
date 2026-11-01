"""
dual_pipeline.runtime_evidence_core

Modularized runtime evidence discovery, claim evaluation, and control verification.
"""
from __future__ import annotations

from .claims import (
    _backend_matches,
    _issue_blocks_backend_inactive_claim,
    evaluate_claim,
)
from .cli import (
    _build_parser,
    _write_output,
    main,
)
from .constants import (
    ACTIVE_PAIR_ISSUE_CODES,
    AUTOMATIC_EVAL_GATE_BINDINGS,
    BASE_PRUNE_NAMES,
    CLAIM_KINDS,
    CONTROL_BEGIN,
    CONTROL_END,
    CONTROL_FILES,
    DEPRECATION_GATE_BINDINGS,
    HOOK_COMMAND,
    INTERNAL_OPERATIONAL,
    NEGATIVE_CLAIMS,
    PAIR_REQUIRED_FIELDS,
    REFERENCE_KEYS,
    REQUIRED_CONTROL_PHRASES,
    RETRIEVAL_RELEASE_GATE_BINDINGS,
    RUNTIME_PRUNE_NAMES,
)
from .controls import (
    validate_control_files,
)
from .discovery import (
    _artifact_digest,
    _l1_terminal_paths,
    _l1_terminal_record,
    _pair_record,
    _query_paths,
    _query_record,
    _query_scope_key,
    _retrieval_evaluation_paths,
    _retrieval_evaluation_record,
    _retrieval_suite_paths,
    _retrieval_suite_record,
    discover_inventory,
    discover_receipt_references,
)
from .primitives import (
    _canonical_json,
    _digest,
    _is_browser_profile_directory,
    _iter_reference_values,
    _path_is_within,
    _read_json,
    _resolve_reference,
    _resolved,
    _walk_for_active_pairs,
)
