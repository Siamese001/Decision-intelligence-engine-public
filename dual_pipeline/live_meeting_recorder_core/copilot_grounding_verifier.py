"""
dual_pipeline/live_meeting_recorder_core/copilot_grounding_verifier.py

Two-Tier Grounding Verifier and Speculative Reranker for Live Executive Copilot.
Enforces Wave 4 acceptance criteria:
1. Validity: Active, non-superseded, non-stale, current graph membership.
2. Provenance: Evidence count, source diversity, and recency scoring.
3. Conflict check: Contradictory assertions surfaced as conflicts, never merged.
4. Rerank: Asynchronous speculative pre-encoding with vectorized BLAS dot-product (Decision D2).
5. Budget guard: Fallback to fast tier (rigor: FAST) on budget breach or model contention.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from dual_pipeline.parameters import (
    get_copilot_rerank_weights, get_copilot_rigor_budget_ms, get_copilot_rigor_min_provenance_score,
)
from .c19_firewall import has_c19_marker, is_c19_quarantined_assertion, is_c19_quarantined_node
from .copilot_predicates import PRIORITY_PREDICATES, QUESTION_PREDICATES

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier")


@dataclass
class GroundingVerificationResult:
    """Encapsulates two-tier verification, provenance, conflicts, and reranking telemetry."""
    rigor_tier: str  # "VERIFIED" or "FAST"
    verified_assertions: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    per_stage_timing_ms: Dict[str, float] = field(default_factory=dict)
    total_ms: float = 0.0
    provenance_scores: Dict[str, float] = field(default_factory=dict)
    is_budget_breached: bool = False
    hash_mismatch: bool = False
    non_blocking_fallback: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rigor_tier": self.rigor_tier, "verified_assertion_count": len(self.verified_assertions),
            "conflict_count": len(self.conflicts), "per_stage_timing_ms": self.per_stage_timing_ms,
            "total_ms": round(self.total_ms, 2), "is_budget_breached": self.is_budget_breached,
            "hash_mismatch": self.hash_mismatch, "non_blocking_fallback": self.non_blocking_fallback,
            "error": self.error,
        }


def _parse_date_epoch(date_str: Any) -> Optional[float]:
    """Parses ISO, MM/DD/YYYY, or YYYY-MM-DD date into UTC epoch timestamp."""
    if not date_str or not isinstance(date_str, str):
        return None
    cleaned = date_str.strip().replace("Z", "+00:00")
    for fmt in (None, "%m/%d/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(cleaned) if fmt is None else datetime.strptime(cleaned, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            continue
    return None


_is_c19_quarantined_assertion = is_c19_quarantined_assertion


def _is_assertion_superseded(assertion: Dict[str, Any]) -> bool:
    if assertion.get("is_active") is False or assertion.get("is_active") == 0:
        return True
    return str(assertion.get("status", "")).upper() in ("SUPERSEDED", "REJECTED", "DELETED", "INACTIVE", "REMOVED")


def _is_assertion_stale(assertion: Dict[str, Any], current_epoch: Optional[float]) -> bool:
    stale_str = assertion.get("stale_after")
    if not stale_str:
        return False
    stale_epoch = _parse_date_epoch(stale_str)
    ref_epoch = current_epoch if current_epoch is not None else time.time()
    return stale_epoch is not None and ref_epoch > stale_epoch


def _is_missing_from_topology(topology: Any, aid: str) -> bool:
    graph_assertions = getattr(topology, "assertions", None)
    if isinstance(graph_assertions, dict):
        return aid not in graph_assertions
    if isinstance(graph_assertions, list):
        return not any(a.get("assertion_id") == aid for a in graph_assertions if isinstance(a, dict))
    return False


def _is_pointer_mismatched(
    assertion: Dict[str, Any], pinned_gen: Optional[str], packet_sha: Optional[str]
) -> bool:
    c_gen = assertion.get("generation_id") or assertion.get("generation") or assertion.get("_pinned_generation")
    if pinned_gen and c_gen and c_gen != pinned_gen:
        return True
    c_sha = assertion.get("graph_sha256") or assertion.get("_pinned_graph_sha")
    return bool(packet_sha and c_sha and c_sha != packet_sha)


def check_assertion_validity(
    assertion: Dict[str, Any],
    topology: Optional[Any] = None,
    packet_graph_sha: Optional[str] = None,
    current_epoch: Optional[float] = None,
    pinned_generation: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Verifies that candidate assertion is active, not superseded, not past stale_after, not quarantined."""
    if not isinstance(assertion, dict):
        return False, "INVALID_PAYLOAD"
    aid = str(assertion.get("assertion_id") or "")
    if not aid:
        return False, "MISSING_ASSERTION_ID"
    if _is_c19_quarantined_assertion(assertion, aid):
        return False, "C19_QUARANTINED"
    eng_id = str(assertion.get("engagement_id") or assertion.get("project_scope") or "")
    if eng_id.startswith("quarantine:"):
        return False, "SCOPE_QUARANTINED"
    if _is_assertion_superseded(assertion):
        return False, "SUPERSEDED_OR_INACTIVE"
    if _is_assertion_stale(assertion, current_epoch):
        return False, "STALE_AFTER_EXPIRED"
    if not assertion.get("is_live_turn") and topology is not None and _is_missing_from_topology(topology, aid):
        return False, "REMOVED_FROM_GRAPH"
    if _is_pointer_mismatched(assertion, pinned_generation, packet_graph_sha):
        return False, "GRAPH_POINTER_MISMATCH"
    return True, None


def _compute_evidence_factor(assertion: Dict[str, Any], primary_src: str) -> float:
    ev_ids = assertion.get("evidence_ids")
    ev_count = len(ev_ids) if isinstance(ev_ids, list) else (1 if (assertion.get("evidence_id") or primary_src) else 0)
    return min(1.0, ev_count / 3.0)


def _compute_source_diversity(assertion: Dict[str, Any], primary_src: str) -> float:
    srcs = assertion.get("source_ids")
    sources = list(srcs) if isinstance(srcs, list) else ([assertion.get("source_id") or primary_src] if (assertion.get("source_id") or primary_src) else [])
    if primary_src and primary_src not in sources:
        sources.append(primary_src)
    types = {str(s).split(":")[0] for s in sources if s}
    return min(1.0, len(types) / 2.0) if types else (0.5 if sources else 0.2)


def _compute_recency_factor(assertion: Dict[str, Any], current_epoch: Optional[float]) -> float:
    dt_str = assertion.get("observed_at") or assertion.get("source_date")
    ref_epoch = current_epoch if current_epoch is not None else time.time()
    dt_epoch = _parse_date_epoch(dt_str)
    return max(0.1, math.exp(-0.015 * max(0.0, (ref_epoch - dt_epoch) / 86400.0))) if dt_epoch else 0.5


def _apply_provenance_floor(calculated: float, assertion: Dict[str, Any], primary_src: str) -> float:
    val_state = str(assertion.get("validation_state") or assertion.get("canonical_status") or "").lower()
    if primary_src or val_state in ("valid", "verified", "passed", "canonical", "active", "promoted"):
        return max(0.30, calculated)
    if bool(assertion.get("extraction_run_id") or assertion.get("evidence_id") or assertion.get("evidence_ids")):
        return max(0.15, calculated)
    return calculated


def compute_provenance_score(assertion: Dict[str, Any], current_epoch: Optional[float] = None) -> float:
    """Calculates provenance strength [0.0, 1.0] from evidence count, source diversity, and recency."""
    p_src = str(assertion.get("primary_source_id") or "").strip()
    calc = 0.5 * _compute_evidence_factor(assertion, p_src) + 0.3 * _compute_source_diversity(assertion, p_src) + 0.2 * _compute_recency_factor(assertion, current_epoch)
    return round(_apply_provenance_floor(calc, assertion, p_src), 3)


OPPOSING_POLARITIES: Tuple[Tuple[str, str], ...] = (
    ("approved", "rejected"), ("funded", "unfunded"),
    ("yes", "no"), ("delayed", "on track"),
)

# Predicates representing multi-valued relations where distinct objects are complementary facts, not contradictions.
MULTI_VALUED_PREDICATES: Set[str] = {
    "HAS_CLAIM_TEXT", "MENTIONS", "CONTAINS", "DISCUSSES", "DISCUSSED_IN_LIVE_CALL", "SUPPORTS",
    "HAS_DOCUMENT", "HAS_NOTE", "TAGGED_WITH", "HAS_EVIDENCE", "RAISES_RISK", "COMMITS_TO", "DECIDES", "ASKS_ABOUT", "REPORTS",
}


def _detect_pair_discrepancy(o1: str, o2: str, c1: str, c2: str, predicate: str = "") -> Optional[str]:
    # Multi-valued predicates (e.g., multiple facts/claims on a subject) do not conflict simply because object values differ.
    if predicate not in MULTI_VALUED_PREDICATES and o1 and o2 and o1 != o2:
        return f"Opposing object values: '{o1}' vs '{o2}'"
    low1, low2 = c1.lower(), c2.lower()
    for p1, p2 in OPPOSING_POLARITIES:
        if p1 in low1 and p2 in low2:
            return f"Opposing claim polarities: '{c1}' vs '{c2}'"
    return None


def _check_pair_conflict(a1: Dict[str, Any], a2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    aid1, aid2 = str(a1.get("assertion_id", "")), str(a2.get("assertion_id", ""))
    s1 = str(a1.get("subject_node_id") or a1.get("subject") or "").strip().lower()
    s2 = str(a2.get("subject_node_id") or a2.get("subject") or "").strip().lower()
    p1 = str(a1.get("predicate") or "").strip().upper()
    p2 = str(a2.get("predicate") or "").strip().upper()
    if not (s1 and p1) or s1 != s2 or p1 != p2:
        return None

    o1 = str(a1.get("object_node_id") or a1.get("object_literal") or "").strip().lower()
    o2 = str(a2.get("object_node_id") or a2.get("object_literal") or "").strip().lower()
    c1 = str(a1.get("claim_text") or a1.get("claim") or "")
    c2 = str(a2.get("claim_text") or a2.get("claim") or "")

    reason = _detect_pair_discrepancy(o1, o2, c1, c2, predicate=p1)
    if not reason:
        return None
    return {"assertion_a": aid1, "assertion_b": aid2, "subject": s1, "predicate": p1, "reason": reason, "claim_a": c1, "claim_b": c2}


def _collect_group_conflicts(
    group: List[Dict[str, Any]], seen_pairs: Set[Tuple[str, str]], conflicts: List[Dict[str, Any]]
) -> None:
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            c = _check_pair_conflict(group[i], group[j])
            if c:
                pair_key = tuple(sorted([c["assertion_a"], c["assertion_b"]]))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    conflicts.append(c)


def detect_assertion_conflicts(assertions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detects contradictory assertion pairs in O(N) by bucketing on (subject, predicate)."""
    conflicts: List[Dict[str, Any]] = []
    seen_pairs: Set[Tuple[str, str]] = set()
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for a in assertions:
        s = str(a.get("subject_node_id") or a.get("subject") or "").strip().lower()
        p = str(a.get("predicate") or "").strip().upper()
        if s and p:
            buckets.setdefault((s, p), []).append(a)
    for group in buckets.values():
        if len(group) >= 2:
            _collect_group_conflicts(group, seen_pairs, conflicts)
    return conflicts


def _tokenize_text(text: str) -> Set[str]:
    raw = re.findall(r"\b[A-Za-z0-9_]{3,}\b|\b[A-Z]{2,}\b", text or "")
    return {w.lower() for w in raw}


def _score_lexical(candidates: List[Dict[str, Any]], query_tokens: Set[str]) -> np.ndarray:
    scores = np.zeros(len(candidates), dtype=np.float32)
    if not query_tokens:
        return scores
    for idx, c in enumerate(candidates):
        txt = f"{c.get('claim_text', '')} {c.get('predicate', '')} {c.get('subject_node_id', '')} {c.get('object_literal', '')}"
        c_tokens = _tokenize_text(txt)
        overlap = len(query_tokens & c_tokens)
        scores[idx] = min(1.0, overlap / max(1, len(query_tokens)))
    return scores


def _score_structural(candidates: List[Dict[str, Any]]) -> np.ndarray:
    scores = np.zeros(len(candidates), dtype=np.float32)
    for idx, c in enumerate(candidates):
        pred = str(c.get("predicate", "")).upper()
        s = 0.3
        if pred in PRIORITY_PREDICATES:
            s += 0.4
        if pred in QUESTION_PREDICATES:
            s += 0.2
        if c.get("is_priority"):
            s += 0.3
        if c.get("connected_chains"):
            s += 0.2
        scores[idx] = min(1.0, s)
    return scores


def _score_dense(
    candidates: List[Dict[str, Any]],
    query_vector: Optional[np.ndarray],
) -> Tuple[np.ndarray, bool]:
    scores = np.zeros(len(candidates), dtype=np.float32)
    if query_vector is None:
        return scores, True

    valid_vectors = []
    vector_indices = []
    for idx, c in enumerate(candidates):
        vec = c.get("dense_vector")
        if vec is not None:
            arr = np.asarray(vec, dtype=np.float32)
            if arr.ndim == 1 and arr.size > 0:
                valid_vectors.append(arr)
                vector_indices.append(idx)

    if not valid_vectors:
        return scores, True

    q = np.asarray(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm < 1e-6:
        return scores, True

    mat = np.stack(valid_vectors, axis=0)
    mat_norms = np.linalg.norm(mat, axis=1)
    mat_norms[mat_norms < 1e-6] = 1e-6
    dots = np.dot(mat, q) / (mat_norms * q_norm)
    dots = np.clip(dots, 0.0, 1.0)
    for sub_i, orig_i in enumerate(vector_indices):
        scores[orig_i] = dots[sub_i]
    return scores, False


def rerank_assertions_vectorized(
    candidates: List[Dict[str, Any]],
    query_tokens: Set[str],
    query_vector: Optional[np.ndarray] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Reranks candidate assertions using a normalized hybrid mixture of lexical,
    structural, and dense cosine similarity via vectorized BLAS (Decision D2).
    """
    if not candidates:
        return [], False

    w = weights or get_copilot_rerank_weights()
    w_lex = float(w.get("lexical", 0.3))
    w_struct = float(w.get("structural", 0.3))
    w_dense = float(w.get("dense", 0.4))

    lex_scores = _score_lexical(candidates, query_tokens)
    struct_scores = _score_structural(candidates)
    dense_scores, fallback = _score_dense(candidates, query_vector)

    if fallback:
        sub_total = w_lex + w_struct
        eff_lex = w_lex / sub_total if sub_total > 0 else 0.5
        eff_struct = w_struct / sub_total if sub_total > 0 else 0.5
        final_scores = eff_lex * lex_scores + eff_struct * struct_scores
    else:
        final_scores = w_lex * lex_scores + w_struct * struct_scores + w_dense * dense_scores

    locked = [(idx, final_scores[idx], c) for idx, c in enumerate(candidates) if c.get("live_directive_lock")]
    unlocked = [(final_scores[idx], c) for idx, c in enumerate(candidates) if not c.get("live_directive_lock")]
    ranked_pairs = sorted(
        unlocked,
        key=lambda pair: pair[0],
        reverse=True,
    )
    for score, cand in ranked_pairs:
        cand["_rerank_score"] = float(score)
    for _idx, score, cand in locked:
        cand["_rerank_score"] = max(1.0, float(score))

    return [cand for _idx, _score, cand in locked] + [cand for _, cand in ranked_pairs], fallback


class AsyncSpeculativeEncoder:
    """
    Singleton background worker performing speculative dense encoding of live dialogue turns.
    Guarantees non-blocking access for real-time meeting turns (Decision D2).
    """
    _instance: Optional[AsyncSpeculativeEncoder] = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_vector: Optional[np.ndarray] = None
        self._cached_text: str = ""
        self._last_encode_epoch: float = 0.0

    @classmethod
    def get_instance(cls) -> AsyncSpeculativeEncoder:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def get_cached_vector(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._cached_vector is not None:
                return self._cached_vector.copy()
            return None

    def set_cached_vector(self, text: str, vector: Any) -> None:
        with self._lock:
            self._cached_text = text
            self._cached_vector = np.asarray(vector, dtype=np.float32)
            self._last_encode_epoch = time.time()

    def trigger_speculative_encode(
        self,
        text: str,
        backend: Optional[Any] = None,
    ) -> bool:
        if not text or len(text.strip()) < 10:
            return False
        if backend is None or not hasattr(backend, "encode_dense_only"):
            return False
        if not self._lock.acquire(blocking=False):
            return False

        def _worker() -> None:
            try:
                dense = backend.encode_dense_only(text)
                self._cached_vector = np.asarray(dense, dtype=np.float32)
                self._cached_text = text
                self._last_encode_epoch = time.time()
            except Exception as e:
                logger.debug("Speculative background encoding error: %s", e)
            finally:
                self._lock.release()

        t = threading.Thread(target=_worker, name="copilot_speculative_encoder", daemon=True)
        t.start()
        return True


class CopilotGroundingVerifier:
    """Two-tier grounding verifier coordinating validity, provenance, conflict detection, and BLAS reranking."""

    def __init__(
        self,
        budget_ms: Optional[float] = None,
        min_provenance_score: Optional[float] = None,
        rerank_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self.budget_ms = budget_ms if budget_ms is not None else get_copilot_rigor_budget_ms()
        self.min_provenance = (
            min_provenance_score
            if min_provenance_score is not None
            else get_copilot_rigor_min_provenance_score()
        )
        self.rerank_weights = rerank_weights or get_copilot_rerank_weights()
        self.speculative_encoder = AsyncSpeculativeEncoder.get_instance()

    def verify_and_ground(
        self,
        candidates: List[Dict[str, Any]],
        dialogue: Any,
        topology: Optional[Any] = None,
        packet_graph_sha: Optional[str] = None,
        current_epoch: Optional[float] = None,
        explicit_query_vector: Optional[np.ndarray] = None,
        pinned_generation: Optional[str] = None,
    ) -> GroundingVerificationResult:
        t_start = time.perf_counter()
        timing: Dict[str, float] = {}

        if not candidates:
            total_elapsed = (time.perf_counter() - t_start) * 1000.0
            return GroundingVerificationResult(
                rigor_tier="VERIFIED",
                verified_assertions=[],
                conflicts=[],
                per_stage_timing_ms={"validity": 0.0, "total": round(total_elapsed, 2)},
                total_ms=total_elapsed,
            )

        hash_mismatch = False
        if topology is not None and packet_graph_sha:
            curr_sha = getattr(topology, "graph_sha256", None)
            if curr_sha and curr_sha != packet_graph_sha:
                hash_mismatch = True

        # STAGE 1: Validity
        t_v0 = time.perf_counter()
        valid_candidates = [
            c for c in candidates
            if check_assertion_validity(
                c,
                topology=topology,
                packet_graph_sha=packet_graph_sha,
                current_epoch=current_epoch,
                pinned_generation=pinned_generation,
            )[0]
        ]
        timing["validity_ms"] = round((time.perf_counter() - t_v0) * 1000.0, 3)

        # STAGE 2: Provenance
        t_p0 = time.perf_counter()
        provenance_map: Dict[str, float] = {}
        proven_candidates: List[Dict[str, Any]] = []
        for c in valid_candidates:
            aid = c.get("assertion_id", "")
            score = compute_provenance_score(c, current_epoch=current_epoch)
            provenance_map[aid] = score
            if score >= self.min_provenance:
                c_copy = dict(c)
                c_copy["provenance_score"] = score
                proven_candidates.append(c_copy)
        timing["provenance_ms"] = round((time.perf_counter() - t_p0) * 1000.0, 3)

        # STAGE 3: Conflict
        t_c0 = time.perf_counter()
        conflicts = detect_assertion_conflicts(proven_candidates)
        conflicting_aids = {str(c["assertion_a"]) for c in conflicts} | {str(c["assertion_b"]) for c in conflicts}
        non_conflicting = [c for c in proven_candidates if str(c.get("assertion_id")) not in conflicting_aids]
        timing["conflict_ms"] = round((time.perf_counter() - t_c0) * 1000.0, 3)

        if (time.perf_counter() - t_start) * 1000.0 > self.budget_ms:
            total_elapsed = (time.perf_counter() - t_start) * 1000.0
            return GroundingVerificationResult(
                rigor_tier="FAST",
                verified_assertions=non_conflicting,
                conflicts=conflicts,
                per_stage_timing_ms=timing,
                total_ms=total_elapsed,
                provenance_scores=provenance_map,
                is_budget_breached=True,
                hash_mismatch=hash_mismatch,
            )

        # STAGE 4: Hybrid Rerank
        t_r0 = time.perf_counter()
        query_tokens = _tokenize_text(str(dialogue))
        q_vec = explicit_query_vector if explicit_query_vector is not None else self.speculative_encoder.get_cached_vector()
        reranked, fallback = rerank_assertions_vectorized(
            proven_candidates, query_tokens=query_tokens, query_vector=q_vec, weights=self.rerank_weights
        )
        timing["rerank_ms"] = round((time.perf_counter() - t_r0) * 1000.0, 3)
        total_elapsed = (time.perf_counter() - t_start) * 1000.0
        timing["total_ms"] = round(total_elapsed, 3)
        is_breached = total_elapsed > self.budget_ms
        rigor = "FAST" if is_breached else "VERIFIED"
        final_assertions = [c for c in reranked if str(c.get("assertion_id")) not in conflicting_aids]
        return GroundingVerificationResult(
            rigor_tier=rigor, verified_assertions=final_assertions, conflicts=conflicts,
            per_stage_timing_ms=timing, total_ms=total_elapsed, provenance_scores=provenance_map,
            is_budget_breached=is_breached, hash_mismatch=hash_mismatch, non_blocking_fallback=fallback,
        )
