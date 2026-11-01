"""Deterministic metric computation engine for Answer Evaluation."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from storage.connection import sqlite3

from .models import LatencyStats, OperatorUsefulnessStats


def resolve_citation_to_assertion_ids(
    cid: str,
    con: Optional[sqlite3.Connection],
) -> List[str]:
    """Resolves a citation identifier (raw assertion_id or retrieval-record/entity-facet ID) to canonical assertion IDs."""
    if not cid or con is None:
        return [cid] if cid else []

    # 1. Direct hit in assertion table
    try:
        cur = con.execute("SELECT 1 FROM assertion WHERE assertion_id = ?", (cid,))
        if cur.fetchone():
            return [cid]
    except sqlite3.OperationalError:
        pass

    # 2. Lookup in retrieval_record artifact_json
    try:
        cur = con.execute("SELECT artifact_json FROM retrieval_record WHERE record_id = ?", (cid,))
        row = cur.fetchone()
        if row and row[0]:
            art = json.loads(row[0])
            ext = art.get("extensions", {})
            obj_id = ext.get("com.partner_advisory.nee.graph-object-id")
            if obj_id:
                return [str(obj_id)]
            ast_ids = ext.get("com.partner_advisory.nee.graph-assertion-ids")
            if ast_ids and isinstance(ast_ids, (list, tuple)):
                return [str(a) for a in ast_ids if a]
    except Exception:
        pass

    # 3. Lookup in retrieval_record_object_facet
    try:
        cur = con.execute(
            "SELECT object_id FROM retrieval_record_object_facet WHERE record_id = ? AND object_type = 'assertion'",
            (cid,),
        )
        rows = cur.fetchall()
        if rows:
            return [str(r[0]) for r in rows if r[0]]
    except Exception:
        pass

    return [cid]


_INLINE_CITATION_RE = re.compile(r"ast-[0-9a-zA-Z_-]+")


def _is_sentence_grounded(line: str, cited_ids: List[str], cited_set: set[str]) -> bool:
    """Return True if line has verified citations and zero fabricated inline ast-... tokens."""
    matched_inline = _INLINE_CITATION_RE.findall(line)
    if matched_inline:
        return all(token in cited_set for token in matched_inline)
    return any(
        re.search(rf"(?<![0-9a-zA-Z_-]){re.escape(cid)}(?![0-9a-zA-Z_-])", line)
        for cid in cited_ids
    )


def compute_groundedness(record: Dict[str, Any]) -> float:
    """Share of sentences in response_text with a verified citation in cited_assertion_ids."""
    text = record.get("response_text", "") or ""
    cited_ids = [str(cid) for cid in (record.get("cited_assertion_ids") or []) if cid]
    if not cited_ids:
        return 0.0
    cited_set = set(cited_ids)

    lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    content_lines = [
        s for s in lines
        if not s.startswith(("#", "===", "---"))
    ]
    if not content_lines:
        return 0.0

    grounded_count = sum(
        1 for line in content_lines if _is_sentence_grounded(line, cited_ids, cited_set)
    )
    return min(1.0, max(0.0, grounded_count / len(content_lines))) if grounded_count > 0 else 0.0


def compute_citation_durability(
    cited_ids: List[str],
    con: Optional[sqlite3.Connection],
) -> float:
    """Checks whether cited assertions remain active and eligible in the graph."""
    if not cited_ids:
        return 0.0
    if con is None:
        return 0.0

    active_count = 0
    resolved_total = 0
    for raw_cid in cited_ids:
        resolved_aids = resolve_citation_to_assertion_ids(raw_cid, con)
        for cid in resolved_aids:
            resolved_total += 1
            try:
                cursor = con.execute(
                    "SELECT canonical_status, retrieval_eligible FROM assertion WHERE assertion_id = ?",
                    (cid,),
                )
                row = cursor.fetchone()
                if row:
                    status, eligible = row[0], row[1]
                    if status not in ("revoked", "superseded", "quarantined") and eligible != 0:
                        active_count += 1
            except sqlite3.OperationalError:
                pass

    if resolved_total == 0:
        return 0.0
    return min(1.0, max(0.0, active_count / resolved_total))


def compute_staleness_and_conflict_miss(
    record: Dict[str, Any],
    con: Optional[sqlite3.Connection],
) -> Tuple[float, float]:
    """Computes staleness rate and conflict-miss rate against graph state."""
    raw_cited_ids = record.get("cited_assertion_ids") or []
    response_text = record.get("response_text", "") or ""
    if not raw_cited_ids or con is None:
        return 0.0, 0.0

    resolved_cited_ids: List[str] = []
    for raw_cid in raw_cited_ids:
        resolved_cited_ids.extend(resolve_citation_to_assertion_ids(raw_cid, con))

    if not resolved_cited_ids:
        return 0.0, 0.0

    stale_count = 0
    conflict_miss_count = 0

    for cid in resolved_cited_ids:
        try:
            cursor = con.execute(
                "SELECT subject_node_id, predicate, source_date_normalized FROM assertion WHERE assertion_id = ?",
                (cid,),
            )
            row = cursor.fetchone()
            if not row:
                continue
            subj, pred, sdate = row[0], row[1], row[2] or ""

            newer_cur = con.execute(
                """SELECT assertion_id FROM assertion
                   WHERE subject_node_id = ? AND predicate = ? AND source_date_normalized > ?
                   AND assertion_id != ? AND canonical_status != 'revoked'""",
                (subj, pred, sdate, cid),
            )
            if newer_cur.fetchone():
                stale_count += 1

            conflict_cur = con.execute(
                """SELECT e.evidence_relationship FROM assertion_evidence e
                   WHERE e.assertion_id = ? AND e.evidence_relationship IN ('CONTRADICTS', 'CONFLICTS')""",
                (cid,),
            )
            if conflict_cur.fetchone() and "CONTRADICTION" not in response_text and "CONFLICT" not in response_text:
                conflict_miss_count += 1
        except sqlite3.OperationalError:
            pass

    staleness_rate = stale_count / len(resolved_cited_ids) if resolved_cited_ids else 0.0
    conflict_miss_rate = 1.0 if conflict_miss_count > 0 else 0.0
    return min(1.0, staleness_rate), min(1.0, conflict_miss_rate)


def compute_context_recall(record: Dict[str, Any]) -> float:
    """Whether answers drew on decayed-salient entities when relevant."""
    snapshot = record.get("context_snapshot") or []
    seeds = set(record.get("retrieval_seeds") or [])
    response_text = record.get("response_text", "").lower()

    salient_entities: set[str] = set()
    for turn in snapshot:
        text = turn.get("text", "")
        matches = re.findall(r"\b[A-Z][a-zA-Z0-9_]+(?:\s+[A-Z][a-zA-Z0-9_]+)*\b", text)
        for m in matches:
            if len(m) > 3 and m.lower() not in {"what", "when", "where", "this", "that", "the"}:
                salient_entities.add(m.lower())

    if not salient_entities:
        return 1.0

    recalled = 0
    seeds_lower = {s.lower() for s in seeds}
    for ent in salient_entities:
        if ent in seeds_lower or ent in response_text:
            recalled += 1

    return min(1.0, max(0.0, recalled / len(salient_entities)))


def compute_operator_usefulness(
    feedback_list: List[Dict[str, Any]],
) -> OperatorUsefulnessStats:
    """Computes Beta posterior over useful vs not_useful/wrong clamped to [0.05, 0.95]."""
    useful = sum(1 for fb in feedback_list if fb.get("rating") == "useful")
    not_useful = sum(1 for fb in feedback_list if fb.get("rating") == "not_useful")
    wrong = sum(1 for fb in feedback_list if fb.get("rating") == "wrong")
    total = len(feedback_list)

    alpha_0, beta_0 = 1.0, 1.0
    posterior_mean = (alpha_0 + useful) / (alpha_0 + beta_0 + useful + not_useful + wrong)
    clamped_mean = round(min(0.95, max(0.05, posterior_mean)), 4)

    return OperatorUsefulnessStats(
        useful_count=useful,
        not_useful_count=not_useful,
        wrong_count=wrong,
        total_feedback=total,
        beta_posterior_mean=clamped_mean,
    )


def compute_post_meeting_uptake(
    feedback_list: List[Dict[str, Any]],
    total_records: int,
) -> float:
    """Computes fraction of answers referenced in post-meeting recap (implicit uptake)."""
    if total_records <= 0:
        return 0.0
    implicit_useful = sum(
        1 for fb in feedback_list
        if fb.get("origin") == "recap_implicit" or fb.get("source") == "recap_implicit"
    )
    return min(1.0, max(0.0, round(implicit_useful / total_records, 4)))


def compute_latency_stats(records: List[Dict[str, Any]]) -> LatencyStats:
    """Computes p50 and p95 latency overall and by rigor tier."""
    latencies = [
        float(r["latency_ms"]) for r in records
        if r.get("latency_ms") is not None and not math.isnan(float(r["latency_ms"]))
    ]

    def _percentile(vals: List[float], pct: float) -> float:
        if not vals:
            return 0.0
        sorted_vals = sorted(vals)
        idx = int(math.ceil(pct * len(sorted_vals))) - 1
        return round(sorted_vals[max(0, min(idx, len(sorted_vals) - 1))], 3)

    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)

    by_tier: Dict[str, Dict[str, Any]] = {}
    tier_groups: Dict[str, List[float]] = {}
    for r in records:
        tier = (r.get("rigor") or "STANDARD").upper()
        l_ms = r.get("latency_ms")
        if l_ms is not None:
            tier_groups.setdefault(tier, []).append(float(l_ms))

    for tier, vals in tier_groups.items():
        by_tier[tier] = {
            "p50_ms": _percentile(vals, 0.50),
            "p95_ms": _percentile(vals, 0.95),
            "count": len(vals),
        }

    return LatencyStats(p50_ms=p50, p95_ms=p95, by_rigor_tier=by_tier)


def compute_fail_closed_rate(records: List[Dict[str, Any]]) -> float:
    """Computes fraction of answers that failed closed or returned empty due to budget."""
    if not records:
        return 0.0
    fail_count = 0
    for r in records:
        text = (r.get("response_text") or "").lower()
        if (
            "fail-closed" in text
            or "zero assertions found" in text
            or "budget exceeded" in text
            or r.get("status") == "FAIL"
        ):
            fail_count += 1
    return min(1.0, max(0.0, round(fail_count / len(records), 4)))


def compute_aggregate_metrics(
    records: List[Dict[str, Any]],
    feedback_list: List[Dict[str, Any]],
    con: Optional[sqlite3.Connection],
) -> Dict[str, Any]:
    """Computes all aggregate metrics across AnswerRecord set."""
    if not records:
        return {
            "groundedness": 0.0,
            "citation_durability": 0.0,
            "staleness_rate": 0.0,
            "conflict_miss_rate": 0.0,
            "context_recall": 0.0,
            "operator_usefulness": compute_operator_usefulness(feedback_list).to_dict(),
            "post_meeting_uptake": 0.0,
            "latency": LatencyStats().to_dict(),
            "fail_closed_rate": 0.0,
            "by_intent_or_type": {},
        }

    groundedness_scores = [compute_groundedness(r) for r in records]
    avg_groundedness = round(sum(groundedness_scores) / len(records), 4)

    durability_scores: List[float] = []
    staleness_scores: List[float] = []
    conflict_scores: List[float] = []
    recall_scores: List[float] = []

    by_intent: Dict[str, Dict[str, Any]] = {}

    for r in records:
        cids = r.get("cited_assertion_ids") or []
        dur = compute_citation_durability(cids, con)
        durability_scores.append(dur)

        stale, conf = compute_staleness_and_conflict_miss(r, con)
        staleness_scores.append(stale)
        conflict_scores.append(conf)

        recall = compute_context_recall(r)
        recall_scores.append(recall)

        intent = r.get("intent_or_type") or "unknown"
        if intent not in by_intent:
            by_intent[intent] = {"count": 0, "groundedness_sum": 0.0, "durability_sum": 0.0}
        by_intent[intent]["count"] += 1
        by_intent[intent]["groundedness_sum"] += compute_groundedness(r)
        by_intent[intent]["durability_sum"] += dur

    intent_breakdown = {
        k: {
            "count": v["count"],
            "groundedness": round(v["groundedness_sum"] / v["count"], 4),
            "citation_durability": round(v["durability_sum"] / v["count"], 4),
        }
        for k, v in by_intent.items()
    }

    return {
        "groundedness": avg_groundedness,
        "citation_durability": round(sum(durability_scores) / len(records), 4),
        "staleness_rate": round(sum(staleness_scores) / len(records), 4),
        "conflict_miss_rate": round(sum(conflict_scores) / len(records), 4),
        "context_recall": round(sum(recall_scores) / len(records), 4),
        "operator_usefulness": compute_operator_usefulness(feedback_list).to_dict(),
        "post_meeting_uptake": compute_post_meeting_uptake(feedback_list, len(records)),
        "latency": compute_latency_stats(records).to_dict(),
        "fail_closed_rate": compute_fail_closed_rate(records),
        "by_intent_or_type": intent_breakdown,
    }
