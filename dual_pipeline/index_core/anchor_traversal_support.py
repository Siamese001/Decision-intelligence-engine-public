"""Support functions for graph traversal anchor expansion."""
from __future__ import annotations

from typing import Any

from dual_pipeline.core import (
    normalize_iso_date,
    normalize_space,
    sha256_bytes,
    stable_id,
)
from dual_pipeline.label_quality import canonicalize_audiences

from .constants import CLASSIFICATION_RANK
from .eligibility import classify_retrieval_eligibility

VALID_CLASSIFICATIONS = frozenset(
    {"public", "external_approved", "internal", "confidential", "restricted"}
)


def validate_classification(value: Any) -> str:
    val = str(value or "").strip().lower()
    if val not in VALID_CLASSIFICATIONS:
        raise ValueError(f"unsupported retrieval classification: {value}")
    return val


def normalize_classification(value: Any) -> str:
    val = str(value or "").strip().lower()
    if val == "external_approved":
        return "public"
    return val if val in {"public", "internal", "confidential", "restricted"} else "internal"


def classification_rank(value: Any) -> int:
    return CLASSIFICATION_RANK.get(normalize_classification(value), 1)


def format_assertion_text(row: dict[str, Any]) -> str:
    subj = normalize_space(str(row.get("subject_name") or ""))
    pred = normalize_space(str(row.get("predicate") or "").lower().replace("_", " "))
    claim = normalize_space(str(row.get("object_literal") or row.get("object_name") or ""))
    scope = normalize_space(str(row.get("scope_text") or ""))
    speaker = normalize_space(str(row.get("speaker_or_author") or ""))
    date = normalize_space(str(row.get("valid_from") or ""))

    text = claim if claim.lower().startswith(subj.lower()) else (
        f"{subj} {pred}: {claim}" if pred else f"{subj}: {claim}"
    )
    parts = []
    if scope and scope.lower() not in text.lower():
        parts.append(f"Scope: {scope}")
    if speaker:
        parts.append(f"Speaker: {speaker}")
    if date:
        parts.append(f"Date: {date}")
    if parts:
        text = f"{text}. " + ". ".join(parts) + "."
    return normalize_space(text + ("" if text.endswith(".") else "."))


def score_assertion_relevance(q_tokens: list[str], asrt: dict[str, Any]) -> int:
    if not q_tokens:
        return 0
    s_name = str(asrt.get("subject_name") or "").lower()
    o_name = str(asrt.get("object_name") or "").lower()
    pred = str(asrt.get("predicate") or "").lower().replace("_", " ")
    claim = str(asrt.get("object_literal") or "").lower()
    entity_tokens = {
        tok
        for tok in q_tokens
        if tok in s_name or tok in o_name
    }
    topic_tokens = [tok for tok in q_tokens if tok not in entity_tokens]
    if topic_tokens and not any(tok in pred or tok in claim for tok in topic_tokens):
        return 0
    score = 0
    for tok in q_tokens:
        score += 3 if tok in pred else 0
        score += 2 if (tok in s_name or tok in o_name) else 0
        score += 1 if tok in claim else 0
    return score


def _is_assertion_temporally_valid(asrt: dict[str, Any]) -> bool:
    import json
    from datetime import datetime, timezone

    # Supersession checks
    if asrt.get("superseded_by_assertion_id") or asrt.get("superseded_at"):
        return False
    if str(asrt.get("status", "")).upper() in (
        "SUPERSEDED", "RETRACTED", "REJECTED", "DELETED", "INACTIVE"
    ):
        return False
    if str(asrt.get("currentness_state", "")).lower() in (
        "superseded", "stale", "retracted"
    ):
        return False

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%d")
    now_ts = now_dt.timestamp()

    # valid_to checks
    valid_to = asrt.get("valid_to")
    if valid_to:
        v_str = str(valid_to).strip()
        try:
            if "/" in v_str:
                parts = v_str.split("/")
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    v_dt = datetime(y, m, d, tzinfo=timezone.utc)
                    if v_dt.timestamp() < now_ts:
                        return False
            else:
                if v_str[:10] < now_iso:
                    return False
        except Exception:
            pass

    # stale_after checks
    stale_after = asrt.get("stale_after")
    if not stale_after and asrt.get("qualifiers_json"):
        try:
            quals = json.loads(asrt["qualifiers_json"])
            if isinstance(quals, dict):
                stale_after = quals.get("stale_after")
        except Exception:
            stale_after = None

    if stale_after:
        s_str = str(stale_after).strip()
        try:
            if "/" in s_str:
                parts = s_str.split("/")
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    s_dt = datetime(y, m, d, tzinfo=timezone.utc)
                    if s_dt.timestamp() < now_ts:
                        return False
            else:
                if s_str[:10] < now_iso:
                    return False
        except Exception:
            pass

    return True


def fetch_validated_assertion(
    conn: Any,
    aid: str,
    *,
    lane: str,
    req_rank: int,
    req_audiences: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    row = conn.execute(
        """SELECT a.*, sn.canonical_name AS subject_name, onode.canonical_name AS object_name
           FROM assertion a
           JOIN node sn ON sn.node_id = a.subject_node_id
           LEFT JOIN node onode ON onode.node_id = a.object_node_id
           WHERE a.assertion_id = ? AND a.currentness_state IN ('current', 'unknown')""",
        (aid,),
    ).fetchone()
    if not row:
        return None, []
    asrt = dict(row)
    if not _is_assertion_temporally_valid(asrt):
        return None, []

    ev_rows = conn.execute(
        """SELECT evidence_id FROM assertion_evidence
           WHERE assertion_id = ? AND evidence_relationship = 'supports'
           ORDER BY evidence_id""",
        (aid,),
    ).fetchall()
    evidence_ids = [r[0] for r in ev_rows]

    from .eligibility import effective_retrieval_lane

    eligible, _ = classify_retrieval_eligibility(
        asrt,
        has_supporting_evidence=bool(evidence_ids),
        allow_unapproved_canonical_as_candidate=True,
    )
    if not eligible:
        return None, []

    asrt_lane = effective_retrieval_lane(asrt)
    if asrt_lane != lane or classification_rank(asrt.get("classification")) > req_rank:
        return None, []

    asrt_auds = set(canonicalize_audiences(asrt.get("allowed_audiences_json", "[]")))
    if not (req_audiences & asrt_auds):
        return None, []

    return asrt, evidence_ids


def build_expanded_item(
    asrt: dict[str, Any],
    evidence_ids: list[str],
    *,
    anchor_id: str,
    anchor_score: float,
    lane: str,
    traversal_kind: str,
    hops: int,
    base_item: dict[str, Any],
) -> dict[str, Any]:
    aid = asrt["assertion_id"]
    claim_text = format_assertion_text(asrt)
    canonical_entities = [
        nid for nid in [asrt.get("subject_node_id"), asrt.get("object_node_id")] if nid
    ]
    asrt_auds = sorted(canonicalize_audiences(asrt.get("allowed_audiences_json", "[]")))
    ans_use = "factual_answer" if lane == "approved" else "candidate_hypothesis"
    canon_rec_id = stable_id("retrieval-record", aid, length=28)

    exp_record = {
        "record_id": f"expanded-assertion-{aid}",
        "record_type": "assertion",
        "namespace": f"nee.{lane}.assertions",
        "lane": lane,
        "title": f"Fact: {asrt.get('subject_name')}",
        "canonical_entities": canonical_entities,
        "retrieval_text": claim_text,
        "retrieval_text_hash": sha256_bytes(claim_text.encode("utf-8")),
        "evidence_cluster_id": stable_id("evidence-cluster", [aid], length=24),
        "evidence_ids": evidence_ids,
        "canonical_record_id": canon_rec_id,
        "graph_snapshot": base_item.get("record", {}).get("graph_snapshot", "expansion"),
        "classification": asrt.get("classification"),
        "audiences": asrt_auds,
        "lifecycle": "current",
        "effective_date": normalize_iso_date(asrt.get("valid_from")),
        "action_status": None,
        "retrieval_kind": "assertion",
        "epistemic_status": "approved" if lane == "approved" else "candidate",
        "provenance_kind": "source_bound",
        "answer_use": ans_use,
        "extensions": {
            "com.partner_advisory.nee.graph-assertion-ids": [aid],
            "com.partner_advisory.nee.graph-object-id": aid,
            "com.partner_advisory.nee.graph-evidence-ids": evidence_ids,
            "com.partner_advisory.nee.graph-entity-ids": canonical_entities,
            "com.partner_advisory.nee.canonical-record-id": canon_rec_id,
            "com.partner_advisory.nee.expanded-from-anchor": anchor_id,
            "com.partner_advisory.nee.traversal-kind": traversal_kind,
            "com.partner_advisory.nee.traversal-hops": hops,
            "com.partner_advisory.nee.predicate": asrt.get("predicate"),
        },
        "predicate": asrt.get("predicate"),
    }

    decay = 0.95 if hops == 1 else 0.81
    return {
        "record_id": exp_record["record_id"],
        "canonical_record_id": canon_rec_id,
        "lane": lane,
        "evidence_stratum": base_item.get("evidence_stratum", "PRIMARY"),
        "fused_rank": base_item.get("fused_rank", 1),
        "fused_score": round(anchor_score * decay, 6),
        "dense_score": base_item.get("dense_score"),
        "sparse_score": base_item.get("sparse_score"),
        "colbert_score": base_item.get("colbert_score"),
        "is_anchor_expanded": True,
        "anchor_record_id": anchor_id,
        "anchor_kind": traversal_kind.split("_")[0],
        "traversal_hops": hops,
        "record": exp_record,
    }


def seed_seen_assertions(results: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for item in results:
        rec = item.get("record", {})
        rtype = rec.get("record_type", "")
        ext = rec.get("extensions", {})
        if rtype in {"assertion", "edge"}:
            obj_id = ext.get("com.partner_advisory.nee.graph-object-id")
            if obj_id:
                seen.add(str(obj_id))
            for aid in ext.get("com.partner_advisory.nee.graph-assertion-ids", []):
                if aid:
                    seen.add(str(aid))
        rid = str(item.get("record_id", ""))
        if rid.startswith("expanded-assertion-"):
            seen.add(rid[len("expanded-assertion-"):])
    return seen
