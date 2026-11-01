"""Merkle Subgraph Hashing, W3 Temporal Staleness, and ALR Replay Protection for Semantic Cache.

Guarantees: (1) Subgraph Merkle Root Isolation, (2) W3 Temporal Awareness, and
(3) Cryptographic ALR Replay Protection (SyntheticCacheReplay re-enveloping).
"""
from __future__ import annotations

import copy
import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from ..core import digest, utc_now
from ..temporal_ssot import TemporalSSOT
from .models import SemanticCacheLookup, SemanticCacheWriteResult


def compute_subgraph_merkle_root(evidence_nodes: Sequence[Any] | None) -> str:
    """Compute deterministic binary Merkle root hash for an evidence envelope or subgraph.

    - Sorts normalized leaf hashes for order-invariance.
    - Repeatedly hashes pairs with SHA-256 until reaching a 64-char root hash.
    - If empty, returns a stable null-envelope digest.
    """
    if not evidence_nodes:
        return hashlib.sha256(b"urn:nee:empty_subgraph_envelope").hexdigest()

    leaf_hashes: list[str] = []
    for item in evidence_nodes:
        if isinstance(item, dict):
            # Canonical RFC 8785 representation
            leaf_hashes.append(digest(item))
        elif isinstance(item, str):
            leaf_hashes.append(hashlib.sha256(item.encode("utf-8")).hexdigest())
        else:
            leaf_hashes.append(hashlib.sha256(str(item).encode("utf-8")).hexdigest())

    # Topological order invariance
    leaf_hashes.sort()

    current_layer = leaf_hashes
    while len(current_layer) > 1:
        next_layer: list[str] = []
        if len(current_layer) % 2 == 1:
            # Standard binary tree extension: duplicate the odd final node
            current_layer.append(current_layer[-1])
        for i in range(0, len(current_layer), 2):
            combined = current_layer[i] + current_layer[i + 1]
            next_layer.append(hashlib.sha256(combined.encode("utf-8")).hexdigest())
        current_layer = next_layer

    return current_layer[0]


def _collect_dict_items(source: Mapping[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        val = source.get(key)
        if isinstance(val, list):
            items.extend(elem for elem in val if isinstance(elem, dict))
    return items


def extract_evidence_nodes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract candidate evidence nodes / assertions from a query response or projection."""
    target_keys = ("evidence", "assertions", "nodes", "decisions", "risks", "evidence_items", "projections")
    nodes = _collect_dict_items(payload, target_keys)
    if nodes:
        return nodes

    retrieval = payload.get("retrieval_record")
    if isinstance(retrieval, dict):
        retrieval_keys = ("hits", "documents", "records", "results")
        return _collect_dict_items(retrieval, retrieval_keys)

    return []


def _parse_candidate_date(val: Any) -> date | None:
    if val is None or not str(val).strip():
        return None
    try:
        return TemporalSSOT.parse_to_date(val)
    except Exception:
        return date.min


def _handle_dict_stale(curr: dict[str, Any], stale_values: list[Any], stack: list[Any]) -> None:
    for k, v in curr.items():
        if k == "stale_after":
            stale_values.append(v)
        elif isinstance(v, (dict, list)):
            stack.append(v)


def _handle_list_stale(curr: list[Any], stack: list[Any]) -> None:
    for elem in curr:
        if isinstance(elem, (dict, list)):
            stack.append(elem)


def _collect_stale_after_values(obj: Any) -> list[Any]:
    stale_values: list[Any] = []
    stack = [obj]
    while stack:
        curr = stack.pop()
        if isinstance(curr, dict):
            _handle_dict_stale(curr, stale_values, stack)
        elif isinstance(curr, list):
            _handle_list_stale(curr, stack)
    return stale_values


def extract_earliest_stale_after(payload: Mapping[str, Any] | None) -> str | None:
    """Extract earliest W3 stale_after timestamp (ISO-8601 UTC) across all payload assertions."""
    if not payload:
        return None

    stale_values = _collect_stale_after_values(payload)
    parsed_dates = [d for d in (_parse_candidate_date(v) for v in stale_values) if d is not None]
    if not parsed_dates:
        return None

    earliest_date = min(parsed_dates)
    return datetime(
        earliest_date.year, earliest_date.month, earliest_date.day, 23, 59, 59, tzinfo=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def is_temporally_stale(stale_after_val: Any, as_of: Any = None) -> bool:
    """Check whether a W3 stale_after date has passed relative to as_of date."""
    if stale_after_val is None or not str(stale_after_val).strip():
        return False

    try:
        stale_date = TemporalSSOT.parse_to_date(stale_after_val)
    except Exception:
        # Malformed stale_after treated as stale per SSOT
        return True

    if as_of is None:
        current_date = datetime.now(timezone.utc).date()
    else:
        try:
            current_date = TemporalSSOT.parse_to_date(as_of)
        except Exception:
            current_date = datetime.now(timezone.utc).date()

    return current_date > stale_date


def build_merkle_cache_context(
    base_context: Mapping[str, Any],
    subgraph_merkle_root: str,
) -> dict[str, Any]:
    """Bind the subgraph Merkle root into the cache context, decoupling from global graph_sha256.

    This ensures that unrelated knowledge graph mutations (e.g. Ingestion of HR docs)
    do NOT invalidate cached answers for independent subgraphs (e.g. DB Architecture).
    """
    ctx = dict(base_context)

    # If active_pair is present, substitute global graph_sha256 and generation_id with the Merkle root
    if "active_pair" in ctx and isinstance(ctx["active_pair"], dict):
        pair = dict(ctx["active_pair"])
        pair["subgraph_merkle_root"] = subgraph_merkle_root
        pair["graph_sha256"] = subgraph_merkle_root
        pair["generation_id"] = f"merkle:{subgraph_merkle_root[:16]}"
        ctx["active_pair"] = pair

    ctx["subgraph_merkle_root"] = subgraph_merkle_root
    return ctx


_STRIP_KEYS = frozenset({"hitl_role_receipts", "verified_entry_id", "ed25519_signature", "subject_sha256"})
_RESET_KEYS = frozenset({"attendee_roles_confirmed_by_operator", "operator_verified"})


def _sanitize_string(s: str) -> tuple[str, int]:
    pattern = re.compile(r"\(Operator Verified\)|\[OPERATOR VERIFIED\]", re.IGNORECASE)
    matches = pattern.findall(s)
    if matches:
        return pattern.sub("(Cached Projection - Reverification Required)", s), len(matches)
    return s, 0


def _sanitize_dict_entry(k: str, v: Any, counter: list[int]) -> tuple[bool, str, Any]:
    if k in _STRIP_KEYS:
        counter[0] += 1
        return False, k, v
    if k in _RESET_KEYS:
        return True, k, False
    sanitized_v = _sanitize_structure(v, counter)
    return True, k, sanitized_v


def _sanitize_structure(obj: Any, counter: list[int]) -> Any:
    if isinstance(obj, str):
        sanitized_str, stripped = _sanitize_string(obj)
        counter[0] += stripped
        return sanitized_str
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            keep, key, val = _sanitize_dict_entry(k, v, counter)
            if keep:
                new_dict[key] = val
        return new_dict
    if isinstance(obj, list):
        return [_sanitize_structure(elem, counter) for elem in obj]
    return obj


def sanitize_output_for_cache_replay(
    cached_output: Mapping[str, Any],
    current_run_id: str,
    operator_id: str | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """Neutralize historical ALR claims and operator verification signatures on cache hits.

    Prevents Gate 1.7 UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM by transforming prior-run
    attributions into a governed SyntheticCacheReplay envelope.
    """
    output = copy.deepcopy(dict(cached_output))
    counter = [0]
    sanitized = _sanitize_structure(output, counter)
    claims_stripped = counter[0]

    if not isinstance(sanitized, dict):
        sanitized = {"replayed_content": sanitized}

    source_run = (
        source_run_id
        or output.get("source_run_id")
        or output.get("run_id")
        or "prior_run"
    )
    sanitized["synthetic_cache_replay"] = {
        "status": "SYNTHETIC_CACHE_REPLAY",
        "replayed_at": utc_now(),
        "source_run_id": str(source_run),
        "replay_run_id": current_run_id,
        "operator_id": operator_id,
        "operator_verification_stripped": claims_stripped > 0,
        "claims_stripped_count": claims_stripped,
        "requires_operator_reverification": claims_stripped > 0,
        "cached_output_digest": digest(output),
    }

    return sanitized


class MerkleSemanticCacheBoundary:
    """Pre-tool execution cache boundary for L2 queries and Decision Intelligence evaluations."""

    def __init__(self, semantic_cache: Any) -> None:
        self.semantic_cache = semantic_cache

    def lookup(
        self,
        *,
        context: Mapping[str, Any],
        intent_vector: list[float],
        subgraph_nodes: Sequence[Any] | None = None,
        current_run_id: str | None = None,
        as_of: Any = None,
    ) -> SemanticCacheLookup:
        """Lookup with Merkle root binding, W3 temporal freshness check, and ALR sanitization."""
        ctx = dict(context)
        if subgraph_nodes is not None:
            merkle_root = compute_subgraph_merkle_root(subgraph_nodes)
            ctx = build_merkle_cache_context(ctx, merkle_root)

        lookup = self.semantic_cache.lookup(context=ctx, intent_vector=intent_vector)
        if not lookup.hit or lookup.output is None:
            return lookup

        # W3 temporal validation
        earliest_stale = extract_earliest_stale_after(lookup.output)
        if earliest_stale and is_temporally_stale(earliest_stale, as_of):
            return SemanticCacheLookup(
                mode=self.semantic_cache.mode,
                hit=False,
                reason="CACHE_MISS_W3_TEMPORAL_EXPIRED",
                entry_id=lookup.entry_id,
                similarity=lookup.similarity,
            )

        # Check if individual assertions in output are expired
        nodes = extract_evidence_nodes(lookup.output)
        for node in nodes:
            if "stale_after" in node and is_temporally_stale(node["stale_after"], as_of):
                return SemanticCacheLookup(
                    mode=self.semantic_cache.mode,
                    hit=False,
                    reason="CACHE_MISS_W3_TEMPORAL_EXPIRED",
                    entry_id=lookup.entry_id,
                    similarity=lookup.similarity,
                )

        output = lookup.output
        if current_run_id:
            output = sanitize_output_for_cache_replay(
                output, current_run_id, source_run_id=lookup.source_run_id
            )

        return SemanticCacheLookup(
            mode=lookup.mode,
            hit=True,
            reason="CACHE_HIT",
            entry_id=lookup.entry_id,
            similarity=lookup.similarity,
            output=output,
            outcome=lookup.outcome,
            source_run_id=lookup.source_run_id,
            source_trace_id=lookup.source_trace_id,
            source_output_digest=lookup.source_output_digest,
        )

    def stage(
        self,
        *,
        context: Mapping[str, Any],
        intent_digest: str,
        intent_vector: list[float],
        output: Mapping[str, Any],
        outcome: str,
        source_run_id: str,
        source_trace_id: str,
        replay_eligible: bool,
        subgraph_nodes: Sequence[Any] | None = None,
    ) -> SemanticCacheWriteResult:
        """Stage an entry into the cache with Merkle root binding and W3 temporal expiration."""
        ctx = dict(context)
        earliest_stale = extract_earliest_stale_after(output)
        if subgraph_nodes is not None:
            merkle_root = compute_subgraph_merkle_root(subgraph_nodes)
            subgraph_stale = extract_earliest_stale_after({"evidence": list(subgraph_nodes)})
            if subgraph_stale and (not earliest_stale or subgraph_stale < earliest_stale):
                earliest_stale = subgraph_stale
            ctx = build_merkle_cache_context(ctx, merkle_root)

        result = self.semantic_cache.stage(
            context=ctx,
            intent_digest=intent_digest,
            intent_vector=intent_vector,
            output=output,
            outcome=outcome,
            source_run_id=source_run_id,
            source_trace_id=source_trace_id,
            replay_eligible=replay_eligible,
            stale_after=earliest_stale,
        )

        # If earliest_stale is defined, tighten the SQLite entry expires_at
        if result.status == "PENDING" and result.entry and earliest_stale:
            try:
                conn = self.semantic_cache._connection()
                try:
                    row = conn.execute(
                        "SELECT expires_at FROM semantic_cache_entry WHERE entry_id=?",
                        (result.entry.entry_id,),
                    ).fetchone()
                    if row:
                        current_expires = row["expires_at"]
                        if earliest_stale < current_expires:
                            conn.execute(
                                "UPDATE semantic_cache_entry SET expires_at=? WHERE entry_id=?",
                                (earliest_stale, result.entry.entry_id),
                            )
                            conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

        return result

    def activate(
        self,
        entry: Any,
        *,
        source_event_count: int,
        source_event_chain_digest: str,
    ) -> SemanticCacheWriteResult:
        """Activate a pending entry."""
        return self.semantic_cache.activate(
            entry,
            source_event_count=source_event_count,
            source_event_chain_digest=source_event_chain_digest,
        )
