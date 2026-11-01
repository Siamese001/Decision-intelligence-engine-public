"""
dual_pipeline/canonical_pipeline/speaker_role_coverage.py

Resolution-to-assertion coverage verifier and receipt generator for Wave 3.
Validates that authentic human speaker role resolutions survive into the active
canonical graph, verifies row deltas, before/after fact values, prevents duplicate
active assertions, and ensures written-but-unactivated data cannot pass completion.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from storage.connection import create_sqlite_conn, sqlite3

from dual_pipeline.core import digest, sha256_file, stable_id

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        cur = conn.cursor()
        if table == "node":
            cur.execute("SELECT COUNT(*) FROM node")
        elif table == "assertion":
            cur.execute("SELECT COUNT(*) FROM assertion")
        elif table == "evidence":
            cur.execute("SELECT COUNT(*) FROM evidence")
        elif table == "canonical_graph_nodes":
            cur.execute("SELECT COUNT(*) FROM canonical_graph_nodes")
        else:
            return 0
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception as err:
        logger.debug("Failed counting rows in table %s: %s", table, err)
        return 0


def _get_node_table_and_cols(conn: sqlite3.Connection) -> Optional[Tuple[str, str, str, str]]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN ('node', 'canonical_graph_nodes')")
    found_tables = {r[0] for r in cur.fetchall()}
    tbl = "node" if "node" in found_tables else ("canonical_graph_nodes" if "canonical_graph_nodes" in found_tables else None)
    if not tbl:
        return None
    q_info = "PRAGMA table_info(node)" if tbl == "node" else "PRAGMA table_info(canonical_graph_nodes)"
    cur.execute(q_info)
    cols = {r[1] for r in cur.fetchall()}
    name_col = "canonical_name" if "canonical_name" in cols else "name"
    prop_col = "properties_json" if "properties_json" in cols else ("properties" if "properties" in cols else "NULL")
    desc_col = "description" if "description" in cols else "NULL"
    return tbl, name_col, prop_col, desc_col


def _parse_json_props(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as err:
        logger.debug("Failed parsing node properties JSON: %s", err)
        return {}


def _inspect_person_node(conn: sqlite3.Connection, person_id: str, name: str) -> Optional[Dict[str, Any]]:
    tbl_info = _get_node_table_and_cols(conn)
    if not tbl_info:
        return None
    tbl, name_col, prop_col, desc_col = tbl_info
    query_str = f"SELECT node_id, {name_col}, {prop_col}, {desc_col} FROM {tbl} WHERE LOWER(node_id) = ? OR LOWER({name_col}) = ?"
    cur = conn.cursor()
    cur.execute(query_str, (person_id.lower(), name.lower()))
    row = cur.fetchone()
    if not row:
        return None
    props = _parse_json_props(row[2])
    desc = row[3] if len(row) > 3 and row[3] else ""
    return {"node_id": row[0], "canonical_name": row[1], "properties": props, "description": desc}


def _inspect_person_role_assertions(
    conn: sqlite3.Connection, person_id: Union[str, List[str]]
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(assertion)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return []
    select_fields = ["assertion_id", "subject_node_id", "predicate"]
    for f in ("object_literal", "literal_value", "object_node_id", "claim_text", "scope_text",
              "currentness_state", "status", "superseded_by_assertion_id", "is_quarantined",
              "created_at", "updated_at", "extraction_run_id", "primary_source_id"):
        if f in cols:
            select_fields.append(f)
    if "scope_text" in cols:
        predicate_filter = "(UPPER(predicate) IN ('HAS_ROLE_IN', 'HAS_ROLE') OR (UPPER(predicate) = 'RELATED_TO' AND UPPER(scope_text) LIKE '%HAS_ROLE%'))"
    else:
        predicate_filter = "(UPPER(predicate) IN ('HAS_ROLE_IN', 'HAS_ROLE'))"
    if isinstance(person_id, (list, tuple, set)):
        placeholders = ", ".join("?" for _ in person_id)
        q = f"SELECT {', '.join(select_fields)} FROM assertion WHERE LOWER(subject_node_id) IN ({placeholders}) AND {predicate_filter}"
        cur.execute(q, [p.lower() for p in person_id])
    else:
        q = f"SELECT {', '.join(select_fields)} FROM assertion WHERE LOWER(subject_node_id) = ? AND {predicate_filter}"
        cur.execute(q, (person_id.lower(),))
    res = []
    for row in cur.fetchall():
        d = dict(zip(select_fields, row))
        state = str(d.get("currentness_state") or d.get("status") or "").lower()
        d["currentness_state"] = state
        d["superseded_by_assertion_id"] = d.get("superseded_by_assertion_id")
        if "literal_value" in d and "object_literal" not in d:
            d["object_literal"] = d["literal_value"]
        obj_node = str(d.get("object_node_id") or "").lower()
        if (obj_node.startswith(("org_", "org:", "org-")) or obj_node.endswith("_org")) and not d.get("object_literal"):
            continue
        res.append(d)
    return res


def _extract_assertion_role_label(a: Dict[str, Any]) -> str:
    lit = str(a.get("object_literal") or "").strip()
    if lit:
        return lit.lower()
    scope = str(a.get("scope_text") or "").strip()
    if "HAS_ROLE" in scope.upper():
        parts = re.split(r"HAS_ROLE\s+", scope, flags=re.IGNORECASE)
        if len(parts) > 1:
            clean = parts[1].strip().rstrip(".")
            if clean:
                return clean.lower()
    obj_id = str(a.get("object_node_id") or "").strip()
    if obj_id.lower().startswith(("role:", "role_", "role-")):
        clean = re.sub(r"^role[:_-]", "", obj_id, flags=re.IGNORECASE).replace("_", " ").replace("-", " ")
        return clean.lower()
    return ""


def _check_duplicate_active_assertions(
    assertions: List[Dict[str, Any]], name: str, target_role: Optional[str] = None
) -> Optional[str]:
    active_ass = [
        a for a in assertions
        if a["currentness_state"] in ("current", "active") and not a.get("superseded_by_assertion_id")
    ]
    if len(active_ass) <= 1:
        return None
    role_ass: List[Tuple[Dict[str, Any], str]] = []
    for a in active_ass:
        role_label = _extract_assertion_role_label(a)
        if role_label:
            role_ass.append((a, role_label))
    if len(role_ass) <= 1:
        return None
    distinct_roles = {r for _, r in role_ass}
    if len(distinct_roles) <= 1:
        return None

    if target_role:
        target_norm = target_role.strip().lower()
        matching_ass = [a for a, r in role_ass if r == target_norm]
        other_ass = [a for a, r in role_ass if r != target_norm]
        if matching_ass and other_ass:
            latest_match_time = max(str(a.get("created_at") or "") for a in matching_ass)
            latest_other_time = max(str(a.get("created_at") or "") for a in other_ass)
            match_runs = {str(a.get("extraction_run_id") or "") for a in matching_ass} - {""}
            other_runs = {str(a.get("extraction_run_id") or "") for a in other_ass} - {""}
            if (latest_match_time and latest_other_time and latest_match_time > latest_other_time) or (
                match_runs and other_runs and match_runs != other_runs and not (match_runs & other_runs)
            ):
                return None

    return f"DUPLICATE_ACTIVE_ROLE_ASSERTION: Multiple active role assertions ({len(active_ass)}) detected for identity '{name}'."


def _find_active_role_assertion(
    assertions: List[Dict[str, Any]], role: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    active_ass = [
        a for a in assertions
        if a["currentness_state"] in ("current", "active") and not a.get("superseded_by_assertion_id")
    ]
    if not active_ass:
        return None
    if role:
        for a in active_ass:
            if _check_role_in_assertion(a, role):
                return a
    active_ass.sort(key=lambda a: str(a.get("created_at") or ""), reverse=True)
    return active_ass[0]


def _check_role_in_node(node: Optional[Dict[str, Any]], role: str) -> bool:
    if not node or not role:
        return False
    props = node.get("properties", {})
    desc = str(node.get("description") or "")
    return bool(props.get("role") == role or (role and role.lower() in desc.lower()))


def _check_role_in_assertion(active_ass: Optional[Dict[str, Any]], role: str) -> bool:
    if not active_ass or not role:
        return False
    lit = str(active_ass.get("object_literal") or "")
    claim = str(active_ass.get("claim_text") or active_ass.get("scope_text") or "")
    obj_id = str(active_ass.get("object_node_id") or "")
    role_slug = _slugify(role)
    return bool(lit.lower() == role.lower() or role.lower() in claim.lower() or (role_slug and obj_id.lower().endswith(role_slug)))


def _determine_role_disposition(
    base_val: Optional[str], role: str, name: str
) -> Tuple[str, str]:
    if base_val is not None and str(base_val).strip().lower() == role.strip().lower():
        return "EXISTING_FACT_NOOP", f"Existing confirmed role '{role}' matches canonical identity; no-op preserved."
    if base_val is not None:
        return "SUPERSEDED_CORRECTION", f"Role corrected from '{base_val}' to '{role}'; prior fact superseded."
    return "COMMITTED", f"Role '{role}' successfully committed for novel identity '{name}'."


def _extract_res_attributes(res: Any) -> Tuple[str, Optional[str], str]:
    if isinstance(res, str):
        return res.strip(), None, ""
    role = getattr(res, "resolved_role", None)
    if not role and isinstance(res, dict):
        role = res.get("resolved_role") or res.get("role", "")
    org = getattr(res, "organization", None)
    if org is None and isinstance(res, dict):
        org = res.get("organization") or res.get("org")
    res_id = getattr(res, "resolution_id", None)
    if not res_id and isinstance(res, dict):
        res_id = res.get("resolution_id", "")
    return str(role or "").strip(), (str(org) if org else None), str(res_id or "")


def _find_base_value(base_snapshot: Optional[Dict[str, Any]], name: str, person_id: str) -> Optional[str]:
    if not base_snapshot:
        return None
    base_nodes = base_snapshot.get("nodes", {})
    name_node = base_nodes.get(name.lower())
    if name_node and name_node.get("role"):
        return str(name_node["role"])
    person_node = base_nodes.get(person_id.lower())
    if person_node and person_node.get("role"):
        return str(person_node["role"])
    return None


def _resolve_matched_row_id(
    active_ass: Optional[Dict[str, Any]], node: Optional[Dict[str, Any]]
) -> Optional[str]:
    if active_ass and active_ass.get("assertion_id"):
        return str(active_ass["assertion_id"])
    if node and node.get("node_id"):
        return str(node["node_id"])
    return None


def _evaluate_single_role_fact(
    conn: sqlite3.Connection, name: str, res: Any, base_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    role, org, res_id = _extract_res_attributes(res)
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import is_placeholder_title
    p_slug = _slugify(name) or "unknown"
    person_id = f"person:{p_slug}"
    if not role or is_placeholder_title(role):
        return {
            "person_name": name, "canonical_identity": person_id, "resolved_role": role,
            "organization": org, "before_value": None, "after_value": None,
            "disposition": "REJECTED",
            "disposition_reason": f"Empty or placeholder role '{role}' rejected for '{name}'.",
            "verified_in_graph": False, "graph_row_id": None, "authority": res_id or "OPERATOR_CONFIRMED",
        }
    node = _inspect_person_node(conn, person_id, name)
    candidate_ids = [person_id, f"person_{p_slug}"]
    if node and node.get("node_id"):
        candidate_ids.append(str(node["node_id"]))
    assertions = _inspect_person_role_assertions(conn, candidate_ids)

    dup_error = _check_duplicate_active_assertions(assertions, name, target_role=role)
    if dup_error:
        return {
            "person_name": name, "canonical_identity": person_id, "resolved_role": role,
            "organization": org, "before_value": None, "after_value": None,
            "disposition": "REJECTED", "disposition_reason": dup_error, "verified_in_graph": False,
            "graph_row_id": None, "authority": res_id or "OPERATOR_CONFIRMED",
        }

    base_val = _find_base_value(base_snapshot, name, person_id)
    active_ass = _find_active_role_assertion(assertions, role=role)
    found_in_node = _check_role_in_node(node, role)
    found_in_assertion = _check_role_in_assertion(active_ass, role)

    if not found_in_node and not found_in_assertion:
        return {
            "person_name": name, "canonical_identity": person_id, "resolved_role": role,
            "organization": org, "before_value": base_val, "after_value": None,
            "disposition": "MISSING",
            "disposition_reason": f"Required role fact '{role}' for '{name}' not found in active graph.",
            "verified_in_graph": False, "graph_row_id": None, "authority": res_id or "OPERATOR_CONFIRMED",
        }

    matched_row_id = _resolve_matched_row_id(active_ass, node)
    disp, reason = _determine_role_disposition(base_val, role, name)
    return {
        "person_name": name, "canonical_identity": person_id, "resolved_role": role,
        "organization": org, "before_value": base_val, "after_value": role,
        "disposition": disp, "disposition_reason": reason, "verified_in_graph": True,
        "graph_row_id": matched_row_id, "authority": res_id or "OPERATOR_CONFIRMED",
    }


def validate_speaker_role_coverage_receipt(receipt: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validates structural invariants of a speaker role coverage receipt."""
    errors = []
    required_keys = {
        "schema_version", "receipt_id", "run_id", "proposal_digest",
        "approval_digest", "activated_generation_id", "status",
        "covered_roles", "coverage_summary", "row_deltas", "verified_at",
    }
    if not isinstance(receipt, dict):
        return False, ["Receipt must be a JSON object."]
    missing = required_keys - set(receipt.keys())
    if missing:
        return False, [f"Missing required receipt fields: {sorted(missing)}"]
    if receipt.get("schema_version") != "1.0.0":
        errors.append(f"Invalid schema_version: {receipt.get('schema_version')}")
    if receipt.get("status") not in ("VERIFIED", "REJECTED", "FAILED"):
        errors.append(f"Invalid receipt status: {receipt.get('status')}")
    if not isinstance(receipt.get("covered_roles"), list):
        errors.append("covered_roles must be a list")
    if not isinstance(receipt.get("coverage_summary"), dict):
        errors.append("coverage_summary must be a dict")
    return len(errors) == 0, errors


def _check_activation_preconditions(
    activated_gen: str, active_gen_check: Optional[str],
) -> List[str]:
    v = []
    if not activated_gen or not str(activated_gen).strip():
        v.append("UNACTIVATED_DATA_CANNOT_PASS_COMPLETION: Activated generation ID is required.")
    if active_gen_check and activated_gen != active_gen_check:
        v.append(f"ACTIVATION_MISMATCH: Written-but-unactivated data detected (expected active generation '{active_gen_check}', got '{activated_gen}').")
    return v


def _make_rejected_receipt(
    run_id: str, proposal_digest: str, approval_digest: str,
    activated_gen: str, graph_name: str, count: int,
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0", "receipt_id": f"src-rejected-{uuid.uuid4().hex[:12]}",
        "run_id": run_id, "proposal_digest": proposal_digest, "approval_digest": approval_digest,
        "activated_generation_id": activated_gen or "unactivated", "target_graph": graph_name,
        "status": "REJECTED", "covered_roles": [],
        "coverage_summary": {"total_roles": count, "committed_count": 0, "superseded_count": 0, "existing_noop_count": 0, "missing_count": 0, "rejected_count": count},
        "row_deltas": {}, "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _evaluate_resolutions_batch(
    conn: sqlite3.Connection, resolutions: Dict[str, Any], base_snapshot: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[str]]:
    covered_roles: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {
        "COMMITTED": 0, "EXISTING_FACT_NOOP": 0, "SUPERSEDED_CORRECTION": 0,
        "MISSING": 0, "REJECTED": 0,
    }
    violations: List[str] = []
    for name, res in resolutions.items():
        fact_res = _evaluate_single_role_fact(conn, name, res, base_snapshot)
        disp = fact_res["disposition"]
        counts[disp] = counts.get(disp, 0) + 1
        if disp in ("MISSING", "REJECTED"):
            violations.append(fact_res["disposition_reason"])
        covered_roles.append(fact_res)
    return covered_roles, counts, violations


def _persist_coverage_receipt(receipt: Dict[str, Any], output_dir: Optional[Path]) -> None:
    if not output_dir:
        return
    out_path = Path(output_dir) / "speaker_role_coverage_receipt.json"
    try:
        out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as err:
        logger.warning("Could not persist speaker_role_coverage_receipt.json: %s", err)


def verify_speaker_role_coverage_in_graph(
    graph_db: Union[Path, str],
    resolutions: Dict[str, Any],
    activated_generation_id: str,
    proposal_digest: str,
    approval_digest: str,
    run_id: str,
    output_dir: Optional[Path] = None,
    base_snapshot: Optional[Dict[str, Any]] = None,
    graph_sha256_before: Optional[str] = None,
    active_generation_check: Optional[str] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Performs independent read-only inspection of speaker role coverage in the canonical graph."""
    g_path = Path(graph_db).resolve()
    if not g_path.is_file():
        return False, [f"Graph database file not found: {g_path}"], {}
    violations = _check_activation_preconditions(activated_generation_id, active_generation_check)
    if violations:
        rec = _make_rejected_receipt(run_id, proposal_digest, approval_digest, activated_generation_id, g_path.name, len(resolutions))
        return False, violations, rec

    conn = create_sqlite_conn(g_path, readonly=True)
    try:
        cur_nodes = _row_count(conn, "node")
        cur_assertions = _row_count(conn, "assertion")
        cur_evidence = _row_count(conn, "evidence_item")
        covered_roles, counts, eval_violations = _evaluate_resolutions_batch(conn, resolutions, base_snapshot)
        violations.extend(eval_violations)
    finally:
        conn.close()

    base_counts = (base_snapshot or {}).get("row_counts", {})
    row_deltas = {
        "node_delta": cur_nodes - base_counts.get("node", cur_nodes),
        "assertion_delta": cur_assertions - base_counts.get("assertion", cur_assertions),
        "evidence_delta": cur_evidence - base_counts.get("evidence", cur_evidence),
    }
    graph_sha256_after = sha256_file(g_path)
    status = "VERIFIED" if not violations else ("REJECTED" if counts["REJECTED"] > 0 else "FAILED")
    receipt_id = stable_id("spk-role-cov", run_id, activated_generation_id, length=24)
    receipt: Dict[str, Any] = {
        "schema_version": "1.0.0", "receipt_id": receipt_id, "run_id": run_id,
        "proposal_digest": proposal_digest, "approval_digest": approval_digest,
        "activated_generation_id": activated_generation_id, "runtime_root": str(g_path.parent),
        "graph_path": str(g_path), "graph_sha256_before": graph_sha256_before or graph_sha256_after,
        "graph_sha256_after": graph_sha256_after, "status": status, "covered_roles": covered_roles,
        "coverage_summary": {
            "total_roles": len(resolutions), "committed_count": counts["COMMITTED"],
            "existing_noop_count": counts["EXISTING_FACT_NOOP"],
            "superseded_count": counts["SUPERSEDED_CORRECTION"],
            "missing_count": counts["MISSING"], "rejected_count": counts["REJECTED"],
        },
        "row_deltas": row_deltas, "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist_coverage_receipt(receipt, output_dir)
    return len(violations) == 0, violations, receipt


def record_speaker_role_coverage(
    pub_res: Any, written: Dict[str, str], auth_info: Dict[str, Any],
    meta: Dict[str, Any], out_p: Path, activated_generation_id: str,
) -> Optional[Dict[str, Any]]:
    """Records speaker role coverage receipt if role resolutions are present in run metadata."""
    resolutions = meta.get("hitl_role_resolutions")
    if not resolutions:
        return None
    from .publication_verifier import _resolve_graph_path
    graph_path = _resolve_graph_path(meta, output_dir=out_p)
    if not graph_path:
        raise RuntimeError("SPEAKER_ROLE_COVERAGE_FAILED: Cannot resolve active graph path for verification.")
    proposal_digest = auth_info.get("proposal_digest") or digest(meta)
    approval_digest = auth_info.get("approval_digest") or digest({"auth": auth_info})
    run_id = meta.get("run_id") or "run_unknown"
    ok, violations, receipt = verify_speaker_role_coverage_in_graph(
        graph_db=graph_path, resolutions=resolutions, activated_generation_id=activated_generation_id,
        proposal_digest=proposal_digest, approval_digest=approval_digest, run_id=run_id,
        output_dir=out_p, graph_sha256_before=meta.get("graph_sha256_before"),
    )
    if not ok:
        raise RuntimeError(f"SPEAKER_ROLE_COVERAGE_FAILED: {'; '.join(violations)}")
    return receipt


def verify_completion_speaker_role_coverage(
    directive_value: dict[str, Any], completion: dict[str, Any]
) -> None:
    """Enforces truthful speaker role coverage proof during directive completion verification."""
    role_receipt = completion.get("speaker_role_coverage_receipt")
    if role_receipt is not None:
        ok, errors = validate_speaker_role_coverage_receipt(role_receipt)
        if not ok:
            raise ValueError(f"ingestion directive speaker role coverage receipt is malformed: {errors}")
        if role_receipt.get("status") != "VERIFIED":
            raise ValueError("speaker role coverage receipt status must be VERIFIED for successful completion")
        if role_receipt.get("activated_generation_id") != completion.get("generation_after"):
            raise ValueError("speaker role coverage receipt generation does not match activated completion generation")
