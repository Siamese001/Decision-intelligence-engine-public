"""Graph and sidecar entity counting and planned increments."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from storage.connection import sqlite3

from ..core import digest
from ..node_type_policy import active_creatable_node_types
from ..storage.governed_context import GovernedStorageContext
from .constants import _COMPONENTS


def _sidecar_counts(sidecar_path: Path) -> dict[str, int]:
    """Return the governed sidecar vector index counts."""
    tables = ("dense_vector", "sparse_weight", "colbert_token", "retrieval_record")
    counts = {t: 0 for t in tables}
    if not sidecar_path.is_file():
        return counts
    connection = GovernedStorageContext.connect(sidecar_path, readonly=True)
    try:
        for key in tables:
            try:
                counts[key] = int(connection.execute("SELECT count(*) FROM " + key).fetchone()[0])
            except sqlite3.OperationalError:
                counts[key] = 0
        return counts
    finally:
        connection.close()


def _table_counts(graph_path: Path) -> dict[str, int]:
    """Return raw row counts for all 10 mandated GraphDB SQLite tables."""
    tables = ("node", "source", "source_version", "extraction_run", "evidence_item", "assertion", "assertion_evidence", "search_document", "search_fts", "audit_event")
    counts = {t: 0 for t in tables}
    if not graph_path.is_file():
        return counts
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        for t in tables:
            try:
                counts[t] = int(connection.execute("SELECT count(*) FROM " + t).fetchone()[0])
            except sqlite3.OperationalError:
                counts[t] = 0
        return counts
    finally:
        connection.close()


def _graph_counts(graph_path: Path) -> dict[str, int]:
    """Return the governed GraphDB counts that an ingestion writer can change."""
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        queries = {
            "source": "SELECT count(*) FROM source",
            "source_version": "SELECT count(*) FROM source_version",
            "evidence": "SELECT count(*) FROM evidence_item",
            "external_concept": "SELECT count(*) FROM node WHERE node_type='external_concept'",
            "internal_entity": "SELECT count(*) FROM node n JOIN node_state ns ON ns.node_id=n.node_id WHERE n.node_type!='external_concept' AND ns.approval_state='candidate'",
            "candidate_assertion": "SELECT count(*) FROM assertion WHERE canonical_status='candidate'",
            "canonical_assertion": "SELECT count(*) FROM assertion WHERE canonical_status='canonical'",
            "superseded_assertion": "SELECT count(*) FROM assertion WHERE currentness_state='historical' AND superseded_at IS NOT NULL",
            "candidate_search_document": "SELECT count(*) FROM search_document WHERE canonical_status='candidate'",
            "audit_event": "SELECT count(*) FROM audit_event",
        }
        return {
            component_key: int(connection.execute(queries[component_key]).fetchone()[0])
            for component_key, _label, _table in _COMPONENTS
        }
    finally:
        connection.close()


def _present_ids(connection: sqlite3.Connection, table: str, column: str, values: list[str]) -> set[str]:
    if not values:
        return set()
    sql = "SELECT " + column + " FROM " + table + " WHERE " + column + " IN (" + ",".join("?" for _ in values) + ")"
    return {str(row[0]) for row in connection.execute(sql, values)}


def _compute_assertion_fingerprint_pair(payload: dict[str, Any], source: dict[str, Any]) -> tuple[str, str]:
    cand = payload.get("candidate", {})
    pred = payload.get("predicate", "HAS_CLAIM_TEXT")
    obj_node = None if pred == "HAS_CLAIM_TEXT" else payload.get("object_node_id")
    obj_lit = cand.get("normalized_text") if (pred == "HAS_CLAIM_TEXT" or not obj_node) else None
    base_fp = {"subject": payload["subject_node_id"], "predicate": payload["predicate"], "object_node_id": obj_node, "object_literal": obj_lit, "scope": cand.get("normalized_text")}
    return digest({**base_fp, "source_id": source["source_id"]}), digest({**base_fp, "source_version_id": source["source_version_id"]})


def _lookup_existing_fp_status(
    connection: sqlite3.Connection,
    candidate_fingerprints: list[tuple[str, str]],
    approved_fingerprints: list[tuple[str, str]],
) -> tuple[dict[str, str], set[str]]:
    all_fps = list({x for pair in (candidate_fingerprints + approved_fingerprints) for x in pair})
    existing_fp_status: dict[str, str] = {}
    matched_assertion_ids: set[str] = set()
    if all_fps:
        sql = "SELECT assertion_id, assertion_fingerprint, canonical_status FROM assertion WHERE assertion_fingerprint IN (" + ",".join("?" for _ in all_fps) + ")"
        for aid_val, fp_val, status_val in connection.execute(sql, all_fps):
            existing_fp_status[str(fp_val)] = str(status_val)
            matched_assertion_ids.add(str(aid_val))
    return existing_fp_status, matched_assertion_ids


def _count_omitted_source_supersessions(
    connection: sqlite3.Connection,
    source_id: str,
    source_version_id: str,
    sv_present: set[str],
    has_assertions: bool,
    matched_assertion_ids: set[str],
    superseded_targets: list[str],
) -> int:
    if sv_present or not has_assertions:
        return 0
    if not connection.execute("SELECT 1 FROM source_version WHERE source_id=? AND source_version_id!=?", (source_id, source_version_id)).fetchone():
        return 0
    prior_cur = {str(r[0]) for r in connection.execute("SELECT assertion_id FROM assertion WHERE primary_source_id=? AND currentness_state='current' AND superseded_at IS NULL", (source_id,))}
    return len(prior_cur - matched_assertion_ids - set(superseded_targets))


def _is_compatible_scope(left: str, right: str, default_val: str) -> bool:
    return default_val in (left, right) or left == right


def _is_staged_mutex_conflict(
    st: dict[str, str],
    subj_id: str,
    pred: str,
    obj_node: str,
    inc_org: str,
    inc_track: str,
    is_card_target: bool,
) -> bool:
    if st["predicate"] != pred or st["object"] != obj_node:
        return False
    if st["subject"] == subj_id:
        return True
    return (
        is_card_target
        and _is_compatible_scope(inc_org, st["org"], "unknown_org")
        and _is_compatible_scope(inc_track, st["track"], "general_track")
    )


def _record_staged_mutex_conflicts(
    connection: sqlite3.Connection,
    staged_active: dict[str, dict[str, str]],
    mutex_superseded: set[str],
    inc_id: str,
    subj_id: str,
    pred: str,
    obj_node: str,
) -> None:
    from ..graph_signal_core.constants import MUTEX_CARDINALITY_PREDICATES
    from ..graph_signal_core.mutex_invalidation import (
        _resolve_subject_org_and_track,
        is_functional_mutex_target,
    )
    inc_org, inc_track = _resolve_subject_org_and_track(connection, subj_id, inc_id)
    is_card = pred in MUTEX_CARDINALITY_PREDICATES and is_functional_mutex_target(obj_node)
    for st_id, st in list(staged_active.items()):
        if st_id != inc_id and _is_staged_mutex_conflict(st, subj_id, pred, obj_node, inc_org, inc_track, is_card):
            mutex_superseded.add(st_id)
            staged_active.pop(st_id, None)
    if inc_id:
        staged_active[inc_id] = {"subject": subj_id, "predicate": pred, "object": obj_node, "org": inc_org, "track": inc_track}


def _count_mutex_supersessions(
    connection: sqlite3.Connection,
    operations: list[dict[str, Any]],
    payloads: dict[str, Any],
    matched_assertion_ids: set[str],
    superseded_targets: list[str],
) -> int:
    from ..graph_signal_core import find_conflicting_assertions
    mutex_superseded: set[str] = set()
    excluded = matched_assertion_ids | set(superseded_targets)
    staged_active: dict[str, dict[str, str]] = {}
    for op in operations:
        payload = payloads.get(op.get("operation_id"), {})
        obj_node = payload.get("object_node_id") if payload.get("disposition") == "APPROVED" else None
        if not obj_node or op.get("operation") not in {"add_candidate_assertion", "add_approved_assertion", "add_relationship", "add_action_item"}:
            continue
        inc_id = str(op.get("target_id") or "")
        subj_id = str(payload["subject_node_id"])
        pred = str(payload.get("predicate", "HAS_CLAIM_TEXT"))
        conflicts = find_conflicting_assertions(
            connection, object_node_id=obj_node, predicate=pred,
            incoming_assertion_id=inc_id, subject_node_id=subj_id,
        )
        mutex_superseded.update(str(c["assertion_id"]) for c in conflicts if c.get("assertion_id") and str(c["assertion_id"]) not in excluded)
        _record_staged_mutex_conflicts(connection, staged_active, mutex_superseded, inc_id, subj_id, pred, obj_node)
    return len(mutex_superseded)


def _collect_promoted_endpoints(operations: list[dict[str, Any]], payloads: dict[str, Any]) -> set[str]:
    pts: set[str] = set()
    for op in operations:
        pl = payloads.get(op.get("operation_id"), {})
        if pl.get("disposition") == "APPROVED" and op.get("operation") in {"add_candidate_assertion", "add_approved_assertion", "add_relationship", "add_action_item"}:
            pts.update(str(ep) for ep in (pl.get("subject_node_id"), pl.get("object_node_id")) if ep)
    return pts


def _planned_increments(change_set: dict[str, Any] | None, graph_path: Path) -> dict[str, int]:
    """Calculate exact writer inserts from a source-bound proposed change set."""
    increments = {component: 0 for component, _label, _table in _COMPONENTS}
    if change_set is None:
        return increments
    operations = change_set.get("operations")
    extensions = change_set.get("extensions")
    if not isinstance(operations, list) or not isinstance(extensions, dict):
        raise ValueError("graph change set has no governed operations")
    source = extensions.get("com.partner_advisory.nee.source")
    payloads = extensions.get("com.partner_advisory.nee.operation-payloads")
    if not isinstance(source, dict) or not isinstance(payloads, dict):
        raise ValueError("graph change set has no source or operation payload bindings")
    source_id = source.get("source_id")
    source_version_id = source.get("source_version_id")
    evidence = source.get("evidence")
    if not isinstance(source_id, str) or not isinstance(source_version_id, str):
        raise ValueError("graph change set has incomplete source identity")
    if not isinstance(evidence, list):
        raise ValueError("graph change set has no governed evidence list")
    evidence_ids = [item.get("evidence_id") for item in evidence]
    if any(not isinstance(value, str) or not value for value in evidence_ids):
        raise ValueError("graph change set evidence IDs are invalid")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("graph change set evidence IDs are not unique")
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        sv_present = _present_ids(connection, "source_version", "source_version_id", [source_version_id])
        increments["source"] = int(not (sv_present | _present_ids(connection, "source", "source_id", [source_id])))
        increments["source_version"] = int(not sv_present)
        increments["evidence"] = len(evidence_ids) - len(
            _present_ids(connection, "evidence_item", "evidence_id", evidence_ids)
        )
    finally:
        connection.close()
    dispositions = Counter()
    external_targets: list[str] = []
    internal_targets: list[str] = []
    superseded_targets: list[str] = []
    candidate_fingerprints: list[str] = []
    approved_fingerprints: list[str] = []
    creatable_node_types = set(active_creatable_node_types(graph_path))
    for operation in operations:
        operation_id = operation.get("operation_id")
        payload = payloads.get(operation_id)
        if not isinstance(payload, dict) or digest(payload) != operation.get("after_state_hash"):
            raise ValueError("graph change set operation payload does not match its digest")
        disposition = payload.get("disposition")
        if operation.get("operation") in {
            "add_candidate_assertion",
            "add_approved_assertion",
            "add_relationship",
            "add_action_item",
        }:
            fp_pair = _compute_assertion_fingerprint_pair(payload, source)
            if disposition == "CANDIDATE":
                candidate_fingerprints.append(fp_pair)
            elif disposition == "APPROVED":
                approved_fingerprints.append(fp_pair)
        if operation.get("operation") == "create_entity":
            entity = payload.get("entity")
            entity_kind = payload.get("entity_kind")
            if operation.get("target_type") != "entity" or disposition != "CANDIDATE":
                raise ValueError("graph change set has an invalid entity operation")
            if entity_kind == "external":
                if (
                    not isinstance(entity, dict)
                    or entity.get("external_concept_id") != operation.get("target_id")
                    or entity.get("identity_scope") != "source_scoped_external"
                    or entity.get("no_internal_equivalence") is not True
                ):
                    raise ValueError(
                        "graph change set has an invalid external concept operation"
                    )
                external_targets.append(operation["target_id"])
            elif entity_kind == "internal":
                if (
                    not isinstance(entity, dict)
                    or entity.get("entity_id") != operation.get("target_id")
                    or entity.get("node_type") not in creatable_node_types
                ):
                    raise ValueError(
                        "graph change set has an invalid internal entity operation"
                    )
                internal_targets.append(operation["target_id"])
            else:
                raise ValueError("graph change set entity operation has no governed kind")
            continue
        if operation.get("operation") == "supersede_assertion":
            supersession = payload.get("supersession")
            if (
                operation.get("target_type") != "assertion"
                or disposition not in {"CANDIDATE", "REJECTED_NOOP"}
                or not isinstance(supersession, dict)
                or supersession.get("superseded_assertion_id")
                != operation.get("target_id")
            ):
                raise ValueError("graph change set has an invalid supersession operation")
            superseded_targets.append(operation["target_id"])
            continue
        if operation.get("operation") == "add_evidence":
            target_id = payload.get("assertion_id") or operation.get("target_id")
            evidence_ids = operation.get("evidence_ids", [])
            conn_delta = GovernedStorageContext.connect(graph_path, readonly=True)
            try:
                cur_row = conn_delta.execute(
                    "SELECT canonical_status, confidence_score FROM assertion WHERE assertion_id=?",
                    (target_id,),
                ).fetchone()
                if cur_row and cur_row[0] == "candidate":
                    prior_sources = {
                        r[0] for r in conn_delta.execute(
                            """SELECT e.source_id FROM assertion_evidence ae
                               JOIN evidence_item e ON e.evidence_id = ae.evidence_id
                               WHERE ae.assertion_id=?""",
                            (target_id,),
                        ).fetchall()
                    }
                    is_indep = source_id not in prior_sources
                    prior_count = len(prior_sources) or 1
                    new_scores = [
                        float(item.get("extraction_confidence", 0.8))
                        for item in evidence
                        if item.get("evidence_id") in evidence_ids
                    ]
                    from ..signal_extraction import recalculate_assertion_credibility
                    cred = recalculate_assertion_credibility(
                        prior_score=float(cur_row[1] or 0.60),
                        new_evidence=new_scores,
                        authority_rank=int(source.get("authority_rank", 4)),
                        prior_corroborator_count=prior_count,
                        is_independent=is_indep,
                    )
                    has_canonical_approval = False
                    approval_id = change_set.get("approval_id")
                    if approval_id:
                        try:
                            ga_row = conn_delta.execute(
                                """SELECT scope_json FROM governance_approval
                                   WHERE (approval_id=? OR approval_reference=?)
                                     AND status='approved' AND active=1
                                   ORDER BY approved_at DESC LIMIT 1""",
                                (approval_id, approval_id),
                            ).fetchone()
                            if ga_row and "canonical_assertion_promotion" in json.loads(ga_row[0]).get("allowed_actions", []):
                                has_canonical_approval = True
                        except Exception:
                            has_canonical_approval = False
                    if cred["promotion_eligible"] and has_canonical_approval:
                        increments["candidate_assertion"] -= 1
                        increments["canonical_assertion"] += 1
                        increments["candidate_search_document"] -= 1
            finally:
                conn_delta.close()
            continue
        if disposition not in {"APPROVED", "CANDIDATE"}:
            raise ValueError("graph change set operation has an unsupported disposition")
        dispositions[disposition] += 1
    if len(external_targets) != len(set(external_targets)):
        raise ValueError("external concept target IDs are not unique")
    if len(internal_targets) != len(set(internal_targets)):
        raise ValueError("internal entity target IDs are not unique")
    if len(superseded_targets) != len(set(superseded_targets)):
        raise ValueError("superseded assertion target IDs are not unique")
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        increments["external_concept"] = len(external_targets) - len(_present_ids(connection, "node", "node_id", external_targets))
        cand_internal = set(internal_targets) - _collect_promoted_endpoints(operations, payloads)
        increments["internal_entity"] = len(cand_internal) - len(_present_ids(connection, "node", "node_id", list(cand_internal)))
        already_superseded = _present_ids(connection, "assertion", "assertion_id", superseded_targets)
        if already_superseded:
            rows = connection.execute(
                f"SELECT assertion_id FROM assertion WHERE assertion_id IN ({','.join('?' for _ in already_superseded)}) AND currentness_state='historical' AND superseded_at IS NOT NULL",
                sorted(already_superseded),
            ).fetchall()
            if rows:
                raise ValueError("supersession target is already superseded")
        increments["superseded_assertion"] = len(superseded_targets)
        existing_fp_status, matched_assertion_ids = _lookup_existing_fp_status(connection, candidate_fingerprints, approved_fingerprints)
        present_candidate_fps = {p[0] for p in set(candidate_fingerprints) if "candidate" in (existing_fp_status.get(p[0]), existing_fp_status.get(p[1]))}
        present_approved_fps = {p[0] for p in set(approved_fingerprints) if "canonical" in (existing_fp_status.get(p[0]), existing_fp_status.get(p[1]))}
        upgraded_from_candidate_fps = {p[0] for p in set(approved_fingerprints) if "candidate" in (existing_fp_status.get(p[0]), existing_fp_status.get(p[1]))}
        increments["superseded_assertion"] += _count_omitted_source_supersessions(
            connection, source_id, source_version_id, sv_present,
            bool(candidate_fingerprints or approved_fingerprints),
            matched_assertion_ids, superseded_targets,
        ) + _count_mutex_supersessions(connection, operations, payloads, matched_assertion_ids, superseded_targets)
    finally:
        connection.close()
    new_candidates = max(0, dispositions["CANDIDATE"] - len(present_candidate_fps))
    new_approved = max(0, dispositions["APPROVED"] - len(present_approved_fps))
    increments["candidate_assertion"] += new_candidates - len(upgraded_from_candidate_fps)
    increments["canonical_assertion"] += new_approved
    increments["candidate_search_document"] += new_candidates - len(upgraded_from_candidate_fps)
    increments["audit_event"] = len(operations)
    return increments


def _row(*, component_key: str, graph_component: str, baseline: int, planned_delta: int) -> dict[str, Any]:
    planned_after = baseline + planned_delta
    change = f"{planned_delta} governed {graph_component.casefold()} insert(s) proposed." if planned_delta else "No governed insert is proposed."
    return {
        "component_key": component_key, "graph_component": graph_component,
        "baseline": baseline, "planned_after": planned_after,
        "planned_delta": planned_delta, "delta": f"{planned_delta:+d}", "change": change,
    }
