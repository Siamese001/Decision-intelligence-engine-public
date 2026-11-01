"""Semantic unit policy loading, schema validation, ledger alignment, and reachability projection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from ..core import ROOT, artifact_digest, connect_sqlite
from .eligibility import classify_retrieval_eligibility

DEFAULT_POLICY_PATH = (
    ROOT / "governance" / "decision_intelligence" / "semantic_unit_policy.json"
)
DEFAULT_SCHEMA_PATH = ROOT / "schemas" / "semantic-unit-policy.schema.json"


def verify_policy_ledger_alignment(
    policy: dict[str, Any],
    ledger_path: Path | str | None = None,
) -> tuple[bool, str | None]:
    """Verify that policy decisions match authentic records in hitl_decision_ledger.json."""
    candidate_paths: list[Path] = []
    if ledger_path:
        candidate_paths.append(Path(ledger_path).expanduser().resolve())
    else:
        candidate_paths.extend([
            (ROOT / "hitl_decision_ledger.json").resolve(),
            (ROOT / ".local" / "hitl_decision_ledger.json").resolve(),
            (ROOT / ".local" / "ledger" / "hitl_decision_ledger.json").resolve(),
        ])

    target_id = policy.get("approval_id")
    ledger_envelope = None

    for lp in candidate_paths:
        if not lp.is_file():
            continue
        try:
            with open(lp, encoding="utf-8") as f:
                ldata = json.load(f)
        except Exception:
            continue

        for item in ldata.get("semantic_unit_policy_decisions", []):
            if item.get("directive_id") == target_id:
                ledger_envelope = item
                break
        if ledger_envelope:
            break

    if not ledger_envelope:
        return False, f"no ledger envelope matching directive: {target_id}"

    ledger_dec_map = {
        d["decision_id"]: d for d in ledger_envelope.get("decisions", [])
    }
    for dec in policy.get("ledger_reference", {}).get("decisions", []):
        did = dec.get("decision_id")
        if did not in ledger_dec_map:
            return False, f"decision {did} missing from ledger envelope"
        ld = ledger_dec_map[did]
        if ld.get("selected_option") != dec.get("selected_option"):
            return (
                False,
                f"decision {did} option mismatch: {ld.get('selected_option')} vs {dec.get('selected_option')}",
            )
        if ld.get("signature") != dec.get("signature"):
            return False, f"decision {did} signature mismatch"

    return True, None


def load_semantic_unit_policy(
    policy_path: Path | str | None = None,
    *,
    schema_path: Path | str | None = None,
    verify_ledger: bool = True,
) -> dict[str, Any]:
    """Load, validate against JSON Schema, and verify digest & ledger of semantic unit policy."""
    pp = Path(policy_path or DEFAULT_POLICY_PATH).expanduser().resolve()
    if not pp.is_file():
        raise FileNotFoundError(f"semantic unit policy file not found: {pp}")

    with open(pp, encoding="utf-8") as f:
        policy = json.load(f)

    # 1. Canonical digest verification
    declared_digest = (
        policy.get("canonical_digest", {}).get("value")
        if isinstance(policy.get("canonical_digest"), dict)
        else None
    )
    expected_digest = artifact_digest(policy)
    if declared_digest != expected_digest:
        raise ValueError(
            f"policy canonical digest mismatch: declared {declared_digest} vs expected {expected_digest}"
        )

    # 2. JSON Schema validation
    sp = Path(schema_path or DEFAULT_SCHEMA_PATH).expanduser().resolve()
    if sp.is_file():
        with open(sp, encoding="utf-8") as sf:
            schema = json.load(sf)
        jsonschema.validate(instance=policy, schema=schema)

    # 3. Ledger alignment
    if verify_ledger:
        aligned, err = verify_policy_ledger_alignment(policy)
        if not aligned:
            raise ValueError(f"policy ledger alignment verification failed: {err}")

    return policy


def classify_policy_proposition_eligibility(
    row: Mapping[str, Any],
    policy: dict[str, Any],
) -> tuple[bool, str]:
    """Determine whether an assertion row is eligible to be a direct proposition unit."""
    prop_pol = policy.get("proposition_eligibility", {})
    eligible_types = set(prop_pol.get("eligible_assertion_types", []))
    require_literal = prop_pol.get("require_literal_claim", True)
    exclude_edges = prop_pol.get("exclude_structural_edges", True)

    atype = str(row.get("assertion_type") or "")
    lit = str(row.get("object_literal") or "").strip()
    oid = str(row.get("object_node_id") or "").strip()

    if require_literal and (not lit or len(lit) < 3):
        if exclude_edges and oid:
            return False, "EXCLUDED_STRUCTURAL_EDGE_TRAVERSAL"
        return False, "EXCLUDED_MISSING_LITERAL_CLAIM"

    if eligible_types and atype not in eligible_types:
        return False, f"EXCLUDED_ASSERTION_TYPE_{atype.upper()}"

    return True, "ELIGIBLE_PROPOSITION"


def project_policy_reachability(
    *,
    graph_path: Path,
    sidecar_path: Path,
    policy: dict[str, Any],
    hop_budget: int = 1,
) -> dict[str, Any]:
    """Project reachability, unit counts, and edge removal under approved semantic-unit policy."""
    if not graph_path.is_file():
        raise FileNotFoundError(f"graph database not found: {graph_path}")
    if not sidecar_path.is_file():
        raise FileNotFoundError(f"sidecar index not found: {sidecar_path}")

    s_conn = connect_sqlite(sidecar_path, readonly=True)
    try:
        rec_rows = s_conn.execute(
            "SELECT record_id, record_type, artifact_json FROM retrieval_record"
        ).fetchall()
    finally:
        s_conn.close()

    baseline_direct_aids: set[str] = set()
    profile_entity_ids: set[str] = set()
    for row in rec_rows:
        art = json.loads(row["artifact_json"])
        ext = art.get("extensions", {})
        if ext.get("com.partner_advisory.nee.graph-object-id"):
            baseline_direct_aids.add(ext["com.partner_advisory.nee.graph-object-id"])
        for aid in ext.get("com.partner_advisory.nee.graph-assertion-ids", []):
            baseline_direct_aids.add(aid)
        if row["record_type"] == "entity_facet":
            for eid in ext.get("com.partner_advisory.nee.graph-entity-ids", []):
                profile_entity_ids.add(eid)
            for ce in art.get("canonical_entities", []):
                profile_entity_ids.add(ce)

    g_conn = connect_sqlite(graph_path, readonly=True)
    try:
        ev_map: dict[str, list[str]] = {}
        for r in g_conn.execute(
            """SELECT assertion_id, evidence_id FROM assertion_evidence
               WHERE evidence_relationship='supports'"""
        ):
            ev_map.setdefault(r["assertion_id"], []).append(r["evidence_id"])

        current_assertions = g_conn.execute(
            "SELECT * FROM assertion WHERE currentness_state IN ('current', 'unknown')"
        ).fetchall()
    finally:
        g_conn.close()

    total_current = len(current_assertions)
    direct_propositions = 0
    dense_edges_removed = 0
    reachable_via_passage = 0
    reachable_via_profile = 0
    excluded_policy: dict[str, int] = {}
    unreachable_unexplained: list[str] = []

    for r in current_assertions:
        row = dict(r)
        aid = str(row.get("assertion_id"))
        sn = str(row.get("subject_node_id") or "")
        on = str(row.get("object_node_id") or "")
        supp_evs = ev_map.get(aid, [])
        has_ev = len(supp_evs) > 0

        lit = str(row.get("object_literal") or "").strip()
        is_prop, _ = classify_policy_proposition_eligibility(row, policy)

        if is_prop and lit and len(lit) > 3:
            el, reas = classify_retrieval_eligibility(row, has_supporting_evidence=has_ev)
            if el:
                direct_propositions += 1
                continue

        if has_ev and aid in baseline_direct_aids:
            dense_edges_removed += 1
            reachable_via_passage += 1
            continue

        if hop_budget >= 1 and ((sn in profile_entity_ids) or (on and on in profile_entity_ids)):
            reachable_via_profile += 1
            continue

        el, reas = classify_retrieval_eligibility(row, has_supporting_evidence=has_ev)
        if not el:
            excluded_policy[reas] = excluded_policy.get(reas, 0) + 1
        else:
            unreachable_unexplained.append(aid)

    total_reachable = direct_propositions + reachable_via_passage + reachable_via_profile
    return {
        "policy_id": policy.get("policy_id", "unknown"),
        "policy_version": policy.get("policy_version", "1.0.0"),
        "policy_digest": policy.get("canonical_digest", {}).get("value", "unknown"),
        "approval_id": policy.get("approval_id", "unknown"),
        "total_current_assertions": total_current,
        "baseline_directly_vectorized": len(
            baseline_direct_aids & {str(r["assertion_id"]) for r in current_assertions}
        ),
        "projected_direct_propositions": direct_propositions,
        "projected_dense_edges_removed": dense_edges_removed,
        "projected_reachable_via_passage": reachable_via_passage,
        "projected_reachable_via_profile": reachable_via_profile,
        "projected_total_reachable": total_reachable,
        "projected_excluded_policy_count": sum(excluded_policy.values()),
        "projected_excluded_breakdown": excluded_policy,
        "projected_unreachable_unexplained_count": len(unreachable_unexplained),
        "projected_unreachable_unexplained_ids": unreachable_unexplained[:100],
    }
