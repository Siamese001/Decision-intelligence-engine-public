"""V0 fact reachability audit: proves all current graph facts are answerable or policy-excluded."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..core import connect_sqlite, digest, stable_id, utc_now, write_json
from .eligibility import classify_retrieval_eligibility
from .policy import (
    DEFAULT_POLICY_PATH,
    classify_policy_proposition_eligibility,
    load_semantic_unit_policy,
)

logger = logging.getLogger("dual_pipeline.index_core.v0_audit")


def verify_v0_fact_reachability(
    *,
    graph_path: Path,
    sidecar_path: Path,
    generation_id: str,
    directive_id: str | None = None,
    runtime_root: Path | None = None,
    policy_path: Path | None = None,
    hop_budget: int = 1,
) -> dict[str, Any]:
    """Evaluates whether 100% of current graph facts are directly embedded, reachable, or excluded."""
    resolved_graph = Path(graph_path).expanduser().resolve()
    resolved_sidecar = Path(sidecar_path).expanduser().resolve()

    if not resolved_graph.is_file():
        raise FileNotFoundError(f"graph database not found: {resolved_graph}")
    if not resolved_sidecar.is_file():
        raise FileNotFoundError(f"sidecar database not found: {resolved_sidecar}")

    policy_file = policy_path or DEFAULT_POLICY_PATH
    policy = load_semantic_unit_policy(policy_file)

    # 1. Read sidecar actual unit records
    sconn = connect_sqlite(resolved_sidecar, readonly=True)
    try:
        sidecar_rows = sconn.execute(
            "SELECT record_type, artifact_json FROM retrieval_record"
        ).fetchall()
    finally:
        sconn.close()

    direct_aids: set[str] = set()
    passage_ev_ids: set[str] = set()
    profile_entity_ids: set[str] = set()

    for rtype, art_str in sidecar_rows:
        art = json.loads(art_str)
        ext = art.get("extensions", {})
        if ext.get("com.partner_advisory.nee.graph-object-id"):
            direct_aids.add(ext["com.partner_advisory.nee.graph-object-id"])
        for aid in ext.get("com.partner_advisory.nee.graph-assertion-ids", []):
            direct_aids.add(aid)
        if rtype in ("source_chunk", "evidence_span"):
            for eid in art.get("evidence_ids", []):
                passage_ev_ids.add(eid)
            for geid in ext.get("com.partner_advisory.nee.graph-evidence-ids", []):
                passage_ev_ids.add(geid)
        if rtype == "entity_facet":
            for eid in ext.get("com.partner_advisory.nee.graph-entity-ids", []):
                profile_entity_ids.add(eid)
            for ce in art.get("canonical_entities", []):
                profile_entity_ids.add(ce)

    # 2. Read graph current assertions and supporting evidence
    gconn = connect_sqlite(resolved_graph, readonly=True)
    try:
        ev_map: dict[str, list[str]] = {}
        # Support both evidence schema variations
        ev_cols = {col[1] for col in gconn.execute("PRAGMA table_info(assertion_evidence)").fetchall()}
        evidence_id_col = "evidence_id" if "evidence_id" in ev_cols else "evidence_item_id"
        rel_clause = "WHERE evidence_relationship='supports'" if "evidence_relationship" in ev_cols else ""
        for r in gconn.execute(f"SELECT assertion_id, {evidence_id_col} FROM assertion_evidence {rel_clause}"):
            ev_map.setdefault(r[0], []).append(r[1])

        # Support both assertion schema variations
        ass_cols = {col[1] for col in gconn.execute("PRAGMA table_info(assertion)").fetchall()}
        where_clause = ""
        if "currentness_state" in ass_cols:
            where_clause = "WHERE currentness_state IN ('current', 'unknown')"
        elif "status" in ass_cols:
            where_clause = "WHERE status IN ('approved', 'canonical', 'needs_review', 'unreviewed')"

        assertions = gconn.execute(f"SELECT * FROM assertion {where_clause}").fetchall()
    finally:
        gconn.close()

    direct_count = 0
    passage_count = 0
    profile_count = 0
    excluded_count = 0
    unexplained_unreachable: list[str] = []

    for r in assertions:
        row = dict(r)
        aid = str(row.get("assertion_id"))
        sn = str(row.get("subject_node_id") or "")
        on = str(row.get("object_node_id") or "")
        supp_evs = ev_map.get(aid, [])
        has_ev = len(supp_evs) > 0

        # Case A: Directly embedded in sidecar
        if aid in direct_aids:
            direct_count += 1
            continue

        # Case B: Supported by passage in sidecar
        if has_ev and any(ev in passage_ev_ids for ev in supp_evs):
            passage_count += 1
            continue

        # Case C: 1-hop reachable from entity profile in sidecar
        if hop_budget >= 1 and ((sn in profile_entity_ids) or (on and on in profile_entity_ids)):
            profile_count += 1
            continue

        # Case D: Policy-excluded
        el, reas = classify_retrieval_eligibility(row, has_supporting_evidence=has_ev)
        if not el:
            excluded_count += 1
            continue

        is_prop, prop_reas = classify_policy_proposition_eligibility(row, policy)
        if not is_prop:
            excluded_count += 1
            continue

        # If eligible under policy and not in sidecar / reachable -> UNEXPLAINED GAP!
        unexplained_unreachable.append(aid)

    unexplained_count = len(unexplained_unreachable)
    status_str = "PASS" if unexplained_count == 0 else "FAIL"
    receipt_id = stable_id(
        "v0-fact-reachability",
        directive_id or "unbound",
        generation_id,
        length=28,
    )

    receipt: dict[str, Any] = {
        "receipt_id": receipt_id,
        "contract": "urn:nee:dual-pipeline:v0-fact-reachability:1.0.0",
        "evaluated_at": utc_now(),
        "status": status_str,
        "generation_id": generation_id,
        "directive_id": directive_id,
        "graph_path": str(resolved_graph),
        "sidecar_path": str(resolved_sidecar),
        "policy_id": policy.get("policy_id", "semantic-unit-policy-v1"),
        "total_current_facts": len(assertions),
        "direct_proposition_count": direct_count,
        "passage_supported_count": passage_count,
        "profile_reachable_count": profile_count,
        "policy_excluded_count": excluded_count,
        "unreachable_count": unexplained_count,
        "unreachable_ids": unexplained_unreachable[:20],
    }
    if unexplained_count > 0:
        receipt["error"] = (
            f"V0_FACT_REACHABILITY: {unexplained_count} unexplained unreachable facts detected: {unexplained_unreachable[:10]}"
        )

    receipt["receipt_digest"] = digest(receipt)

    if runtime_root:
        out_dir = Path(runtime_root) / "v0_receipts"
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json(out_dir / f"{receipt_id}.json", receipt)

    return receipt
