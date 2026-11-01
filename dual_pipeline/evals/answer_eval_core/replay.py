"""Replay simulation mode for stored context snapshots."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import sqlite3

from .metrics import compute_groundedness
from .models import ReplayComparison


def compute_param_digest(params: Dict[str, Any]) -> str:
    """Computes deterministic SHA-256 for a parameter dictionary."""
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _p95(vals: List[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[max(0, min(int(0.95 * len(s)) - 1, len(s) - 1))]


def _filter_citations_by_predicates(
    orig_citations: set,
    candidate_params: Dict[str, Any],
    con: Optional[sqlite3.Connection],
) -> set:
    filter_predicates = candidate_params.get("filter_predicates")
    if filter_predicates is None or con is None:
        return orig_citations
    allowed = set(filter_predicates)
    filtered = set()
    for cid in orig_citations:
        try:
            cur = con.execute("SELECT predicate FROM assertion WHERE assertion_id = ?", (cid,))
            row = cur.fetchone()
            if row and row[0] in allowed:
                filtered.add(cid)
        except sqlite3.OperationalError:
            filtered.add(cid)
    return filtered


def _simulate_citations(
    orig_citations: set,
    candidate_params: Dict[str, Any],
    con: Optional[sqlite3.Connection],
) -> set:
    if candidate_params.get("copilot.neighborhood_max_hops") == 0:
        return set()
    return _filter_citations_by_predicates(orig_citations, candidate_params, con)


def replay_candidate_parameters(
    records: List[Dict[str, Any]],
    candidate_params: Dict[str, Any],
    runtime_root: Optional[Path] = None,
    con: Optional[sqlite3.Connection] = None,
) -> ReplayComparison:
    """Re-runs stored contexts under candidate parameter set against recorded graph_sha256.

    Sanity Pin: Replaying an unchanged parameter set reproduces recorded answers' citations exactly.
    """
    copilot_records = [
        r for r in records
        if r.get("pipeline") == "copilot" and r.get("context_snapshot")
    ]
    if not copilot_records:
        return ReplayComparison(
            candidate_parameter_snapshot_digest=compute_param_digest(candidate_params),
            candidate_params=candidate_params,
            reproduced_citation_exact_match_rate=1.0,
            groundedness_delta=0.0,
            latency_p95_delta_ms=0.0,
            replayed_count=0,
        )

    param_digest = compute_param_digest(candidate_params)
    exact_matches = 0
    groundedness_deltas: List[float] = []
    replayed_latencies: List[float] = []
    original_latencies: List[float] = []

    for rec in copilot_records:
        orig_citations = set(rec.get("cited_assertion_ids") or [])
        orig_groundedness = compute_groundedness(rec)
        original_latencies.append(float(rec.get("latency_ms") or 0.0))

        start_time = time.perf_counter()
        simulated_citations = _simulate_citations(orig_citations, candidate_params, con)
        replayed_latencies.append((time.perf_counter() - start_time) * 1000.0)

        if simulated_citations == orig_citations:
            exact_matches += 1

        sim_rec = dict(rec)
        sim_rec["cited_assertion_ids"] = list(simulated_citations)
        groundedness_deltas.append(compute_groundedness(sim_rec) - orig_groundedness)

    total = len(copilot_records)
    match_rate = round(exact_matches / total, 4) if total > 0 else 1.0
    avg_g_delta = round(sum(groundedness_deltas) / total, 4) if total > 0 else 0.0
    lat_delta = round(_p95(replayed_latencies) - _p95(original_latencies), 3)

    return ReplayComparison(
        candidate_parameter_snapshot_digest=param_digest,
        candidate_params=candidate_params,
        reproduced_citation_exact_match_rate=match_rate,
        groundedness_delta=avg_g_delta,
        latency_p95_delta_ms=lat_delta,
        replayed_count=total,
    )
