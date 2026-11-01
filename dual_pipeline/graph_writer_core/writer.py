"""GovernedGraphWriter core implementation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.database import sqlite3

from ..contracts import validate_artifact
from ..core import (
    ZERO_HASH,
    connect_sqlite,
    digest,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
)
from ..l0.speaker_and_qualifier_policy import sync_canonical_qualifiers_json
from ..label_quality_core.audiences import canonicalize_audiences
from ..label_quality_core.audit import approved_validation_state, candidate_validation_state
from ..signal_extraction import recalculate_assertion_credibility
from ..storage.governed_context import GLOBAL_WRITE_SERIALIZER
from .assertions import AssertionOpsMixin
from .entities import EntityOpsMixin
from .helpers import (
    _apply_existing_assertion_state,
    _chunked_fetch_rows,
    _chunked_fetch_set,
    _ensure_source_and_track_reingest,
    _evict_fts_and_resolve_validation_queue,
    _find_existing_assertion_for_replacement,
    _insert_audit_event,
    _prepare_fingerprint_replacement,
    _restore_fingerprint_replacement,
    _retire_omitted_source_assertions,
    _row_digest,
    _sync_search_fts_row,
    _table_exists,
)
from .proof_gate import ProofGatedApplyMixin
from .sources import SourceOpsMixin


class GovernedGraphWriter(ProofGatedApplyMixin, SourceOpsMixin, EntityOpsMixin, AssertionOpsMixin):
    """Applies an exact approved change set in one SQLite transaction."""

    def __init__(self, graph_path: Path):
        self.graph_path = graph_path.resolve()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return _table_exists(connection, table)

    @staticmethod
    def _verify_changeset_signature(change_set: dict[str, Any]) -> None:
        sig_info = change_set.get("signature") or change_set.get("extensions", {}).get("com.partner_advisory.nee.signature")
        if sig_info is None:
            return
        from dual_pipeline.exceptions import GraphMutationSecurityError
        if not isinstance(sig_info, dict):
            raise GraphMutationSecurityError("Malformed signature block in change set: must be a JSON object.")
        signature, signer = sig_info.get("signature"), sig_info.get("signer_key_id") or sig_info.get("signer")
        if not signature or not signer:
            raise GraphMutationSecurityError("Signature block missing 'signature' or 'signer_key_id'.")
        payload_to_verify = {
            "approval_id": change_set.get("approval_id"),
            "base_graph_hash": change_set.get("base_graph_hash"),
            "operations": change_set.get("operations"),
        }
        from dual_pipeline.core import canonical_bytes, sha256_bytes
        expected_digest = sha256_bytes(canonical_bytes(payload_to_verify))
        claimed_digest = sig_info.get("digest")
        if claimed_digest and claimed_digest != expected_digest:
            raise GraphMutationSecurityError(f"Signature digest mismatch: claimed {claimed_digest}, computed {expected_digest}.")

    def _apply_committed(
        self,
        change_set: dict[str, Any],
        *,
        fail_after_operations: int | None = None,
    ) -> dict[str, Any]:
        validate_artifact(change_set)
        if change_set["status"] not in {"approved", "staged"} or not change_set["approval_id"]:
            raise ValueError("graph writer requires an approval-bound change set")
        before_hash = sha256_file(self.graph_path)
        if before_hash != change_set["base_graph_hash"]:
            raise RuntimeError("change-set before-state graph hash does not match staging graph")
        source = change_set["extensions"].get("com.partner_advisory.nee.source")
        payloads = change_set["extensions"].get("com.partner_advisory.nee.operation-payloads")
        operation_contexts = change_set["extensions"].get(
            "com.partner_advisory.nee.operation-contexts", {}
        )
        if not isinstance(source, dict) or not isinstance(payloads, dict):
            raise ValueError("change set is missing governed source or operation payload bindings")
        if not isinstance(operation_contexts, dict):
            raise ValueError("change-set operation contexts must be an object")
        operation_ids = {operation["operation_id"] for operation in change_set["operations"]}
        if operation_contexts and set(operation_contexts) != operation_ids:
            raise ValueError("batch change-set operation contexts do not exactly cover operations")
        self._verify_changeset_signature(change_set)

        write_lock = GLOBAL_WRITE_SERIALIZER.get_lock(str(self.graph_path))
        write_lock.acquire()
        connection = None
        try:
            connection = connect_sqlite(self.graph_path)
            now = utc_now()
            receipts: list[dict[str, Any]] = []
            connection.execute("BEGIN IMMEDIATE")
            ensured_source_versions: set[str] = set()
            extraction_run_ids: dict[tuple[str, str], str] = {}
            reingested_sources: set[str] = set()
            touched_by_source: dict[str, set[str]] = {}
            candidate_val_state = candidate_validation_state(connection)
            approved_val_state = approved_validation_state(connection)
            canonical_approval_ref = self._resolve_canonical_approval_ref(
                connection, change_set, payloads, source, now
            )
            create_ops = [
                op for op in change_set["operations"]
                if op["operation"] in {
                    "add_candidate_assertion",
                    "add_approved_assertion",
                    "add_relationship",
                    "add_action_item",
                }
            ]
            target_ids = [op["target_id"] for op in create_ops]
            existing_targets = (
                _chunked_fetch_set(
                    connection,
                    'SELECT assertion_id FROM assertion WHERE assertion_id IN ({})',
                    target_ids,
                )
                if target_ids
                else set()
            )
            needed_nodes: set[str] = set()
            needed_evidence: set[str] = set()
            for op in create_ops:
                p = payloads.get(op["operation_id"], {})
                if "subject_node_id" in p:
                    needed_nodes.add(p["subject_node_id"])
                if p.get("object_node_id"):
                    needed_nodes.add(p["object_node_id"])
                for eid in op.get("evidence_ids", []):
                    needed_evidence.add(eid)
            active_nodes = (
                _chunked_fetch_set(
                    connection,
                    'SELECT node_id FROM node WHERE active=1 AND node_id IN ({})',
                    list(needed_nodes),
                )
                if needed_nodes
                else set()
            )
            existing_evidence = (
                _chunked_fetch_set(
                    connection,
                    'SELECT evidence_id FROM evidence_item WHERE evidence_id IN ({})',
                    list(needed_evidence),
                )
                if needed_evidence
                else set()
            )
            for operation in change_set["operations"]:
                operation_context = operation_contexts.get(operation["operation_id"])
                if operation_context is not None:
                    if not isinstance(operation_context, dict) or set(operation_context) != {
                        "change_set_id",
                        "run_id",
                        "source",
                        "trace_id",
                    }:
                        raise ValueError("batch operation context is malformed")
                    operation_source = operation_context["source"]
                    operation_change_set_id = operation_context["change_set_id"]
                    operation_run_id = operation_context["run_id"]
                    if not isinstance(operation_source, dict):
                        raise ValueError("batch operation context has no governed source")
                else:
                    operation_source = source
                    operation_change_set_id = change_set["change_set_id"]
                    operation_run_id = change_set["run_id"]
                source_version_id = operation_source.get("source_version_id")
                if not isinstance(source_version_id, str) or not source_version_id:
                    raise ValueError("governed source has no source-version identity")
                if source_version_id not in ensured_source_versions:
                    _ensure_source_and_track_reingest(
                        self, connection, operation_source, source_version_id,
                        now, reingested_sources, ensured_source_versions, existing_evidence,
                    )
                else:
                    operation_source["source_id"] = self.resolve_canonical_source_id(
                        connection, operation_source
                    )
                extraction_key = (operation_run_id, source_version_id)
                if extraction_key not in extraction_run_ids:
                    extraction_run_ids[extraction_key] = self._ensure_extraction_run(
                        connection,
                        run_id=operation_run_id,
                        source=operation_source,
                        baseline_graph_sha256=change_set["base_graph_hash"],
                        now=now,
                    )
                extraction_run_id = extraction_run_ids[extraction_key]
                payload = payloads.get(operation["operation_id"])
                if not isinstance(payload, dict) or digest(payload) != operation["after_state_hash"]:
                    raise RuntimeError(f"operation payload hash mismatch: {operation['operation_id']}")
                candidate = payload.get("candidate")
                if not isinstance(candidate, dict):
                    raise RuntimeError("operation payload has no governed candidate")
                if (
                    operation["candidate_id"] != candidate["candidate_id"]
                    or operation["classification"] != candidate["classification"]
                    or operation["audiences"] != candidate["audiences"]
                    or operation["evidence_ids"] != candidate["evidence_ids"]
                ):
                    raise RuntimeError("operation/candidate security or evidence binding mismatch")
                if operation["operation"] == "create_entity":
                    if operation["target_type"] != "entity":
                        raise RuntimeError("entity creation must target an entity")
                    if operation["before_state_hash"] != ZERO_HASH:
                        raise RuntimeError(
                            "this writer version only permits exact create operations"
                        )
                    effective_op = (
                        {**operation, "idempotent_upsert": True}
                        if operation_context is not None
                        else operation
                    )
                    if payload.get("entity_kind") == "external":
                        self._create_external_concept(
                            connection, effective_op, payload, operation_source, now
                        )
                        reason = (
                            "Approval-bound NEE source-scoped external concept creation."
                        )
                    elif payload.get("entity_kind") == "internal":
                        self._create_internal_entity(connection, effective_op, payload, now)
                        reason = "Approval-bound NEE internal candidate entity creation."
                    else:
                        raise RuntimeError("entity creation has no governed entity kind")
                    active_nodes.add(operation["target_id"])
                    existing_targets.add(operation["target_id"])
                    _insert_audit_event(
                        connection, operation_change_set_id, operation["operation_id"],
                        operation["operation"], operation["target_type"], operation["target_id"],
                        None, json.dumps(payload, sort_keys=True), operation_source["source_id"],
                        now, reason, change_set["approval_id"], None, operation_run_id,
                    )
                    receipts.append({
                        "operation_id": operation["operation_id"], "change_set_id": operation_change_set_id,
                        "run_id": operation_run_id, "source_id": operation_source["source_id"],
                        "target_id": operation["target_id"], "before_state_hash": ZERO_HASH,
                        "after_row_hash": _row_digest(connection, "node", "node_id", operation["target_id"]),
                    })
                    if fail_after_operations is not None and len(receipts) >= fail_after_operations:
                        raise RuntimeError("injected graph-writer failure")
                    continue
                if operation["operation"] == "supersede_assertion":
                    if operation["target_type"] != "assertion":
                        raise RuntimeError("supersession must target an assertion")
                    supersession = payload.get("supersession")
                    if not isinstance(supersession, dict) or set(supersession) != {"replacement_assertion_id", "superseded_assertion_id"}:
                        raise RuntimeError("supersession payload has an invalid shape")
                    if supersession["superseded_assertion_id"] != operation["target_id"]:
                        raise RuntimeError("supersession target binding mismatch")
                    current_row_hash = _row_digest(connection, "assertion", "assertion_id", operation["target_id"])
                    if current_row_hash != operation["before_state_hash"]:
                        raise RuntimeError("supersession before-state hash mismatch")
                    current = connection.execute(
                        "SELECT currentness_state,superseded_at,superseded_by_assertion_id FROM assertion WHERE assertion_id=?",
                        (operation["target_id"],),
                    ).fetchone()
                    if current is None or current["currentness_state"] == "historical" or current["superseded_at"] is not None or current["superseded_by_assertion_id"] is not None:
                        raise RuntimeError("supersession target is absent or already superseded")
                    replacement_id = supersession["replacement_assertion_id"]
                    if replacement_id is not None and not connection.execute("SELECT 1 FROM assertion WHERE assertion_id=?", (replacement_id,)).fetchone():
                        raise RuntimeError("supersession replacement assertion is absent")
                    previous_value = dict(connection.execute("SELECT * FROM assertion WHERE assertion_id=?", (operation["target_id"],)).fetchone())
                    connection.execute(
                        """UPDATE assertion
                           SET currentness_state='historical',superseded_at=?,
                               superseded_by_assertion_id=?,retrieval_eligible=0,
                               updated_at=?
                           WHERE assertion_id=?""",
                        (now, replacement_id, now, operation["target_id"]),
                    )
                    if self._table_exists(connection, "assertion_label_state"):
                        connection.execute(
                            """UPDATE assertion_label_state
                               SET lifecycle_state='superseded',updated_at=?
                               WHERE assertion_id=?""",
                            (now, operation["target_id"]),
                        )
                    connection.execute(
                        """UPDATE search_document
                           SET currentness_state='historical',retrieval_eligible=0,
                               updated_at=?
                           WHERE assertion_id=?""",
                        (now, operation["target_id"]),
                    )
                    _evict_fts_and_resolve_validation_queue(
                        connection, operation["target_id"], now
                    )
                    _insert_audit_event(
                        connection, operation_change_set_id, operation["operation_id"],
                        operation["operation"], operation["target_type"], operation["target_id"],
                        json.dumps(previous_value, sort_keys=True), json.dumps(payload, sort_keys=True),
                        operation_source["source_id"], now,
                        "Approval-bound semantic correction of a candidate assertion.",
                        change_set["approval_id"], None, operation_run_id,
                    )
                    receipts.append({
                        "operation_id": operation["operation_id"], "change_set_id": operation_change_set_id,
                        "run_id": operation_run_id, "source_id": operation_source["source_id"],
                        "target_id": operation["target_id"], "before_state_hash": operation["before_state_hash"],
                        "after_row_hash": _row_digest(connection, "assertion", "assertion_id", operation["target_id"]),
                    })
                    if fail_after_operations is not None and len(receipts) >= fail_after_operations:
                        raise RuntimeError("injected graph-writer failure")
                    continue
                if operation["operation"] == "add_evidence":
                    if operation["target_type"] not in {"assertion", "evidence"}:
                        raise RuntimeError("add_evidence must target an assertion or evidence")
                    target_assertion_id = payload.get("assertion_id") or operation["target_id"]
                    touched_by_source.setdefault(operation_source["source_id"], set()).add(target_assertion_id)
                    current = connection.execute(
                        "SELECT * FROM assertion WHERE assertion_id=?", (target_assertion_id,)
                    ).fetchone()
                    if current is None:
                        raise RuntimeError(f"target assertion does not exist: {target_assertion_id}")
                    current_dict = dict(current)

                    # Query existing evidence sources BEFORE inserting new evidence links
                    prior_ev_rows = connection.execute(
                        """SELECT e.evidence_id, e.source_id
                           FROM assertion_evidence ae
                           JOIN evidence_item e ON e.evidence_id = ae.evidence_id
                           WHERE ae.assertion_id=?""",
                        (target_assertion_id,),
                    ).fetchall()
                    prior_sources = {row["source_id"] for row in prior_ev_rows if row["source_id"]}
                    if current_dict.get("primary_source_id"):
                        prior_sources.add(current_dict["primary_source_id"])

                    # Link each evidence_id in operation["evidence_ids"]
                    for evidence_id in operation["evidence_ids"]:
                        ev_item = connection.execute(
                            "SELECT source_id FROM evidence_item WHERE evidence_id=?", (evidence_id,)
                        ).fetchone()
                        if not ev_item:
                            raise RuntimeError(f"governed evidence is missing: {evidence_id}")
                        indep_flag = int(bool(prior_sources) and ev_item["source_id"] not in prior_sources)
                        connection.execute(
                            """INSERT OR IGNORE INTO assertion_evidence
                               (assertion_id,evidence_id,evidence_relationship,independent_corroboration,created_at)
                               VALUES (?,?,?,?,?)""",
                            (target_assertion_id, evidence_id, "supports", indep_flag, now),
                        )

                    # Query all evidence items for this assertion to compute updated credibility
                    evidence_rows = connection.execute(
                        """SELECT e.evidence_id, e.extraction_confidence, e.source_authority_rank, e.source_id, ae.independent_corroboration
                           FROM assertion_evidence ae
                           JOIN evidence_item e ON e.evidence_id = ae.evidence_id
                           WHERE ae.assertion_id=?""",
                        (target_assertion_id,),
                    ).fetchall()

                    new_sources = {
                        row["source_id"] for row in evidence_rows if row["evidence_id"] in operation["evidence_ids"]
                    }
                    is_independent = bool(new_sources - prior_sources)
                    prior_corroborator_count = len(prior_sources) or 1
                    new_evidence_scores = [
                        float(row["extraction_confidence"] or 0.8) for row in evidence_rows if row["evidence_id"] in operation["evidence_ids"]
                    ]
                    cred = recalculate_assertion_credibility(
                        prior_score=float(current_dict["confidence_score"] or 0.60), new_evidence=new_evidence_scores,
                        authority_rank=int(operation_source.get("authority_rank", 4)),
                        prior_corroborator_count=prior_corroborator_count, is_independent=is_independent,
                    )
                    updated_score, updated_label = cred["updated_score"], cred["confidence_label"]

                    promoted, approval_reference = False, None
                    if self._can_corroborate_promote(current_dict, operation_source, target_assertion_id, cred):
                        try:
                            approval_reference = self._validate_canonical_approval(connection, change_set.get("approval_id"))
                            promoted = True
                        except RuntimeError:
                            promoted = False
                    if promoted:
                        validation_state = approved_validation_state(connection)
                        synced_quals = sync_canonical_qualifiers_json(current_dict.get("qualifiers_json"))
                        connection.execute(
                            "UPDATE assertion SET confidence_score=?, confidence_label=?, validation_state=?, canonical_status='canonical', canonical_eligible=1, retrieval_eligible=1, approval_reference=?, qualifiers_json=?, hitl_required=0, validation_priority=NULL, validation_owner=NULL, updated_at=? WHERE assertion_id=?",
                            (updated_score, updated_label, validation_state, approval_reference, synced_quals, now, target_assertion_id),
                        )
                        connection.execute(
                            "UPDATE search_document SET canonical_status='canonical', retrieval_eligible=1, updated_at=? WHERE assertion_id=?",
                            (now, target_assertion_id),
                        )
                        if self._table_exists(connection, "assertion_label_state"):
                            connection.execute("UPDATE assertion_label_state SET validation_state=?, updated_at=? WHERE assertion_id=?", (validation_state, now, target_assertion_id))
                        from dual_pipeline.graph_signal_core import supersede_conflicting_assertions
                        from dual_pipeline.uwg_core.node_promotion import promote_candidate_endpoint_nodes
                        supersede_conflicting_assertions(connection, incoming_assertion_id=target_assertion_id, subject_node_id=current_dict["subject_node_id"], predicate=current_dict["predicate"], object_node_id=current_dict.get("object_node_id"), now_iso=now)
                        promote_candidate_endpoint_nodes(connection, current_dict, validation_state, now, self._table_exists)
                    else:
                        connection.execute("UPDATE assertion SET confidence_score=?, confidence_label=?, updated_at=? WHERE assertion_id=?", (updated_score, updated_label, now, target_assertion_id))

                    _insert_audit_event(
                        connection, operation_change_set_id, operation["operation_id"],
                        "corroborate_and_promote" if promoted else "add_evidence",
                        "assertion", target_assertion_id,
                        json.dumps({"confidence_score": current_dict["confidence_score"], "canonical_status": current_dict["canonical_status"]}, sort_keys=True),
                        json.dumps({"confidence_score": updated_score, "confidence_label": updated_label, "canonical_status": "canonical" if promoted else current_dict["canonical_status"], "promoted": promoted}, sort_keys=True),
                        operation_source["source_id"], now,
                        "Approval-bound multi-source evidence corroboration and credibility promotion." if promoted else "Approval-bound evidence corroboration.",
                        None, approval_reference if promoted else change_set.get("approval_id"), operation_run_id,
                    )
                    receipts.append({
                        "operation_id": operation["operation_id"], "change_set_id": operation_change_set_id,
                        "run_id": operation_run_id, "source_id": operation_source["source_id"],
                        "target_id": target_assertion_id, "before_state_hash": operation["before_state_hash"],
                        "after_row_hash": _row_digest(connection, "assertion", "assertion_id", target_assertion_id),
                    })
                    if fail_after_operations is not None and len(receipts) >= fail_after_operations:
                        raise RuntimeError("injected graph-writer failure")
                    continue
                if operation["operation"] not in {
                    "add_candidate_assertion",
                    "add_approved_assertion",
                    "add_relationship",
                    "add_action_item",
                }:
                    raise RuntimeError("unsupported graph operation for this writer")
                approved_disposition = payload.get("disposition") == "APPROVED"
                if (operation["operation"] == "add_approved_assertion") != approved_disposition:
                    if operation["operation"] not in {"add_relationship", "add_action_item"}:
                        raise RuntimeError("operation type and governed disposition do not agree")
                if operation["before_state_hash"] != ZERO_HASH:
                    raise RuntimeError("this writer version only permits exact create operations")
                if operation["target_id"] in existing_targets:
                    raise RuntimeError(f"target already exists: {operation['target_id']}")
                if payload["subject_node_id"] not in active_nodes:
                    raise RuntimeError("subject identity is absent or inactive")
                if payload.get("object_node_id") and payload["object_node_id"] not in active_nodes:
                    raise RuntimeError("object identity is absent or inactive")
                # Generate base assertion to capture complex fingerprint digest
                assertion_vals = self._assertion_values(
                    payload, operation["target_id"], operation_source,
                    extraction_run_id, now, validation_state=candidate_val_state,
                )
                fingerprint = assertion_vals[1]
                existing_assertion = _find_existing_assertion_for_replacement(
                    connection, fingerprint, payload, operation_source
                )
                if existing_assertion:
                    assertion_vals = _apply_existing_assertion_state(
                        self, payload, operation["target_id"], operation_source,
                        extraction_run_id, now, candidate_val_state, existing_assertion, receipts,
                    )
                    prior_ev, superseded_ids = _prepare_fingerprint_replacement(connection, existing_assertion[0])
                else:
                    prior_ev, superseded_ids = [], []
                connection.execute(
                    """INSERT INTO assertion
                       (assertion_id,assertion_fingerprint,subject_node_id,predicate,legacy_predicate,
                        object_node_id,object_literal,object_literal_type,assertion_type,atomicity_state,
                        scope_text,speaker_or_author,primary_source_id,extraction_run_id,extraction_method,
                        origin_type,confidence_label,confidence_score,validation_state,currentness_state,
                        valid_from,valid_to,date_precision,date_status,recorded_at,superseded_at,
                        superseded_by_assertion_id,resolution_state,canonical_status,canonical_eligible,
                        retrieval_eligible,materiality,classification,allowed_audiences_json,hitl_required,
                        validation_priority,validation_owner,qualifiers_json,legacy_record_type,
                        legacy_record_ids_json,created_at,updated_at,approval_reference)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    assertion_vals,
                )
                touched_by_source.setdefault(operation_source["source_id"], set()).add(operation["target_id"])
                for evidence_id in operation["evidence_ids"]:
                    if evidence_id not in existing_evidence:
                        if connection.execute("SELECT 1 FROM evidence_item WHERE evidence_id=?", (evidence_id,)).fetchone():
                            existing_evidence.add(evidence_id)
                        else:
                            raise RuntimeError(f"governed evidence is missing: {evidence_id}")
                    connection.execute(
                        "INSERT OR IGNORE INTO assertion_evidence VALUES (?,?,?,?,?)",
                        (operation["target_id"], evidence_id, "supports", 0, now),
                    )
                if existing_assertion is not None:
                    _restore_fingerprint_replacement(
                        connection, operation["target_id"], prior_ev, superseded_ids,
                        operation["evidence_ids"], existing_evidence, now,
                        old_assertion_id=existing_assertion[0],
                    )
                canonical = approved_disposition
                elig_val = int(assertion_vals[30])
                approval_reference: str | None = None
                if canonical:
                    approval_reference = canonical_approval_ref
                    validation_state = approved_val_state
                    connection.execute(
                        "UPDATE assertion SET validation_state=?,canonical_status='canonical', canonical_eligible=1,retrieval_eligible=?,approval_reference=?, hitl_required=0,validation_priority=NULL,validation_owner=NULL, updated_at=? WHERE assertion_id=?",
                        (validation_state, elig_val, approval_reference, now, operation["target_id"]),
                    )
                    from dual_pipeline.graph_signal_core import supersede_conflicting_assertions
                    from dual_pipeline.uwg_core.node_promotion import promote_candidate_endpoint_nodes
                    supersede_conflicting_assertions(connection, incoming_assertion_id=operation["target_id"], subject_node_id=payload["subject_node_id"], predicate=payload["predicate"], object_node_id=payload.get("object_node_id"), now_iso=now)
                    promote_candidate_endpoint_nodes(connection, payload, validation_state, now, self._table_exists)
                else:
                    validation_state = candidate_val_state
                    if not elig_val:
                        connection.execute(
                            "UPDATE assertion SET canonical_eligible=0, retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
                            (now, operation["target_id"]),
                        )
                if self._table_exists(connection, "assertion_label_state"):
                    connection.execute(
                        "INSERT INTO assertion_label_state (assertion_id,extraction_confidence,assertion_confidence_label, assertion_confidence_score,validation_state,lifecycle_state, mapping_state,mapping_rule_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (operation["target_id"], float(candidate["extraction_confidence"]), None, None, validation_state, "active", "native", None, now),
                    )
                if self._table_exists(connection, "assertion_audience"):
                    for audience in canonicalize_audiences(candidate["audiences"]):
                        connection.execute(
                            "INSERT INTO assertion_audience (assertion_id,audience_id,mapping_state,created_at) VALUES (?,?,?,?)",
                            (operation["target_id"], audience, "native", now),
                        )
                search_id = stable_id("search", operation["target_id"], length=28)
                search_title = payload["candidate"]["normalized_text"][:240]
                search_body = payload["candidate"]["normalized_text"]
                search_currentness = assertion_vals[19]
                connection.execute(
                    "INSERT INTO search_document (search_document_id,object_type,object_id,title,body,canonical_status,currentness_state,retrieval_eligible,source_id,assertion_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (search_id, "assertion", operation["target_id"], search_title, search_body, "canonical" if canonical else "candidate", search_currentness, int(canonical and elig_val), operation_source["source_id"], operation["target_id"], now),
                )
                _sync_search_fts_row(
                    connection, search_id, search_title, search_body, operation["target_id"], search_currentness
                )
                _insert_audit_event(
                    connection, operation_change_set_id, operation["operation_id"],
                    operation["operation"], operation["target_type"], operation["target_id"],
                    None, json.dumps(payload, sort_keys=True), operation_source["source_id"],
                    now, "Approval-bound NEE dual-pipeline graph change.",
                    change_set["approval_id"], approval_reference, operation_run_id,
                )
                receipts.append(
                    {
                        "operation_id": operation["operation_id"],
                        "change_set_id": operation_change_set_id,
                        "run_id": operation_run_id,
                        "source_id": operation_source["source_id"],
                        "target_id": operation["target_id"],
                        "before_state_hash": ZERO_HASH,
                        "after_row_hash": (
                            _row_digest(connection, "assertion", "assertion_id", operation["target_id"])
                            if fail_after_operations is not None
                            else None
                        ),
                    }
                )
                if fail_after_operations is not None and len(receipts) >= fail_after_operations:
                    raise RuntimeError("injected graph-writer failure")
            _retire_omitted_source_assertions(connection, reingested_sources, touched_by_source, now)
            if fail_after_operations is None:
                pending_targets = [r["target_id"] for r in receipts if r.get("after_row_hash") is None]
                if pending_targets:
                    fetched_rows = _chunked_fetch_rows(
                        connection,
                        'SELECT * FROM "assertion" WHERE "assertion_id" IN ({})',
                        pending_targets,
                    )
                    digests = {r["assertion_id"]: digest(dict(r)) for r in fetched_rows}
                    for r in receipts:
                        if r.get("after_row_hash") is None:
                            r["after_row_hash"] = digests.get(r["target_id"], ZERO_HASH)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"foreign-key validation failed: {len(violations)} violations")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.close()
            connection = None
            integrity = sqlite_integrity(self.graph_path)
            if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
                raise RuntimeError("post-commit graph integrity validation failed")
            after_hash = sha256_file(self.graph_path)
            if len(receipts) > 0 and before_hash == after_hash:
                raise RuntimeError("graph writer operations did not produce a changed graph hash")
            return {
                "status": "COMMITTED",
                "change_set_id": change_set["change_set_id"],
                "approval_id": change_set["approval_id"],
                "transaction_required": True,
                "operation_count": len(receipts),
                "operations": receipts,
                "graph_sha256_before": before_hash,
                "graph_sha256_after": after_hash,
                "integrity": integrity,
                "committed_at": utc_now(),
            }
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
            write_lock.release()
