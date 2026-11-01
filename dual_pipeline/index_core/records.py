"""Retrieval record rendering, metadata derivation, and object facet projections."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    connect_sqlite,
    normalize_iso_date,
    normalize_space,
    sha256_bytes,
    stable_id,
)
from dual_pipeline.label_quality import canonicalize_audiences
from dual_pipeline.strategic_reasoning import (
    answer_use_for_profile,
    explicit_strategic_profile,
)

from .constants import (
    APPROVED_VALIDATION_STATES,
    ARTIFACT_ID_PATTERN,
    CLASSIFICATION_RANK,
    ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS,
    ENTITY_FACET_MAX_ASSERTIONS,
    ENTITY_FACET_MAX_TEXT_CHARACTERS,
    LEGACY_DEFAULT_RETRIEVAL_MODE_BY_LANES,
    RETRIEVAL_KIND_BY_RECORD_TYPE,
    RETRIEVAL_METADATA_FIELDS,
    RETRIEVAL_MODE_POLICY,
)
from dual_pipeline.index_core.eligibility import classify_retrieval_eligibility, is_single_source_directional  # noqa: lazy-decouple
from dual_pipeline.l0.assumption_lifecycle import (
    adjust_retrieval_record_for_assumption,
    build_assertion_retrieval_extensions,
)


def _classification(value: str) -> str:
    return "public" if value == "external_approved" else value if value in {
        "public",
        "internal",
        "confidential",
        "restricted",
    } else "internal"


def _audiences(raw: str) -> list[str]:
    # Access labels fail closed. A malformed graph row must never acquire a
    # default audience merely because its JSON cannot be parsed.
    return list(canonicalize_audiences(raw))


def _contract_reference(prefix: str, value: str) -> str:
    return value if ARTIFACT_ID_PATTERN.fullmatch(value) else stable_id(prefix, value, length=28)


def retrieval_record_type_for_assertion(
    *, qualifiers_json: str | None, object_node_id: str | None
) -> str:
    """Classify an assertion's immutable retrieval projection without querying state."""

    try:
        qualifiers = json.loads(qualifiers_json or "{}")
    except (TypeError, json.JSONDecodeError):
        qualifiers = {}
    if not isinstance(qualifiers, dict):
        qualifiers = {}
    candidate_type = qualifiers.get("dual_pipeline_candidate_type")
    if candidate_type == "action":
        return "action_item"
    if candidate_type == "source_chunk":
        return "source_chunk"
    if candidate_type == "architecture_component":
        return "architecture_component"
    if candidate_type == "architecture_relationship":
        return "architecture_relationship"
    if object_node_id:
        return "edge"
    return "assertion"


def retrieval_metadata_for_record(
    record_type: str,
    lane: str,
    *,
    strategic_profile: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the immutable, orthogonal retrieval metadata for a record projection.

    These fields describe what may be retrieved and how it may be used.  They
    do not change graph authority: the graph assertion, evidence chain, lane,
    and generation binding remain the source of truth.
    """

    try:
        retrieval_kind = RETRIEVAL_KIND_BY_RECORD_TYPE[record_type]
    except KeyError as exc:
        raise ValueError(f"unsupported retrieval record type: {record_type}") from exc
    if lane not in {"approved", "candidate", "external_public"}:
        raise ValueError(f"unsupported retrieval lane: {lane}")
    epistemic_status = "approved" if lane == "approved" else "candidate"
    if retrieval_kind == "guidance":
        answer_use = "guidance"
    elif retrieval_kind == "evidence_span":
        answer_use = "citation_only"
    elif retrieval_kind == "entity_facet":
        answer_use = "navigation_only"
    elif epistemic_status == "approved":
        answer_use = "factual_answer"
    else:
        answer_use = "candidate_hypothesis"
    if strategic_profile is not None:
        answer_use = answer_use_for_profile(
            base_answer_use=answer_use,
            profile=strategic_profile,
            lane=lane,
        )
    return {
        "retrieval_kind": retrieval_kind,
        "epistemic_status": epistemic_status,
        # Assertions in the current projection retain a graph/evidence binding.
        # Later waves may emit more precise direct/derived/policy provenance.
        "provenance_kind": "source_bound",
        "answer_use": answer_use,
    }


def retrieval_metadata_from_artifact(artifact: dict[str, Any]) -> dict[str, str]:
    """Read stored retrieval metadata, deriving a safe legacy projection if absent."""

    present = [field in artifact for field in RETRIEVAL_METADATA_FIELDS]
    if not any(present):
        return retrieval_metadata_for_record(artifact["record_type"], artifact["lane"])
    if not all(present):
        raise RuntimeError("retrieval artifact has incomplete retrieval metadata")
    return {field: artifact[field] for field in RETRIEVAL_METADATA_FIELDS}


def resolve_retrieval_mode(
    allowed_lanes: list[str],
    retrieval_mode: str | None,
    *,
    allow_lane_subset: bool = False,
) -> tuple[str, dict[str, frozenset[str]]]:
    """Resolve a fixed, server-enforced retrieval policy.

    A caller cannot use a lane list to bypass the answer-use policy.  Legacy
    callers that provide one of the exact supported lane sets receive the
    conservative matching mode until they are upgraded to name a mode.
    """

    requested_lanes = tuple(dict.fromkeys(allowed_lanes))
    if not requested_lanes or len(requested_lanes) != len(allowed_lanes):
        raise ValueError("retrieval lanes must be nonempty and unique")
    if retrieval_mode is None:
        retrieval_mode = LEGACY_DEFAULT_RETRIEVAL_MODE_BY_LANES.get(
            frozenset(requested_lanes)
        )
        if retrieval_mode is None:
            raise ValueError(
                "retrieval_mode is required unless allowed_lanes exactly match a fixed policy"
            )
    try:
        policy = RETRIEVAL_MODE_POLICY[retrieval_mode]
    except KeyError as exc:
        raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}") from exc
    requested_set = set(requested_lanes)
    policy_set = set(policy)
    if allow_lane_subset:
        if not requested_set <= policy_set:
            raise ValueError(
                f"retrieval mode {retrieval_mode} does not permit lanes {sorted(requested_set)}"
            )
    elif requested_set != policy_set or len(requested_lanes) != len(policy):
        raise ValueError(
            f"retrieval mode {retrieval_mode} requires lanes {list(policy)}, "
            f"got {list(requested_lanes)}"
        )
    return retrieval_mode, policy


def _bounded_entity_facet_text(value: str, limit: int) -> str:
    """Normalize and deterministically bound a derived entity-facet fragment."""

    normalized = normalize_space(value)
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return f"{normalized[: limit - 3].rstrip()}..."


def render_entity_facet_records(
    assertion_records: Iterable[dict[str, Any]],
    *,
    entity_names: dict[str, str],
    graph_snapshot: str,
) -> list[dict[str, Any]]:
    """Render lane-isolated, evidence-bound navigation facets for graph entities.

    Facets are a derived retrieval projection, never a graph fact.  They are
    made only from assertion and relationship retrieval records with a graph
    assertion/evidence binding.  The target entity's canonical name is used
    solely as a label; node descriptions are intentionally never read or
    embedded.  A single facet may only contain one lane.  Its access envelope
    is the most restrictive contributing classification and the intersection
    of all contributing audiences, so a facet cannot disclose a looser union
    of its source assertions.
    """

    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in assertion_records:
        lane = record.get("lane")
        if lane not in {"approved", "candidate"}:
            continue
        try:
            source_metadata = retrieval_metadata_from_artifact(record)
        except (KeyError, ValueError):
            continue
        expected_status = "approved" if lane == "approved" else "candidate"
        if (
            source_metadata["retrieval_kind"]
            not in {"assertion", "relationship_assertion"}
            or source_metadata["answer_use"]
            not in {"factual_answer", "candidate_hypothesis"}
            or source_metadata["epistemic_status"] != expected_status
        ):
            continue
        extensions = record.get("extensions")
        if not isinstance(extensions, dict):
            continue
        assertion_id = extensions.get("com.partner_advisory.nee.graph-object-id")
        graph_evidence_ids = extensions.get("com.partner_advisory.nee.graph-evidence-ids")
        graph_entity_ids = extensions.get("com.partner_advisory.nee.graph-entity-ids")
        evidence_ids = record.get("evidence_ids")
        classification = record.get("classification")
        audiences = record.get("audiences")
        record_id = record.get("record_id")
        if (
            not isinstance(assertion_id, str)
            or not isinstance(graph_evidence_ids, list)
            or not all(isinstance(value, str) and value for value in graph_evidence_ids)
            or not isinstance(graph_entity_ids, list)
            or not all(isinstance(value, str) and value for value in graph_entity_ids)
            or not isinstance(evidence_ids, list)
            or not all(isinstance(value, str) and value for value in evidence_ids)
            or classification not in CLASSIFICATION_RANK
            or not isinstance(audiences, list)
            or not all(isinstance(value, str) and value for value in audiences)
            or not isinstance(record_id, str)
            or not record_id
        ):
            continue
        for entity_id in sorted(set(graph_entity_ids)):
            if not normalize_space(str(entity_names.get(entity_id, ""))):
                continue
            grouped[(lane, entity_id)][record_id] = record

    facets: list[dict[str, Any]] = []
    for (lane, entity_id), by_record_id in sorted(grouped.items()):
        source_records = [
            by_record_id[record_id] for record_id in sorted(by_record_id)
        ]
        shared_audiences: set[str] | None = None
        for source_record in source_records:
            source_audiences = set(source_record["audiences"])
            shared_audiences = (
                source_audiences
                if shared_audiences is None
                else shared_audiences & source_audiences
            )
        # An entity profile that has no audience which can see every source
        # record would leak a union of protected context.  Omit it entirely.
        if not shared_audiences:
            continue
        evidence_ids = sorted(
            {
                evidence_id
                for source_record in source_records
                for evidence_id in source_record["evidence_ids"]
            }
        )
        graph_assertion_ids = sorted(
            {
                source_record["extensions"]["com.partner_advisory.nee.graph-object-id"]
                for source_record in source_records
            }
        )
        graph_evidence_ids = sorted(
            {
                evidence_id
                for source_record in source_records
                for evidence_id in source_record["extensions"][
                    "com.partner_advisory.nee.graph-evidence-ids"
                ]
            }
        )
        if not evidence_ids or not graph_assertion_ids or not graph_evidence_ids:
            continue
        entity_name = _bounded_entity_facet_text(
            str(entity_names[entity_id]), ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS
        )
        profile = _bounded_entity_facet_text(
            f"Entity facet for {entity_name}. Evidence-bound graph relationships.",
            ENTITY_FACET_MAX_TEXT_CHARACTERS,
        )
        profile_source_record_ids: list[str] = []
        for source_record in source_records[:ENTITY_FACET_MAX_ASSERTIONS]:
            fragment = _bounded_entity_facet_text(
                str(source_record["retrieval_text"]),
                ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS,
            )
            if not fragment:
                continue
            separator = " | "
            remaining = ENTITY_FACET_MAX_TEXT_CHARACTERS - len(profile) - len(separator)
            if remaining <= 0:
                break
            profile = f"{profile}{separator}{_bounded_entity_facet_text(fragment, remaining)}"
            profile_source_record_ids.append(source_record["record_id"])
            if len(profile) >= ENTITY_FACET_MAX_TEXT_CHARACTERS:
                break
        if not profile_source_record_ids:
            continue
        # The source records are ordered by immutable record ID, so profile
        # truncation and the derived artifact digest remain deterministic.
        classification = max(
            (str(source_record["classification"]) for source_record in source_records),
            key=lambda value: CLASSIFICATION_RANK[value],
        )
        retrieval_metadata = retrieval_metadata_for_record("entity_facet", lane)
        record_id = stable_id("entity-facet", lane, entity_id, length=28)
        artifact = create_artifact(
            "retrieval-record",
            record_id,
            record_id=record_id,
            record_type="entity_facet",
            namespace=f"nee.{lane}.entity_facets",
            lane=lane,
            title=_bounded_entity_facet_text(
                f"Entity facet: {entity_name}",
                ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS,
            ),
            canonical_entities=[_contract_reference("entity-ref", entity_id)],
            retrieval_text=profile,
            retrieval_text_hash=sha256_bytes(profile.encode("utf-8")),
            evidence_cluster_id=stable_id("evidence-cluster", evidence_ids, length=24),
            evidence_ids=evidence_ids,
            graph_snapshot=graph_snapshot,
            classification=classification,
            audiences=sorted(shared_audiences),
            lifecycle="current" if lane == "approved" else "candidate",
            effective_date=None,
            action_status=None,
            **retrieval_metadata,
            extensions={
                "com.partner_advisory.nee.graph-entity-ids": [entity_id],
                "com.partner_advisory.nee.graph-assertion-ids": graph_assertion_ids,
                "com.partner_advisory.nee.graph-evidence-ids": graph_evidence_ids,
                "com.partner_advisory.nee.entity-facet-source-record-ids": sorted(by_record_id),
                "com.partner_advisory.nee.entity-facet-profile-record-ids": profile_source_record_ids,
            },
        )
        facets.append(artifact)
    return facets


def _resolve_row_lane(row: Any, row_keys: set[str]) -> str:
    conf_score = float(row["confidence_score"]) if "confidence_score" in row_keys and row["confidence_score"] is not None else 1.0
    can_elig = bool(row["canonical_eligible"]) if "canonical_eligible" in row_keys and row["canonical_eligible"] is not None else True
    if row["canonical_status"] == "canonical" and can_elig and row["validation_state"] in APPROVED_VALIDATION_STATES and conf_score >= 0.65:
        return "approved"
    return "candidate"


def render_retrieval_records(graph_path: Path, graph_snapshot: str) -> list[dict[str, Any]]:
    """Render the complete eligible approved and candidate assertion corpus."""

    connection = connect_sqlite(graph_path, readonly=True)
    try:
        rows = connection.execute(
            """SELECT a.*,sd.title AS search_title,sd.body AS search_body,
                      sn.canonical_name AS subject_name,onode.canonical_name AS object_name
               FROM assertion a
               JOIN node sn ON sn.node_id=a.subject_node_id
               LEFT JOIN node onode ON onode.node_id=a.object_node_id
               LEFT JOIN search_document sd ON sd.search_rowid=(
                   SELECT sd2.search_rowid FROM search_document sd2
                   WHERE sd2.assertion_id=a.assertion_id
                   ORDER BY CASE sd2.object_type
                              WHEN 'assertion' THEN 0
                              WHEN 'claim' THEN 1
                              ELSE 2
                            END,
                            sd2.search_rowid
                   LIMIT 1
               )
               WHERE (
                    (a.canonical_status='canonical' AND a.canonical_eligible=1 AND a.currentness_state='current')
                 OR (a.canonical_status IN ('candidate','historical_candidate') AND a.currentness_state IN ('current','unknown'))
               )
               AND a.retrieval_eligible=1
               AND EXISTS (
                   SELECT 1 FROM assertion_evidence ae
                   WHERE ae.assertion_id=a.assertion_id
                     AND ae.evidence_relationship='supports'
               )
               ORDER BY a.assertion_id"""
        ).fetchall()
        records: list[dict[str, Any]] = []
        entity_names: dict[str, str] = {}
        seen_slide_bodies: set[str] = set()
        for row in rows:
            row_keys = set(row.keys())
            assertion_type = row["assertion_type"] if "assertion_type" in row_keys else None
            allowed, _reason = classify_retrieval_eligibility(row, has_supporting_evidence=True, allow_unapproved_canonical_as_candidate=True)
            if not allowed:
                continue
            graph_evidence_ids = [
                value[0]
                for value in connection.execute(
                    "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? AND evidence_relationship='supports' ORDER BY evidence_id",
                    (row["assertion_id"],),
                )
            ]
            if not graph_evidence_ids:
                continue
            evidence_ids = [_contract_reference("evidence-ref", value) for value in graph_evidence_ids]
            lane = _resolve_row_lane(row, row_keys)
            if lane == "approved" and row["validation_state"] not in APPROVED_VALIDATION_STATES:
                continue
            try:
                qualifiers = json.loads(row["qualifiers_json"])
            except (TypeError, json.JSONDecodeError):
                qualifiers = {}
            if not isinstance(qualifiers, dict):
                qualifiers = {}
            record_type = retrieval_record_type_for_assertion(qualifiers_json=row["qualifiers_json"], object_node_id=row["object_node_id"])
            strategic_profile = explicit_strategic_profile(qualifiers)
            action_status = qualifiers.get("action_status") or "open" if record_type == "action_item" else None
            object_text = row["object_name"] or row["object_literal"] or ""
            generated_text = normalize_space(f"{row['subject_name']} {row['predicate']} {object_text}. {row['scope_text']}")
            raw_body = normalize_space(row["search_body"] or "")
            subj_prefix = normalize_space(str(row["subject_name"] or ""))[:30]
            if lane == "candidate" and raw_body in seen_slide_bodies and (not subj_prefix or not raw_body.startswith(subj_prefix)):
                retrieval_text = normalize_space(f"{generated_text} {raw_body[:200]}")
            else:
                retrieval_text = raw_body or generated_text
            if lane == "candidate" and len(raw_body) > 200:
                seen_slide_bodies.add(raw_body)
            title = normalize_space(
                row["search_title"]
                or f"{row['subject_name']} {row['predicate']} {object_text}"
            )
            graph_entities = [row["subject_node_id"]]
            subject_name = normalize_space(str(row["subject_name"] or ""))
            if subject_name:
                entity_names.setdefault(row["subject_node_id"], subject_name)
            if row["object_node_id"]:
                graph_entities.append(row["object_node_id"])
                object_name = normalize_space(str(row["object_name"] or ""))
                if object_name:
                    entity_names.setdefault(row["object_node_id"], object_name)
            entities = [_contract_reference("entity-ref", value) for value in graph_entities]
            record_id = stable_id("retrieval-record", row["assertion_id"], length=28)
            namespace_kind = {"action_item": "actions", "source_chunk": "source_chunks"}.get(record_type, "assertions")
            retrieval_metadata = retrieval_metadata_for_record(record_type, lane, strategic_profile=strategic_profile)
            strategic_fields = {"strategic_profile": strategic_profile} if strategic_profile is not None else {}
            status_snapshot = qualifiers.get("project_status_snapshot")
            status_fields = {"com.partner_advisory.nee.project-status-snapshot": status_snapshot} if isinstance(status_snapshot, dict) else {}
            ext_fields = build_assertion_retrieval_extensions(row, row_keys, graph_entities, graph_evidence_ids, status_fields)
            if is_single_source_directional(dict(row)):
                ext_fields["com.partner_advisory.nee.single-source-directional"] = True
            adjust_retrieval_record_for_assumption(
                retrieval_metadata, ext_fields, assertion_type=assertion_type, predicate=row["predicate"], lane=lane
            )
            record_cls = _classification(row["classification"])
            record_audiences = _audiences(row["allowed_audiences_json"])
            if record_cls in {"confidential", "restricted"} and (
                "nee-engineering" in record_audiences or "client_approved_external" in record_audiences
            ):
                filtered_aud = [a for a in record_audiences if a not in {"nee-engineering", "client_approved_external"}]
                record_audiences = filtered_aud or ["internal_consulting"]
            artifact = create_artifact(
                "retrieval-record", record_id, record_id=record_id, record_type=record_type,
                namespace=f"nee.{lane}.{namespace_kind}", lane=lane, title=title,
                canonical_entities=list(dict.fromkeys(entities)), retrieval_text=retrieval_text,
                retrieval_text_hash=sha256_bytes(retrieval_text.encode("utf-8")),
                evidence_cluster_id=stable_id("evidence-cluster", evidence_ids, length=24),
                evidence_ids=evidence_ids, graph_snapshot=graph_snapshot, classification=record_cls,
                audiences=record_audiences, lifecycle="current" if lane == "approved" else "candidate",
                effective_date=normalize_iso_date(row["valid_from"]), action_status=action_status,
                **retrieval_metadata, **strategic_fields, extensions=ext_fields,
            )
            records.append(artifact)
        return records + render_entity_facet_records(records, entity_names=entity_names, graph_snapshot=graph_snapshot)
    finally:
        connection.close()


def render_retrieval_record_object_facets(graph_path: Path, records: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Project approved controlled facets onto assertion retrieval records."""
    record_by_assertion_id: dict[str, str] = {}
    assertion_ids_by_node_id: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.get("record_type") == "entity_facet":
            continue
        extensions = record.get("extensions")
        if not isinstance(extensions, dict):
            continue
        assertion_id = extensions.get("com.partner_advisory.nee.graph-object-id")
        entity_ids = extensions.get("com.partner_advisory.nee.graph-entity-ids")
        record_id = record.get("record_id")
        if not isinstance(assertion_id, str) or not isinstance(record_id, str):
            continue
        if assertion_id in record_by_assertion_id:
            raise RuntimeError(f"multiple retrieval records map to graph assertion {assertion_id}")
        record_by_assertion_id[assertion_id] = record_id
        if isinstance(entity_ids, list):
            for entity_id in entity_ids:
                if isinstance(entity_id, str) and entity_id:
                    assertion_ids_by_node_id[entity_id].add(assertion_id)
    if not record_by_assertion_id:
        return []

    connection = connect_sqlite(graph_path, readonly=True)
    try:
        tables = {
            str(row["name"]) for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type='table'
                      AND name IN ('object_facet_assignment', 'controlled_value_registry', 'legacy_claim_assertion_map')"""
            )
        }
        if "object_facet_assignment" not in tables:
            return []
        if "controlled_value_registry" not in tables:
            raise RuntimeError("object facet assignments exist without the controlled-value registry")
        assertion_ids_by_claim_id: dict[str, str] = {}
        if "legacy_claim_assertion_map" in tables:
            assertion_ids_by_claim_id = {
                str(row["claim_id"]): str(row["assertion_id"])
                for row in connection.execute("SELECT claim_id,assertion_id FROM legacy_claim_assertion_map ORDER BY claim_id")
            }
        assignments = connection.execute(
            """SELECT f.assignment_id,f.object_type,f.object_id,f.vocabulary,f.value,f.assignment_method,f.review_state
                 FROM object_facet_assignment f
                 JOIN controlled_value_registry cv ON cv.vocabulary=f.vocabulary AND cv.value=f.value AND cv.active=1
                WHERE f.active=1 AND f.review_state='approved' ORDER BY f.assignment_id"""
        ).fetchall()
    finally:
        connection.close()

    rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for assignment in assignments:
        assignment_id, object_type, object_id = str(assignment["assignment_id"]), str(assignment["object_type"]), str(assignment["object_id"])
        if object_type == "assertion":
            assertion_ids = {object_id}
        elif object_type == "node":
            assertion_ids = assertion_ids_by_node_id.get(object_id, set())
        elif object_type == "claim":
            mapped = assertion_ids_by_claim_id.get(object_id)
            assertion_ids = {mapped} if mapped else set()
        else:
            raise RuntimeError(f"unsupported object facet type: {object_type}")
        for assertion_id in sorted(assertion_ids):
            record_id = record_by_assertion_id.get(assertion_id)
            if record_id is None:
                continue
            rows_by_key[(record_id, assignment_id)] = {
                "record_id": record_id, "assignment_id": assignment_id, "object_type": object_type, "object_id": object_id,
                "vocabulary": str(assignment["vocabulary"]), "value": str(assignment["value"]),
                "assignment_method": str(assignment["assignment_method"]), "review_state": str(assignment["review_state"]),
            }
    return [rows_by_key[key] for key in sorted(rows_by_key)]


def synchronize_missing_search_documents(connection: Any) -> int:
    """Populate missing search_document and search_fts rows for eligible assertions."""
    missing_rows = connection.execute(
        """SELECT a.assertion_id, a.predicate, a.object_literal, a.scope_text,
                  a.canonical_status, a.currentness_state, a.retrieval_eligible,
                  sn.canonical_name AS subject_name, onode.canonical_name AS object_name
           FROM assertion a
           JOIN node sn ON sn.node_id=a.subject_node_id
           LEFT JOIN node onode ON onode.node_id=a.object_node_id
           WHERE NOT EXISTS (SELECT 1 FROM search_document sd WHERE sd.assertion_id=a.assertion_id)"""
    ).fetchall()
    if not missing_rows:
        return 0
    fts_ok = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone() is not None
    for row in missing_rows:
        aid = str(row["assertion_id"])
        obj_text = row["object_name"] or row["object_literal"] or ""
        title = normalize_space(f"{row['subject_name']} {row['predicate']} {obj_text}")
        body = normalize_space(f"{title}. {row['scope_text'] or ''}")
        cur = connection.execute(
            """INSERT INTO search_document
               (search_document_id, object_type, object_id, title, body,
                canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id)
               VALUES (?, 'assertion', ?, ?, ?, ?, ?, ?, 'source-sync', ?)""",
            (f"sd-sync-{aid}", aid, title, body, row["canonical_status"], row["currentness_state"], row["retrieval_eligible"], aid),
        )
        if fts_ok and cur.lastrowid is not None:
            fts_sql = (
                "INSERT INTO search_fts(rowid, title, body) VALUES (?, ?, ?)"
            )
            connection.execute(fts_sql, (cur.lastrowid, title, body))
    return len(missing_rows)
