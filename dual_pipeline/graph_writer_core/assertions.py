"""Assertion value preparation and governance approval validation for GovernedGraphWriter."""
from __future__ import annotations

import json
from typing import Any

from storage.database import sqlite3

from ..core import digest, normalize_iso_date
from ..l0.assumption_lifecycle import (
    DEFAULT_VALIDATION_OWNER,
    normalize_epistemic_assertion_type,
    resolve_assumption_checkpoint_ttl,
)
from ..l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    clean_non_placeholder_speaker,
    extract_inline_turn_speaker,
    has_non_promotable_qualifiers,
    is_placeholder_speaker,
    sync_canonical_qualifiers_json,
)
from ..label_quality_core.audiences import canonical_audiences_json
from ..release_core.helpers import _retrieval_governance_for_payload
from ..strategic_reasoning import (
    STRATEGIC_PROFILE_QUALIFIER,
    extract_quantitative_constraints,
    infer_object_literal_type,
    validate_strategic_profile,
)
from .proof_gate import update_assertion_object_literals





def compute_cross_source_scope_signature(
    subject_node_id: str | None,
    predicate: str | None,
    object_node_id: str | None,
    normalized_text: str | None,
) -> str:
    """Compute a source-independent semantic fingerprint for cross-source assertion deduplication."""
    import re as _re
    raw_text = str(normalized_text or "")
    norm_text = " ".join(
        _re.sub(r"\s+in\s+[^.]+?\.\s*$", "", raw_text)
        .replace("\u2019", "'")
        .replace("\u2014", "-")
        .casefold()
        .split()
    ).strip(" .;:,-")
    norm_pred = str(predicate or "HAS_CLAIM_TEXT").strip().upper().replace(" ", "_")
    return digest(
        {
            "subject_node_id": subject_node_id,
            "predicate": norm_pred,
            "object_node_id": object_node_id,
            "normalized_scope_text": norm_text,
        }
    )


def _validate_snapshot_qualifier(candidate_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = payload.get("project_status_snapshot")
    if snapshot is None:
        return None
    if (
        candidate_type != "source_chunk"
        or payload["disposition"] != "CANDIDATE"
        or snapshot.get("source_posture") != "citation_only_weekly_snapshot"
        or snapshot.get("fact_promotion_eligible") is not False
    ):
        raise RuntimeError("project-status snapshot cannot be written as a fact")
    return snapshot


def _attach_topic_signatures(qualifiers: dict[str, Any], candidate: dict[str, Any], payload: dict[str, Any]) -> None:
    claim_topic = (
        candidate.get("normalized_topic")
        or candidate.get("normalized_claim_topic")
        or (payload.get("edge_traits") or {}).get("topic")
    )
    if claim_topic:
        qualifiers["normalized_claim_topic"] = str(claim_topic).strip().lower()
        qualifiers["scope_signature"] = digest(
            {"subject": payload.get("subject_node_id"), "topic": qualifiers["normalized_claim_topic"]}
        )
    if claim_topic or payload.get("include_cross_source_scope_signature"):
        qualifiers["cross_source_scope_signature"] = compute_cross_source_scope_signature(
            payload.get("subject_node_id"),
            payload.get("predicate"),
            payload.get("object_node_id"),
            candidate.get("normalized_text"),
        )
    quant = payload.get("quantitative_constraints") or extract_quantitative_constraints(candidate.get("normalized_text"))
    if quant:
        qualifiers["quantitative_constraints"] = quant
        qualifiers["inferred_literal_type"] = infer_object_literal_type(candidate.get("normalized_text"))


def _sync_approved_default_qualifiers(qualifiers: dict[str, Any], candidate_type: str, payload: dict[str, Any]) -> None:
    if payload.get("disposition") != "APPROVED" or payload.get("retrieval_governance") is not None:
        return
    if candidate_type == "action" or qualifiers.get("retrieval_kind") == "guidance":
        return
    if not has_non_promotable_qualifiers(qualifiers):
        qualifiers["fact_promotion_eligible"] = True
        if qualifiers.get("answer_use") == "candidate_hypothesis":
            qualifiers["answer_use"] = "factual_answer"


def _build_assertion_qualifiers(candidate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    candidate_type = candidate["candidate_type"]
    qualifiers = {
        "dual_pipeline_candidate_id": candidate["candidate_id"],
        "dual_pipeline_candidate_type": candidate_type,
        "dual_pipeline_disposition": payload["disposition"],
        "extraction_confidence": float(candidate["extraction_confidence"]),
        **_retrieval_governance_for_payload(payload),
    }
    if payload.get("edge_traits") is not None:
        qualifiers["edge_traits"] = payload["edge_traits"]
    if payload.get("strategic_profile") is not None:
        qualifiers[STRATEGIC_PROFILE_QUALIFIER] = validate_strategic_profile(payload["strategic_profile"])
    if payload.get("action_status"):
        qualifiers["action_status"] = payload["action_status"]
    snapshot = _validate_snapshot_qualifier(candidate_type, payload)
    if snapshot is not None:
        qualifiers["project_status_snapshot"] = snapshot
    _attach_topic_signatures(qualifiers, candidate, payload)
    _sync_approved_default_qualifiers(qualifiers, candidate_type, payload)
    return qualifiers


def _candidate_speaker_mention(candidate: dict[str, Any]) -> str | None:
    for m in candidate.get("entity_mentions") or []:
        if not isinstance(m, dict):
            continue
        ctx = str(m.get("context") or "")
        if ctx.startswith("Speaker in ") or ctx.startswith("Speaker / "):
            spk = clean_non_placeholder_speaker(m.get("reported_text"))
            if spk:
                return spk
    return None


def _raw_explicit_speaker(
    edge_traits: dict[str, Any], candidate: dict[str, Any], source: dict[str, Any]
) -> str | None:
    for val in (
        edge_traits.get("speaker_or_author"),
        (edge_traits.get("lineage") or {}).get("speaker"),
        candidate.get("speaker_or_author"),
        _candidate_speaker_mention(candidate),
        source.get("author"),
    ):
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _resolve_speaker_or_author(
    edge_traits: dict[str, Any], candidate: dict[str, Any], source: dict[str, Any]
) -> str | None:
    for val in (
        edge_traits.get("speaker_or_author"),
        (edge_traits.get("lineage") or {}).get("speaker"),
        candidate.get("speaker_or_author"),
        _candidate_speaker_mention(candidate),
        source.get("author"),
        extract_inline_turn_speaker(candidate.get("normalized_text")),
    ):
        clean = clean_non_placeholder_speaker(val)
        if clean:
            return clean
    return None




def _resolve_confidence_score(
    conf_info: dict[str, Any], candidate: dict[str, Any], prior_confidence: float | None
) -> float:
    base = float(conf_info.get("score") or candidate.get("extraction_confidence") or 0.85)
    if prior_confidence is not None:
        return min(0.99, prior_confidence + (1.0 - prior_confidence) * 0.5)
    return base


def _resolve_confidence_label(conf_info: dict[str, Any], score: float) -> str:
    raw = conf_info.get("tier")
    if not raw:
        raw = "high" if score >= 0.8 else ("medium" if score >= 0.6 else "low")
    raw_str = str(raw).lower()
    valid = {"high", "medium", "low", "directional", "confirmed", "medium-high", "superseded"}
    return raw_str if raw_str in valid else "directional"


def _check_writer_speaker_gate(
    edge_traits: dict[str, Any], candidate: dict[str, Any], payload: dict[str, Any], source: dict[str, Any], target_id: str, predicate: str
) -> str | None:
    raw_spk = _raw_explicit_speaker(edge_traits, candidate, source)
    resolved_spk = _resolve_speaker_or_author(edge_traits, candidate, source)
    effective_spk = resolved_spk if (raw_spk is None or (is_placeholder_speaker(raw_spk) and resolved_spk is not None)) else raw_spk
    spk_defect = classify_speaker_attribution_defect(
        predicate=predicate,
        scope_text=candidate["normalized_text"],
        speaker_or_author=effective_spk,
        subject_node_id=payload.get("subject_node_id"),
        source_type=source.get("source_type"),
        source_name=source.get("name") or source.get("title") or source.get("source_name"),
        assertion_id=target_id,
    )
    if spk_defect is not None and payload.get("disposition") == "APPROVED":
        raise RuntimeError(f"approved assertion rejected due to speaker/purity defect: {spk_defect}")
    return spk_defect


def _compute_retrieval_eligibility(
    payload: dict[str, Any], qualifiers: dict[str, Any], confidence_score: float, spk_defect: str | None
) -> int:
    if spk_defect is not None or not bool(payload.get("retrieval_eligible", True)):
        return 0
    if payload.get("disposition") == "APPROVED":
        return 1
    if payload.get("disposition") != "CANDIDATE" or has_non_promotable_qualifiers(qualifiers) or confidence_score < 0.65:
        return 0
    return int(bool(qualifiers.get("fact_promotion_eligible")) or confidence_score >= 0.80)


def _object_literal_for_predicate(predicate: str, object_node_id: str | None, normalized_text: str) -> str | None:
    if predicate == "HAS_CLAIM_TEXT":
        return normalized_text
    return None if object_node_id else normalized_text


def _resolve_assertion_date_status(
    edge_traits: dict[str, Any],
    candidate: dict[str, Any],
    source: dict[str, Any],
    valid_from: str | None,
    qualifiers: dict[str, Any],
) -> str:
    """Resolve assertion date_status and stamp snapshot upload qualifiers when undated."""
    if not valid_from:
        return "unknown"
    raw_status = str(
        edge_traits.get("date_status")
        or candidate.get("date_status")
        or source.get("date_status")
        or source.get("source_date_status")
        or ""
    ).strip()
    is_upload_only = bool(source.get("upload_date") and not source.get("actual_date"))
    if (
        raw_status == "estimated_from_source_snapshot"
        or is_upload_only
        or source.get("source_posture") == "citation_only_weekly_snapshot"
    ):
        qualifiers.setdefault("date_basis", "file_upload_snapshot")
        qualifiers.setdefault("upload_date", valid_from)
        return "estimated_from_source_snapshot"
    if raw_status in {"exact", "unknown"}:
        return raw_status
    return "exact"


class AssertionOpsMixin:
    """Provides assertion serialization and canonical approval validation."""

    @staticmethod
    def _assertion_values(
        payload: dict[str, Any],
        target_id: str,
        source: dict[str, Any],
        extraction_run_id: str,
        now: str,
        validation_state: str = "source_statement",
        prior_confidence: float | None = None,
    ) -> tuple[Any, ...]:
        candidate = payload["candidate"]
        predicate = payload.get("predicate", "HAS_CLAIM_TEXT")
        object_node_id = payload.get("object_node_id")
        object_literal = _object_literal_for_predicate(predicate, object_node_id, candidate["normalized_text"])
        materiality = "medium" if candidate["materiality"] == "low" else candidate["materiality"]
        # The intake contract exposes the user-facing ``public`` label while
        # the governed graph's controlled vocabulary stores public external
        # material as ``external_approved``. This is a storage mapping only;
        # the source-bound operation and audit retain the original label.
        classification = "external_approved" if candidate["classification"] == "public" else candidate["classification"]
        edge_traits = payload.get("edge_traits") or {}
        raw_type = edge_traits.get("assertion_type") or candidate.get("assertion_type") or candidate["candidate_type"]
        graph_assertion_type = normalize_epistemic_assertion_type(raw_type)
        speaker_or_author = _resolve_speaker_or_author(edge_traits, candidate, source)
        spk_defect = _check_writer_speaker_gate(edge_traits, candidate, payload, source, target_id, predicate)
        conf_info = edge_traits.get("confidence") or {}
        confidence_score = _resolve_confidence_score(conf_info, candidate, prior_confidence)
        confidence_label = _resolve_confidence_label(conf_info, confidence_score)
        valid_to = resolve_assumption_checkpoint_ttl(candidate, payload, source)
        qualifiers = _build_assertion_qualifiers(candidate, payload)
        if valid_to:
            qualifiers["stale_after"] = valid_to
        valid_from = normalize_iso_date(source.get("date"))
        date_status = _resolve_assertion_date_status(edge_traits, candidate, source, valid_from, qualifiers)
        hitl_req = int(bool(payload.get("hitl_required") or candidate.get("hitl_required")) or payload["disposition"] == "APPROVED")
        val_owner = (payload.get("validation_owner") or candidate.get("validation_owner") or DEFAULT_VALIDATION_OWNER) if hitl_req else None
        retrieval_elig = _compute_retrieval_eligibility(payload, qualifiers, confidence_score, spk_defect)
        return (
            target_id,
            digest({
                "subject": payload["subject_node_id"], "predicate": payload["predicate"],
                "object_node_id": object_node_id, "object_literal": object_literal,
                "scope": candidate["normalized_text"], "source_id": source["source_id"],
            }),
            payload["subject_node_id"], payload["predicate"], None, object_node_id, object_literal,
            None if object_node_id else "text", graph_assertion_type, "atomic", candidate["normalized_text"],
            speaker_or_author, source["source_id"], extraction_run_id, "dual_pipeline_structured_adapter",
            "human_source", confidence_label, confidence_score, validation_state, "current", valid_from,
            valid_to, "day" if valid_from else "unknown", date_status, now, None, None, "resolved",
            "candidate", 0, retrieval_elig, materiality, classification,
            canonical_audiences_json(candidate["audiences"]), hitl_req,
            "P1" if candidate["materiality"] == "high" else "P2", val_owner,
            json.dumps(qualifiers, sort_keys=True), "edge" if object_node_id else "claim", "[]", now, now, None,
        )

    @staticmethod
    def _can_corroborate_promote(
        current_dict: dict[str, Any], operation_source: dict[str, Any], target_assertion_id: str, cred: dict[str, Any]
    ) -> bool:
        if current_dict.get("canonical_status") != "candidate" or not cred.get("promotion_eligible"):
            return False
        if has_non_promotable_qualifiers(current_dict.get("qualifiers_json")):
            return False
        return (
            classify_speaker_attribution_defect(
                predicate=current_dict.get("predicate"),
                scope_text=current_dict.get("scope_text"),
                speaker_or_author=current_dict.get("speaker_or_author"),
                subject_node_id=current_dict.get("subject_node_id"),
                source_type=operation_source.get("source_type"),
                source_name=operation_source.get("title") or operation_source.get("source_name"),
                assertion_id=target_assertion_id,
            )
            is None
        )

    @staticmethod
    def _validate_canonical_approval(connection: sqlite3.Connection, approval_id: str) -> str:
        row = connection.execute(
            "SELECT approval_reference,scope_json FROM governance_approval "
            "WHERE (approval_id=? OR approval_reference=?) AND status='approved' AND active=1 "
            "ORDER BY approved_at DESC LIMIT 1",
            (approval_id, approval_id),
        ).fetchone()
        if not row:
            raise RuntimeError("approved assertion requires an active graph governance approval")
        if "canonical_assertion_promotion" not in json.loads(row["scope_json"]).get("allowed_actions", []):
            raise RuntimeError("graph approval does not authorize canonical assertion promotion")
        return row["approval_reference"]

    @staticmethod
    def _ensure_inline_canonical_approval(connection: sqlite3.Connection, approval_id: str | None, now_iso: str) -> None:
        if not approval_id or not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_approval'").fetchone():
            return
        cols = {r[1] for r in connection.execute("PRAGMA table_info(governance_approval)").fetchall()}
        dig = digest(approval_id)
        row_map = {
            "approval_id": approval_id, "approval_reference": approval_id, "approval_type": "ingestion_promotion",
            "status": "approved", "approved_by": "governed_intake_pipeline", "approved_at": now_iso, "created_at": now_iso,
            "proposal_sha256": dig, "approval_statement_sha256": dig, "release_version": "1.0.0",
            "scope_json": json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}, sort_keys=True),
            "notes": "Auto-registered for governed inline canonical promotion", "active": 1,
        }
        pcols = [c for c in row_map if c in cols]
        ins_sql = "INSERT OR IGNORE INTO governance_approval (" + ",".join(pcols) + ") VALUES (" + ",".join("?" for _ in pcols) + ")"
        connection.execute(ins_sql, [row_map[c] for c in pcols])

    def _resolve_canonical_approval_ref(
        self, connection: sqlite3.Connection, change_set: dict[str, Any], payloads: dict[str, Any], source: dict[str, Any], now_iso: str
    ) -> str | None:
        has_approved = any(
            op.get("operation") == "add_approved_assertion" or payloads.get(op.get("operation_id"), {}).get("disposition") == "APPROVED"
            for op in change_set["operations"]
        )
        if not has_approved:
            return None
        if source.get("inline_canonical_promotion"):
            self._ensure_inline_canonical_approval(connection, change_set.get("approval_id"), now_iso)
        return self._validate_canonical_approval(connection, change_set.get("approval_id"))

    @staticmethod
    def remediate_supersession_and_candidate_eligibility(
        connection: sqlite3.Connection, now_iso: str
    ) -> dict[str, int]:
        """Repair cross-object supersession defects and enforce retrieval_eligible=0 on non-canonical/historical assertions."""
        # 1. Restore cross-object false supersessions on canonical assertions (e.g. 40 EDO/NEE IT superseded by 66 NEA)
        has_ga = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_approval'"
        ).fetchone()
        if has_ga:
            connection.execute(
                """UPDATE governance_approval
                   SET scope_json = json_insert(scope_json, '$.allowed_actions[#]', 'canonical_assertion_promotion')
                   WHERE approval_reference = 'AD-V48-INTEGRATED-HARDENING-001'
                     AND NOT EXISTS (
                         SELECT 1 FROM json_each(governance_approval.scope_json, '$.allowed_actions') act
                         WHERE act.value = 'canonical_assertion_promotion'
                     )"""
            )
        has_ae = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_evidence'"
        ).fetchone()
        ev_filter = (
            "AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports')"
            if has_ae
            else ""
        )
        cur_restored = connection.execute(
            f"""UPDATE assertion
               SET validation_state = 'accepted_legacy',
                   currentness_state = 'current',
                   superseded_by_assertion_id = NULL,
                   superseded_at = NULL,
                   retrieval_eligible = 1,
                   updated_at = ?
               WHERE assertion_id IN (
                   SELECT a.assertion_id
                   FROM assertion a
                   JOIN assertion b ON b.assertion_id = a.superseded_by_assertion_id
                   WHERE a.canonical_status = 'canonical'
                     AND a.canonical_eligible = 1
                     AND a.object_node_id IS NOT NULL
                     AND b.object_node_id IS NOT NULL
                     AND a.object_node_id != b.object_node_id
                     AND a.subject_node_id = b.subject_node_id
                     {ev_filter}
               )""",
            (now_iso,),
        )
        restored_count = cur_restored.rowcount if cur_restored.rowcount != -1 else 0

        # 2. Enforce retrieval_eligible=0 on any candidate, uneligible, or non-current assertion
        cur_demoted = connection.execute(
            """UPDATE assertion
               SET retrieval_eligible = 0,
                   updated_at = ?
               WHERE (canonical_status != 'canonical'
                      OR COALESCE(canonical_eligible, 0) = 0
                      OR COALESCE(currentness_state, 'current') != 'current')
                 AND COALESCE(retrieval_eligible, 0) != 0""",
            (now_iso,),
        )
        demoted_count = cur_demoted.rowcount if cur_demoted.rowcount != -1 else 0

        # 3. Sync search_document with assertion canonical/currentness state (when not already synced by DB triggers)
        has_sd = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'"
        ).fetchone()
        has_sd_trigger = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='search_document_au'"
        ).fetchone()
        if has_sd and not has_sd_trigger:
            connection.execute(
                """UPDATE search_document
                   SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                       currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                       retrieval_eligible = (SELECT a.retrieval_eligible FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                       updated_at = ?
                   WHERE assertion_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM assertion a WHERE a.assertion_id = search_document.assertion_id)""",
                (now_iso,),
            )

        # 4. Sync legacy claim table supersession from assertion table
        has_claim = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claim'"
        ).fetchone()
        synced_claims = 0
        if has_claim:
            cur_claim = connection.execute(
                """UPDATE claim
                   SET superseded_by = (
                       SELECT REPLACE(a.superseded_by_assertion_id, 'asrt_claim_', '')
                       FROM assertion a
                       WHERE a.assertion_id = 'asrt_claim_' || claim.claim_id
                         AND a.superseded_by_assertion_id IS NOT NULL
                   )
                   WHERE superseded_by IS NULL
                     AND EXISTS (
                         SELECT 1 FROM assertion a
                         JOIN claim c2 ON c2.claim_id = REPLACE(a.superseded_by_assertion_id, 'asrt_claim_', '')
                         WHERE a.assertion_id = 'asrt_claim_' || claim.claim_id
                           AND a.superseded_by_assertion_id IS NOT NULL
                     )"""
            )
            synced_claims = cur_claim.rowcount if cur_claim.rowcount != -1 else 0

        return {
            "restored_false_supersessions": restored_count,
            "demoted_non_canonical_retrieval_eligible": demoted_count,
            "synced_legacy_claims": synced_claims,
        }




