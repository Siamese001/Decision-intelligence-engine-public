"""Evidence-bound claim evaluation and validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.assurance import INTERNAL_OPERATIONAL

from .constants import ACTIVE_PAIR_ISSUE_CODES, CLAIM_KINDS, NEGATIVE_CLAIMS
from .primitives import _digest, _path_is_within, _resolved


def _backend_matches(record: dict[str, Any], selector: str) -> bool:
    selector = selector.strip().casefold()
    values = " ".join(
        str(record.get(field) or "").casefold()
        for field in ("embedding_backend", "model_id")
    )
    if selector in {"bge-m3", "bge_m3", "baai/bge-m3"}:  # noqa: SSOT_EXEMPT
        return "bge-m3" in values or "bge_m3" in values
    return selector in values


def _issue_blocks_backend_inactive_claim(
    issue: dict[str, Any],
    runtime_pairs: Iterable[dict[str, Any]],
    backend: str,
) -> bool:
    """Return whether an inventory issue can hide the selected active backend.

    A backend-inactive claim is about active pairs.  Invalid query or retrieval
    receipts must remain visible, but cannot turn a verified nonmatching active
    pair into the selected backend.  By contrast, a malformed active pair, or
    one whose backend is missing or selected, leaves the active-backend state
    uncertain and must fail closed.
    """

    if issue.get("code") not in ACTIVE_PAIR_ISSUE_CODES:
        return False
    issue_path = issue.get("path")
    if not isinstance(issue_path, str) or not issue_path:
        return True
    related_pairs = [
        pair
        for pair in runtime_pairs
        if (
            pair.get("active_pair_path") == issue_path
            or (
                isinstance(pair.get("runtime_root"), str)
                and _path_is_within(issue_path, Path(pair["runtime_root"]))
            )
        )
    ]
    if not related_pairs:
        return True
    return any(
        not isinstance(pair.get("embedding_backend"), str)
        or not pair["embedding_backend"].strip()
        or _backend_matches(pair, backend)
        for pair in related_pairs
    )


def evaluate_claim(
    inventory: dict[str, Any],
    *,
    kind: str,
    backend: str,
    scope_roots: Iterable[Path] = (),
    complete_scope: bool = False,
) -> dict[str, Any]:
    """Evaluate a claim against exact runtime and query evidence."""

    if kind not in CLAIM_KINDS:
        raise ValueError(f"unsupported runtime evidence claim: {kind}")
    roots = sorted(
        {_resolved(path) for path in scope_roots}, key=lambda value: str(value).casefold()
    )
    negative = kind in NEGATIVE_CLAIMS
    reasons: list[str] = []
    if negative and not roots:
        reasons.append("NEGATIVE_CLAIM_REQUIRES_EXPLICIT_SCOPE_ROOTS")
    if negative and not complete_scope:
        reasons.append("NEGATIVE_CLAIM_REQUIRES_COMPLETE_SCOPE_ATTESTATION")

    def in_scope(record: dict[str, Any]) -> bool:
        if not roots:
            return True
        candidates = [
            record.get("runtime_root"),
            record.get("evidence_root"),
            record.get("path"),
            *record.get("artifact_paths", []),
        ]
        return any(
            isinstance(candidate, str)
            and any(_path_is_within(candidate, root) for root in roots)
            for candidate in candidates
        )

    scoped_pairs = [
        item for item in inventory["runtime_pairs"] if in_scope(item)
    ]
    scoped_queries = [
        item for item in inventory["query_evidence"] if in_scope(item)
    ]
    scoped_evaluations = [
        item for item in inventory.get("retrieval_evaluations", []) if in_scope(item)
    ]
    scoped_l1_terminals = [
        item for item in inventory.get("l1_controller_terminals", []) if in_scope(item)
    ]
    matching_pairs = [
        item for item in scoped_pairs if _backend_matches(item, backend)
    ]
    matching_queries = [
        item for item in scoped_queries if _backend_matches(item, backend)
    ]
    matching_evaluations = [
        item for item in scoped_evaluations if _backend_matches(item, backend)
    ]
    matching_l1_terminals = [
        item for item in scoped_l1_terminals if _backend_matches(item, backend)
    ]
    scoped_issues = [
        item
        for item in inventory["issues"]
        if not roots or any(_path_is_within(item["path"], root) for root in roots)
    ]
    if kind == "backend-inactive":
        blocking_issues = [
            item
            for item in scoped_issues
            if _issue_blocks_backend_inactive_claim(item, scoped_pairs, backend)
        ]
    else:
        blocking_issues = scoped_issues
    nonblocking_issues = [
        item for item in scoped_issues if item not in blocking_issues
    ]
    if negative and blocking_issues:
        reasons.append("NEGATIVE_CLAIM_BLOCKED_BY_INCOMPLETE_OR_INVALID_EVIDENCE")

    if kind == "backend-active":
        supported = bool(matching_pairs)
        if not supported:
            reasons.append("NO_MATCHING_ACTIVE_PAIR_FOUND_IN_SEARCHED_SCOPE")
    elif kind == "internal-operational-active":
        matching_pairs = [
            item
            for item in matching_pairs
            if item.get("assurance_tier") == INTERNAL_OPERATIONAL
            and item.get("operational_status") == "QUALIFIED"
        ]
        supported = bool(matching_pairs)
        if not supported:
            reasons.append(
                "NO_QUALIFIED_INTERNAL_OPERATIONAL_PAIR_FOUND_IN_SEARCHED_SCOPE"
            )
    elif kind == "backend-inactive":
        supported = not matching_pairs and not reasons
        if matching_pairs:
            reasons.append("MATCHING_ACTIVE_PAIR_EXISTS")
    elif kind == "retrieval-present":
        supported = bool(matching_queries or matching_evaluations)
        if not supported:
            reasons.append("NO_MATCHING_QUERY_EVIDENCE_FOUND_IN_SEARCHED_SCOPE")
            reasons.append(
                "NO_MATCHING_RETRIEVAL_EVALUATION_FOUND_IN_SEARCHED_SCOPE"
            )
    else:
        if kind == "l1-terminal-present":
            supported = bool(matching_l1_terminals)
            if not supported:
                reasons.append("NO_MATCHING_L1_TERMINAL_FOUND_IN_SEARCHED_SCOPE")
        elif kind == "l1-terminal-absent":
            supported = not matching_l1_terminals and not reasons
            if matching_l1_terminals:
                reasons.append("MATCHING_L1_TERMINAL_EXISTS")
        else:
            supported = not matching_queries and not matching_evaluations and not reasons
            if matching_queries:
                reasons.append("MATCHING_QUERY_EVIDENCE_EXISTS")
            if matching_evaluations:
                reasons.append("MATCHING_RETRIEVAL_EVALUATION_EXISTS")

    result = {
        "schema_version": "1.0.0",
        "claim_kind": kind,
        "backend_selector": backend,
        "supported": supported,
        "complete_scope_attested": complete_scope,
        "scope_roots": [str(path) for path in roots],
        "searched_runtime_count": len(scoped_pairs),
        "searched_query_evidence_count": len(scoped_queries),
        "searched_retrieval_evaluation_count": len(scoped_evaluations),
        "searched_l1_terminal_count": len(scoped_l1_terminals),
        "matching_runtime_pairs": matching_pairs,
        "matching_query_evidence": matching_queries,
        "matching_retrieval_evaluations": matching_evaluations,
        "matching_l1_terminals": matching_l1_terminals,
        "blocking_issues": blocking_issues,
        "nonblocking_issues": nonblocking_issues,
        "reason_codes": sorted(set(reasons)),
        "inventory_digest": inventory["inventory_digest"],
    }
    result["claim_evidence_digest"] = _digest(result)
    return result

