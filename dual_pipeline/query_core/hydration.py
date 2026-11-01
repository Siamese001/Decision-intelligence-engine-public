"""Exact retrieval, graph entity/assertion hydration, and evidence packet assembly."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Sequence, Set

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    connect_sqlite,
    digest,
)
from dual_pipeline.graph_retrieval import bm25_query_tokens
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.index import (
    CLASSIFICATION_RANK,
    resolve_retrieval_mode,
    retrieval_metadata_for_record,
    retrieval_metadata_from_artifact,
    retrieval_record_type_for_assertion,
)
from dual_pipeline.l2_policy import retrieval_lanes_for_mode
from dual_pipeline.label_quality import APPROVED_VALIDATION_STATES
from dual_pipeline.strategic_reasoning import (
    build_strategic_channels,
    explicit_strategic_profile,
    hydrate_quantitative_metadata,
)
from .constants import _entities
from .dynamic_entities import resolve_dynamic_entity_matches as _resolve_dynamic_entity_matches


def _classify_assertion_lane(row: Any) -> str | None:
    keys = set(row.keys())
    conf_raw = row["confidence_score"] if "confidence_score" in keys else None
    conf = float(conf_raw) if conf_raw is not None else 1.0
    if conf < 0.65:
        return None
    can_elig = bool(row["canonical_eligible"]) if "canonical_eligible" in keys and row["canonical_eligible"] is not None else True
    c_status = row["canonical_status"]
    val_state = row["validation_state"]
    cur_state = row["currentness_state"]
    if val_state in {"superseded", "rejected", "retired"}:
        return None
    if c_status == "canonical" and can_elig and val_state in APPROVED_VALIDATION_STATES and cur_state == "current" and row["retrieval_eligible"] == 1:
        return "approved"
    if (c_status in {"candidate", "historical_candidate"} and cur_state in {"current", "unknown", "historical"}) or (
        c_status == "canonical" and (val_state not in APPROVED_VALIDATION_STATES or not can_elig or row["retrieval_eligible"] != 1) and cur_state == "current"
    ):
        return "candidate"
    return None


def _parse_iso_date(raw_value: Any) -> dt.date | None:
    raw_d = str(raw_value or "")[:10]
    if len(raw_d) != 10:
        return None
    try:
        return dt.date.fromisoformat(raw_d)
    except ValueError:
        return None


def discover_unpromoted_recent_workstream_sources(
    connection: Any, workstream_node_ids: Sequence[str] | Set[str], *, max_age_days: int = 14
) -> list[dict[str, Any]]:
    """Return recent sources tied to the target workstream nodes that still have unpromoted candidate assertions."""
    node_ids = sorted({str(nid) for nid in workstream_node_ids if nid})
    src_cols = {str(r[1]) for r in connection.execute("PRAGMA table_info(source)").fetchall()}
    if not node_ids or not {"source_id", "source_name", "source_date", "source_posture"}.issubset(src_cols):
        return []
    ph = ",".join("?" for _ in node_ids)
    sql = (
        "SELECT s.source_id, s.source_name, s.source_date, s.source_posture, "
        "SUM(CASE WHEN a.canonical_status = 'canonical' AND a.canonical_eligible = 1 AND a.retrieval_eligible = 1 THEN 1 ELSE 0 END) AS canonical_count, "
        "SUM(CASE WHEN a.canonical_status IN ('candidate', 'historical_candidate') AND a.validation_state NOT IN ('superseded', 'rejected', 'retired') THEN 1 ELSE 0 END) AS unpromoted_count "
        "FROM source s JOIN assertion a ON a.primary_source_id = s.source_id "
        f"WHERE s.source_id IN (SELECT DISTINCT a2.primary_source_id FROM assertion a2 WHERE a2.subject_node_id IN ({ph}) OR a2.object_node_id IN ({ph})) "
        "GROUP BY s.source_id, s.source_name, s.source_date, s.source_posture HAVING unpromoted_count > 0 ORDER BY s.source_date DESC, s.source_id"
    )
    parsed = [
        (_parse_iso_date(r["source_date"]), {
            "source_id": r["source_id"], "source_name": r["source_name"], "source_date": r["source_date"],
            "source_posture": r["source_posture"], "canonical_count": int(r["canonical_count"] or 0),
            "unpromoted_count": int(r["unpromoted_count"] or 0),
        })
        for r in connection.execute(sql, [*node_ids, *node_ids]).fetchall()
    ]
    dates = [d for d, _ in parsed if d is not None]
    latest = max(dates) if dates else None
    return [info for d, info in parsed if latest is None or d is None or (latest - d).days <= max_age_days]


def _assertion_select_sql(connection: Any) -> str:
    ass_cols = {str(r["name"]) for r in connection.execute("PRAGMA table_info(assertion)")}
    opt_cols = [col for col in ("canonical_eligible", "confidence_score") if col in ass_cols]
    opt_select = ("," + ",".join(opt_cols)) if opt_cols else ""
    return f"""SELECT assertion_id,subject_node_id,object_node_id,classification,
                      allowed_audiences_json,canonical_status,validation_state,
                      currentness_state,retrieval_eligible,qualifiers_json{opt_select}
               FROM assertion
               WHERE EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id=assertion.assertion_id)"""


def _effective_requested_audiences(audiences: list[str], classification: str = "internal") -> set[str]:
    req = set(audiences)
    return (req | {"internal_consulting"}) if ("nee-engineering" in req and classification != "public") else req


def _fts_bm25_search(
    connection: Any,
    fts_query: str,
    allowed_lanes: set[str],
    authorized_assertions: dict[str, dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    fts_available = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'").fetchone() is not None
    if not (fts_query and fts_available):
        return False, []
    bm25_rows: list[dict[str, Any]] = []
    seen_bm25: set[str] = set()
    allow_cand = 1 if "candidate" in allowed_lanes else 0
    for row in connection.execute(
        """SELECT sd.search_document_id,sd.object_type,sd.object_id,sd.title,sd.body,sd.canonical_status,
                  sd.currentness_state,sd.retrieval_eligible,sd.source_id,sd.assertion_id,bm25(search_fts) AS bm25_score
             FROM search_fts JOIN search_document sd ON sd.search_rowid=search_fts.rowid
            WHERE search_fts MATCH ? AND (sd.currentness_state IN ('current', 'unknown') OR (?=1 AND sd.canonical_status='historical_candidate'))
              AND (sd.retrieval_eligible=1 OR (?=1 AND sd.canonical_status IN ('candidate', 'historical_candidate', 'canonical')))
            ORDER BY bm25_score,sd.search_document_id LIMIT 200""",
        (fts_query, allow_cand, allow_cand),
    ):
        aid = row["assertion_id"]
        if aid in authorized_assertions and aid not in seen_bm25:
            seen_bm25.add(aid)
            b = authorized_assertions[aid]
            bm25_rows.append(dict(row) | {"lane": b["lane"], "answer_use": b["answer_use"], "strategic_profile": b["strategic_profile"]})
    return True, bm25_rows


def _filter_status_snapshot_assertions(
    connection: Any,
    resolved_mode: str,
    authorized_assertions: dict[str, dict[str, Any]],
    authorized_nodes: set[str],
) -> tuple[dict[str, dict[str, Any]], set[str], list[str] | None]:
    if resolved_mode not in {"status_snapshot_current", "status_snapshot_history"}:
        return authorized_assertions, authorized_nodes, None
    snap_rows = {
        aid: b for aid, b in authorized_assertions.items()
        if b["lane"] == "candidate" and b["answer_use"] == "citation_only" and isinstance(b["project_status_snapshot"], dict)
    }
    if snap_rows:
        if resolved_mode == "status_snapshot_current":
            latest: dict[str, str] = {}
            for b in snap_rows.values():
                p = b["project_status_snapshot"]
                latest[str(p["scope_id"])] = max(latest.get(str(p["scope_id"]), ""), str(p["as_of_date"]))
            snap_rows = {
                aid: b for aid, b in snap_rows.items()
                if str(b["project_status_snapshot"]["as_of_date"]) == latest[str(b["project_status_snapshot"]["scope_id"])]
            }
        authorized_assertions = snap_rows
    new_nodes: set[str] = set()
    for aid in authorized_assertions:
        row = connection.execute("SELECT subject_node_id,object_node_id FROM assertion WHERE assertion_id=?", (aid,)).fetchone()
        if row is not None:
            new_nodes.add(row["subject_node_id"])
            if row["object_node_id"]:
                new_nodes.add(row["object_node_id"])
    return authorized_assertions, new_nodes, sorted(authorized_assertions)


def _unpromoted_sources_for_matches(
    connection: Any, entity_matches: list[Any], authorized_nodes: set[str]
) -> list[dict[str, Any]]:
    has_src = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source'").fetchone() is not None
    if not has_src:
        return []
    m_ids = [str(m["node_id"] if isinstance(m, dict) else m) for m in entity_matches if (m.get("node_id") if isinstance(m, dict) else m)]
    target_ids = m_ids or authorized_nodes
    return discover_unpromoted_recent_workstream_sources(connection, target_ids) if target_ids else []


def exact_retrieval(  # noqa: C901
    question: str,
    graph_path: Path,
    *,
    classification: str,
    audiences: list[str],
    retrieval_mode: str,
    allowed_assertion_ids: list[str] | None = None,
) -> dict[str, Any]:
    tokens = bm25_query_tokens(question)
    expected_lanes = list(retrieval_lanes_for_mode(retrieval_mode))
    resolved_mode, mode_policy = resolve_retrieval_mode(expected_lanes, retrieval_mode)
    allowed_lanes = set(expected_lanes)
    assertion_scope = None if allowed_assertion_ids is None else set(allowed_assertion_ids)
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        requested_audiences = _effective_requested_audiences(audiences, classification=classification)
        authorized_assertions: dict[str, dict[str, Any]] = {}
        authorized_nodes: set[str] = set()
        for row in connection.execute(_assertion_select_sql(connection)):
            if assertion_scope is not None and row["assertion_id"] not in assertion_scope:
                continue
            lane = _classify_assertion_lane(row)
            if not lane or lane not in allowed_lanes:
                continue
            record_type = retrieval_record_type_for_assertion(qualifiers_json=row["qualifiers_json"], object_node_id=row["object_node_id"])
            try:
                qualifiers = json.loads(row["qualifiers_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                qualifiers = {}
            strategic_profile = explicit_strategic_profile(qualifiers)
            retrieval_metadata = retrieval_metadata_for_record(record_type, lane, strategic_profile=strategic_profile)
            if retrieval_metadata["answer_use"] not in mode_policy[lane]:
                continue
            try:
                record_audiences = set(json.loads(row["allowed_audiences_json"]))
            except (TypeError, json.JSONDecodeError):
                record_audiences = set()
            if row["classification"] in CLASSIFICATION_RANK and CLASSIFICATION_RANK[row["classification"]] <= CLASSIFICATION_RANK[classification] and bool(requested_audiences & record_audiences):
                status_snapshot = qualifiers.get("project_status_snapshot") if isinstance(qualifiers.get("project_status_snapshot"), dict) else None
                authorized_assertions[row["assertion_id"]] = {"lane": lane, "answer_use": retrieval_metadata["answer_use"], "strategic_profile": strategic_profile, "project_status_snapshot": status_snapshot}
                authorized_nodes.add(row["subject_node_id"])
                if row["object_node_id"]:
                    authorized_nodes.add(row["object_node_id"])
        authorized_assertions, authorized_nodes, status_snapshot_assertion_ids = _filter_status_snapshot_assertions(
            connection, resolved_mode, authorized_assertions, authorized_nodes
        )
        token_patterns = [(token, re.compile(r"\b" + re.escape(token) + r"\b")) for token in tokens]
        rows: list[dict[str, Any]] = []
        for row in connection.execute(
            """SELECT sd.search_document_id,sd.object_type,sd.object_id,sd.title,sd.body,sd.canonical_status,
                      sd.currentness_state,sd.retrieval_eligible,sd.source_id,sd.assertion_id
               FROM search_document sd ORDER BY sd.search_document_id"""
        ):
            if row["assertion_id"] not in authorized_assertions:
                continue
            b = authorized_assertions[row["assertion_id"]]
            text = f"{row['title']} {row['body']}".casefold()
            matched = [token for token, pat in token_patterns if pat.search(text)]
            if matched:
                rows.append(dict(row) | {"lane": b["lane"], "answer_use": b["answer_use"], "strategic_profile": b["strategic_profile"], "matched_tokens": matched, "exact_score": len(matched)})
        rows.sort(key=lambda value: (-value["exact_score"], value["search_document_id"]))
        fts_query = " OR ".join(f'"{token}"' for token in tokens)
        bm25_executed, bm25_rows = _fts_bm25_search(connection, fts_query, allowed_lanes, authorized_assertions)
        bm25_ranks_by_lane = {
            lane: {aid: rank for rank, aid in enumerate([r["assertion_id"] for r in bm25_rows if r["lane"] == lane], 1)}
            for lane in expected_lanes
        }
        entity_matches = _resolve_dynamic_entity_matches(connection, question, authorized_nodes)
        unpromoted_recent_sources = _unpromoted_sources_for_matches(connection, entity_matches, authorized_nodes)
        result = {
            "retrieval_mode": resolved_mode, "allowed_lanes": sorted(allowed_lanes),
            "tokens": tokens, "bm25_query": fts_query, "bm25_executed": bm25_executed,
            "bm25_anchors": bm25_rows[:40], "bm25_ranks_by_lane": bm25_ranks_by_lane,
            "entity_matches": entity_matches, "exact_anchors": rows[:40],
            "anchor_count": len({value["search_document_id"] for value in [*rows[:40], *bm25_rows[:40]]}),
            "unpromoted_recent_sources": unpromoted_recent_sources,
        }
        if assertion_scope is not None:
            result["assertion_scope_count"] = len(assertion_scope)
            result["assertion_scope_digest"] = digest(sorted(assertion_scope))
        if status_snapshot_assertion_ids is not None:
            result["status_snapshot_assertion_ids"] = status_snapshot_assertion_ids
            result["status_snapshot_selection"] = "latest_per_scope" if resolved_mode == "status_snapshot_current" else "history"
        return result
    finally:
        connection.close()


def hydrate_results(
    retrieval: dict[str, Any],
    graph_path: Path,
    *,
    allowed_assertion_ids: list[str] | None = None,
    connected_evidence_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assertion_scope = None if allowed_assertion_ids is None else set(allowed_assertion_ids)
    connected_paths_by_record: dict[str, dict[str, Any]] = {}
    if connected_evidence_map is not None:
        validate_artifact(connected_evidence_map)
        if connected_evidence_map["graph_sha256"] != cached_sha256_file(graph_path):
            raise RuntimeError("connected evidence map graph binding mismatch")
        connected_paths_by_record = {
            value["target_record_id"]: value for value in connected_evidence_map["paths"]
        }
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        strata: dict[str, list[dict[str, Any]]] = {"approved": [], "candidate": [], "external_public": []}
        graph_paths: list[dict[str, Any]] = []
        for lane, lane_result in retrieval["lanes"].items():
            for result in lane_result["results"]:
                record = result["record"]
                assertion_id = record["extensions"].get("com.partner_advisory.nee.graph-object-id")
                if assertion_scope is not None and assertion_id not in assertion_scope:
                    continue
                assertion = connection.execute(
                    """SELECT a.*,sn.canonical_name AS subject_name,onode.canonical_name AS object_name
                       FROM assertion a JOIN node sn ON sn.node_id=a.subject_node_id
                       LEFT JOIN node onode ON onode.node_id=a.object_node_id WHERE a.assertion_id=?""",
                    (assertion_id,),
                ).fetchone()
                if not assertion:
                    continue
                evidence = [
                    dict(row) for row in connection.execute(
                        """SELECT e.evidence_id,e.source_id,e.source_version_id,e.locator_kind,
                                  e.locator_value,e.evidence_directness,e.source_authority_rank,
                                  ae.evidence_relationship,s.source_name,s.source_posture
                           FROM assertion_evidence ae JOIN evidence_item e ON e.evidence_id=ae.evidence_id
                           JOIN source s ON s.source_id=e.source_id WHERE ae.assertion_id=? ORDER BY e.evidence_id""",
                        (assertion_id,),
                    )
                ]
                path = {
                    "record_id": record["record_id"], "assertion_id": assertion_id,
                    "subject_id": assertion["subject_node_id"], "subject": assertion["subject_name"],
                    "predicate": assertion["predicate"], "object_id": assertion["object_node_id"],
                    "object": assertion["object_name"] or assertion["object_literal"],
                    "lane": lane, "evidence_ids": [value["evidence_id"] for value in evidence],
                }
                quant = hydrate_quantitative_metadata(assertion, path)
                if connected_evidence_map is not None:
                    connected_path = connected_paths_by_record.get(record["record_id"])
                    if connected_path is None:
                        raise RuntimeError("selected retrieval record is missing a connected path")
                    path.update({
                        "connected_path_id": connected_path["path_id"], "seed_record_id": connected_path["seed_record_id"],
                        "origin": connected_path["origin"], "attachment_kind": connected_path["attachment_kind"],
                        "hop_count": connected_path["hop_count"], "path_score": connected_path["path_score"],
                        "topology_score": connected_path["topology_score"], "steps": connected_path["steps"],
                    })
                graph_paths.append(path)
                strata[lane].append({"retrieval": {key: value for key, value in result.items() if key != "record"}, "record": record, "retrieval_metadata": retrieval_metadata_from_artifact(record), "graph_path": path, "evidence": evidence, "quantitative_constraints": quant})
        hydrated = {"strata": strata, "graph_paths": graph_paths}
        if connected_evidence_map is not None:
            hydrated.update({
                "connected_evidence_map_id": connected_evidence_map["map_id"],
                "connected_evidence_map_digest": connected_evidence_map["canonical_digest"]["value"],
                "graph_retrieval_metrics": connected_evidence_map["metrics"],
            })
        if assertion_scope is not None:
            hydrated["assertion_scope_count"] = len(assertion_scope)
            hydrated["assertion_scope_digest"] = digest(sorted(assertion_scope))
        return hydrated
    finally:
        connection.close()


def _validate_facet_audiences(audiences: list[Any], classification: str) -> set[str]:
    req = {value for value in audiences if isinstance(value, str) and value.strip()}
    if len(req) != len(audiences) or not req:
        raise ValueError("entity facet hydration request audiences are invalid")
    return _effective_requested_audiences(list(req)) if classification != "public" else req


def hydrate_entity_facet_results(  # noqa: C901
    entity_retrieval: dict[str, Any], graph_path: Path
) -> dict[str, Any]:
    """Hydrate a strict entity-navigation result without producing an answer.

    Entity facets are navigation projections, not factual answer records.  A
    caller must supply the exact result returned by
    :meth:`HybridRetriever.retrieve_entity_facets`; this helper validates that
    shape and only expands the assertion IDs embedded in each facet.  Approved
    evidence and candidate evidence remain in separate output fields.
    """

    if entity_retrieval.get("entity_navigation") is not True:
        raise ValueError("entity facet hydration requires a strict entity navigation result")
    lane = entity_retrieval.get("lane")
    expected_modes = {
        "approved": "entity_navigation",
        "candidate": "candidate_entity_navigation",
    }
    if lane not in expected_modes:
        raise ValueError("entity facet hydration lane must be approved or candidate")
    if entity_retrieval.get("retrieval_mode") != expected_modes[lane]:
        raise ValueError("entity facet hydration retrieval mode is not lane-bound")
    if entity_retrieval.get("allowed_answer_uses") != ["navigation_only"]:
        raise ValueError("entity facet hydration requires navigation-only retrieval")
    request = entity_retrieval.get("entity_navigation_request")
    if not isinstance(request, dict) or request.get("lane") != lane:
        raise ValueError("entity facet hydration requires the navigation request binding")
    classification = request.get("classification")
    audiences = request.get("audiences")
    if classification not in CLASSIFICATION_RANK or not isinstance(audiences, list):
        raise ValueError("entity facet hydration request access binding is invalid")
    requested_audiences = _validate_facet_audiences(audiences, str(classification))
    graph_binding = entity_retrieval.get("entity_navigation_graph_binding")
    if not isinstance(graph_binding, dict) or not all(
        isinstance(graph_binding.get(field), str) and graph_binding[field]
        for field in ("generation_id", "graph_snapshot", "graph_sha256")
    ):
        raise ValueError("entity facet hydration requires a graph/index binding")
    if cached_sha256_file(graph_path) != graph_binding["graph_sha256"]:
        raise RuntimeError("entity facet hydration graph binding does not match")
    results = entity_retrieval.get("results")
    if not isinstance(results, list):
        raise ValueError("entity facet hydration requires a result list")

    def required_string_list(value: Any, field: str) -> list[str]:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"entity facet hydration has invalid {field}")
        return list(value)

    navigation_anchors: list[dict[str, Any]] = []
    hydrated_by_assertion: dict[str, dict[str, Any]] = {}
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        for result in results:
            if not isinstance(result, dict) or result.get("lane") != lane:
                raise ValueError("entity facet hydration detected lane contamination")
            record = result.get("record")
            if not isinstance(record, dict) or record.get("lane") != lane:
                raise ValueError("entity facet hydration record lane is invalid")
            # This helper accepts an in-memory retrieval result. Revalidate the
            # sealed artifact before trusting its graph assertion/evidence IDs;
            # otherwise a caller could mutate a wrapper result after retrieval.
            validate_artifact(record)
            metadata = retrieval_metadata_from_artifact(record)
            if (
                record.get("record_type") != "entity_facet"
                or metadata["retrieval_kind"] != "entity_facet"
                or metadata["answer_use"] != "navigation_only"
                or record.get("graph_snapshot") != graph_binding["graph_snapshot"]
            ):
                raise ValueError("entity facet hydration received a non-navigation record")
            record_classification = record.get("classification")
            record_audiences = record.get("audiences")
            if (
                record_classification not in CLASSIFICATION_RANK
                or not isinstance(record_audiences, list)
                or not all(isinstance(value, str) and value for value in record_audiences)
                or CLASSIFICATION_RANK[record_classification] > CLASSIFICATION_RANK[classification]
                or not (requested_audiences & set(record_audiences))
            ):
                raise ValueError("entity facet hydration record access binding is invalid")
            extensions = record.get("extensions")
            if not isinstance(extensions, dict):
                raise ValueError("entity facet hydration record extensions are invalid")
            entity_ids = required_string_list(extensions.get("com.partner_advisory.nee.graph-entity-ids"), "graph entity IDs")
            if len(entity_ids) != 1:
                raise ValueError("entity facet hydration requires one graph entity ID")
            assertion_ids = required_string_list(extensions.get("com.partner_advisory.nee.graph-assertion-ids"), "graph assertion IDs")
            evidence_ids = required_string_list(extensions.get("com.partner_advisory.nee.graph-evidence-ids"), "graph evidence IDs")
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("entity facet hydration record ID is invalid")
            navigation_anchors.append({"record_id": record_id, "lane": lane, "entity_id": entity_ids[0], "title": record.get("title"), "assertion_ids": assertion_ids, "evidence_ids": evidence_ids})
            evidence_id_set = set(evidence_ids)
            for assertion_id in assertion_ids:
                existing = hydrated_by_assertion.get(assertion_id)
                if existing is not None:
                    if entity_ids[0] not in {existing["subject_id"], existing["object_id"]} or not (evidence_id_set & {item["evidence_id"] for item in existing["evidence"]}):
                        raise RuntimeError("entity facet duplicate assertion is not evidence-bound")
                    existing["navigation_record_ids"].append(record_id)
                    continue
                assertion = connection.execute(
                    """SELECT a.*,sn.canonical_name AS subject_name,onode.canonical_name AS object_name
                       FROM assertion a JOIN node sn ON sn.node_id=a.subject_node_id
                       LEFT JOIN node onode ON onode.node_id=a.object_node_id WHERE a.assertion_id=?""",
                    (assertion_id,),
                ).fetchone()
                if assertion is None:
                    raise RuntimeError("entity facet references a missing graph assertion")
                if entity_ids[0] not in {assertion["subject_node_id"], assertion["object_node_id"]}:
                    raise RuntimeError("entity facet assertion is not anchored to the facet entity")
                if _classify_assertion_lane(assertion) != lane:
                    raise RuntimeError("entity facet assertion lane no longer matches graph state")
                try:
                    assertion_audiences = set(json.loads(assertion["allowed_audiences_json"]))
                except (TypeError, json.JSONDecodeError):
                    assertion_audiences = set()
                if (
                    assertion["classification"] not in CLASSIFICATION_RANK
                    or CLASSIFICATION_RANK[assertion["classification"]]
                    > CLASSIFICATION_RANK[classification]
                    or not (requested_audiences & assertion_audiences)
                ):
                    raise RuntimeError("entity facet assertion access binding is invalid")
                evidence = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT e.evidence_id,e.source_id,e.source_version_id,
                                  e.locator_kind,e.locator_value,
                                  e.evidence_directness,e.source_authority_rank,
                                  ae.evidence_relationship,s.source_name,s.source_posture
                           FROM assertion_evidence ae
                           JOIN evidence_item e ON e.evidence_id=ae.evidence_id
                           JOIN source s ON s.source_id=e.source_id
                           WHERE ae.assertion_id=?
                             AND ae.evidence_relationship='supports'
                           ORDER BY e.evidence_id""",
                        (assertion_id,),
                    )
                    if row["evidence_id"] in evidence_id_set
                ]
                if not evidence:
                    raise RuntimeError("entity facet assertion has no embedded supporting evidence")
                hydrated_by_assertion[assertion_id] = {
                    "assertion_id": assertion_id,
                    "lane": lane,
                    "navigation_record_ids": [record_id],
                    "subject_id": assertion["subject_node_id"],
                    "subject": assertion["subject_name"],
                    "predicate": assertion["predicate"],
                    "object_id": assertion["object_node_id"],
                    "object": assertion["object_name"] or assertion["object_literal"],
                    "evidence": evidence,
                }
    finally:
        connection.close()

    hydrated_assertions = []
    for assertion_id in sorted(hydrated_by_assertion):
        item = hydrated_by_assertion[assertion_id]
        item["navigation_record_ids"] = sorted(set(item["navigation_record_ids"]))
        hydrated_assertions.append(item)
    factual_evidence = hydrated_assertions if lane == "approved" else []
    candidate_evidence = hydrated_assertions if lane == "candidate" else []
    return {
        "entity_navigation": True,
        "lane": lane,
        "graph_binding": graph_binding,
        "navigation_anchors": navigation_anchors,
        "factual_evidence": factual_evidence,
        "candidate_evidence": candidate_evidence,
        "factual_evidence_count": len(factual_evidence),
        "candidate_evidence_count": len(candidate_evidence),
    }


def build_evidence_packet(
    *, hydrated: dict[str, Any], graph_snapshot: str, exact: dict[str, Any],
    retrieval_mode: str, card_scope: dict[str, Any] | None = None, connected_evidence_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategic = retrieval_mode == "strategic"
    connected_counterevidence = (
        [] if connected_evidence_map is None else [
            {
                **value,
                "statement": (
                    "Governed contradicting source evidence is attached to this record."
                    if value.get("kind") == "contradicting_evidence"
                    else "This record has a governed unresolved assertion conflict."
                ),
                "citations": sorted(set(
                    ([str(value["record_id"])] if value.get("record_id") else [])
                    + ([str(value["target_record_id"])] if value.get("target_record_id") else [])
                    + ([str(value["source_record_id"])] if value.get("source_record_id") else [])
                )),
            }
            for value in connected_evidence_map.get("counterevidence", [])
        ]
    )
    connected_gaps = [] if connected_evidence_map is None else connected_evidence_map.get("gaps", [])
    packet = {
        "retrieval_mode": retrieval_mode,
        "allowed_lanes": list(retrieval_lanes_for_mode(retrieval_mode)),
        "approved": hydrated["strata"]["approved"],
        "candidate": hydrated["strata"]["candidate"],
        "external_public": hydrated["strata"]["external_public"],
        "graph_paths": hydrated["graph_paths"],
        "counterevidence": connected_counterevidence,
        "gaps": (
            [
                "Candidate material may inform labeled hypotheses but cannot establish internal fact.",
                "External patterns may support analogy, not claims of NEE implementation.",
                "Official intent and target outcomes do not establish realized outcomes.",
            ]
            if strategic
            else [
                "Candidate material cannot establish internal fact.",
                "No external-public evidence was requested for this run.",
            ]
        ) + connected_gaps,
        "exclusions": [
            "Superseded, rejected, non-retrieval-eligible, and audience-incompatible records",
            "Automatic lane blending",
        ],
        "exact_anchor_ids": list(dict.fromkeys(
            value["search_document_id"] for value in exact.get("exact_anchors", [])
            if isinstance(value, dict) and "search_document_id" in value
        )),
        "bm25_anchor_ids": list(dict.fromkeys(
            value["search_document_id"] for value in exact.get("bm25_anchors", [])
            if isinstance(value, dict) and "search_document_id" in value
        )),
        "graph_snapshot": graph_snapshot,
    }
    if connected_evidence_map is not None:
        packet["connected_evidence_map"] = {
            "map_id": connected_evidence_map["map_id"],
            "map_digest": connected_evidence_map["canonical_digest"]["value"],
            "requested_hops": connected_evidence_map["requested_hops"],
            "max_hop_reached": connected_evidence_map["max_hop_reached"],
            "metrics": connected_evidence_map["metrics"],
        }
    if card_scope is not None:
        packet["card_scope"] = {
            "card_id": card_scope["card_id"], "card_view_id": card_scope["card_view_id"],
            "view_version": card_scope["view_version"], "selector_digest": card_scope["selector_digest"],
            "membership_digest": card_scope["membership_digest"],
            "lane_counts": {lane: len(assertion_ids) for lane, assertion_ids in card_scope["lanes"].items()},
        }
    if strategic:
        packet["strategic_context"] = build_strategic_channels(packet)
    packet["packet_digest"] = digest(packet)
    return packet
