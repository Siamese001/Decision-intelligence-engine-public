"""Source-bound clearance and retrieval completion facts for document ingestion."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from dual_pipeline.core import connect_sqlite
from dual_pipeline.index_core.eligibility import classify_retrieval_eligibility
from dual_pipeline.retrieval_clearance import retrieval_clearance


class RetrievalClearancePending(PermissionError):
    """An operator must resolve source labels before requested retrieval work."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(
            f"RETRIEVAL_CLEARANCE_PENDING: {report['reason_code']}; "
            "obtain an authenticated classification/audience decision before ingestion"
        )


def _resolve_preflight_audiences(
    project: Optional[str], classification: str, raw_audiences: Optional[list[str]]
) -> list[str]:
    if raw_audiences:
        return list(raw_audiences)
    if project == "internal_consulting" or classification in ("confidential", "restricted"):
        return ["internal_consulting"]
    return ["internal_consulting", "nee-engineering"]


def preflight_document_clearance(metadata: dict[str, Any], *, required: bool) -> dict[str, Any]:
    """Use the same source labels as package generation, without changing them."""
    project = metadata.get("project")
    classification = metadata.get("classification") or "internal"
    raw_audiences = metadata.get("audiences")

    # Fail closed on illegal internal_consulting client audience leakage
    if project == "internal_consulting" and raw_audiences:
        client_leaks = [a for a in raw_audiences if a in ("nee-engineering", "client_approved_external")]
        if client_leaks:
            report = {
                "status": "BLOCKED_CLIENT_AUDIENCE_LEAK",
                "reason_code": "INTERNAL_CONSULTING_CLIENT_AUDIENCE_LEAK",
                "classification": classification,
                "audiences": list(raw_audiences),
                "retrieval_requested": bool(required),
                "labels_changed": False,
            }
            raise RetrievalClearancePending(report)

    audiences = _resolve_preflight_audiences(project, classification, raw_audiences)
    allowed, reason = retrieval_clearance(
        assertion_type=None, classification=classification, audiences=audiences,
    )
    report = {
        "status": "ELIGIBLE" if allowed else "PENDING_OPERATOR_DECISION",
        "reason_code": "ELIGIBLE" if allowed else str(reason).upper(),
        "classification": classification,
        "audiences": audiences,
        "retrieval_requested": bool(required),
        "labels_changed": False,
    }
    if required and not allowed:
        raise RetrievalClearancePending(report)
    return report


def _readonly(path: Path):
    path = Path(path).resolve(strict=True)
    return connect_sqlite(path, readonly=True)


def _source_assertions(graph: Path, source_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    with closing(_readonly(graph)) as connection:
        rows = connection.execute(
            """SELECT a.*, EXISTS(
                   SELECT 1 FROM assertion_evidence ae
                   WHERE ae.assertion_id=a.assertion_id
                     AND ae.evidence_relationship='supports'
               ) AS has_supporting_evidence
               FROM assertion a WHERE a.primary_source_id=? ORDER BY a.assertion_id""",
            (source_id,),
        ).fetchall()
    reasons: Counter[str] = Counter()
    assertions = []
    for row in rows:
        eligible, reason = classify_retrieval_eligibility(
            row, has_supporting_evidence=bool(row["has_supporting_evidence"]),
        )
        reasons[reason] += 1
        assertions.append({"assertion_id": row["assertion_id"],
                           "eligible": eligible, "reason_code": reason,
                           "canonical_status": row["canonical_status"]})
    return assertions, dict(sorted(reasons.items()))


def _source_records(sidecar: Path, source_id: str) -> dict[str, dict[str, str]]:
    with closing(_readonly(sidecar)) as connection:
        rows = connection.execute(
            """SELECT r.record_id,r.lane,r.retrieval_text_hash,r.artifact_json,
                      EXISTS(SELECT 1 FROM dense_vector d WHERE d.record_id=r.record_id) AS has_vector
               FROM retrieval_record r
               WHERE r.artifact_json LIKE ?
               ORDER BY r.record_id""",
            (f"%{source_id}%",),
        ).fetchall()
    records = {}
    for row in rows:
        artifact = json.loads(row["artifact_json"])
        extensions = artifact.get("extensions") or {}
        if extensions.get("com.partner_advisory.nee.source-id") == source_id:
            records[row["record_id"]] = {
                "lane": row["lane"], "text_sha256": row["retrieval_text_hash"],
                "assertion_id": extensions.get("com.partner_advisory.nee.graph-object-id"),
                "has_vector": bool(row["has_vector"]),
            }
    return records


def _record_query_kind_forbidden(
    query_kind: str | None,
    forbidden: set[str],
    semantic_denied: set[str],
    acl: set[str],
    lane: set[str],
) -> None:
    if query_kind == "semantic_paraphrase":
        semantic_denied.update(forbidden)
    elif query_kind == "acl_boundary":
        acl.update(forbidden)
    elif query_kind == "lane_isolation":
        lane.update(forbidden)


def _delta_hits(evaluation: dict[str, Any], record_ids: set[str]) -> dict[str, set[str]]:
    coverage = evaluation.get("coverage") or {}
    declared = coverage.get("added_or_changed_record_ids") or []
    if declared and isinstance(declared[0], dict):
        declared = [item["record_id"] for item in declared]
    delta = record_ids & set(declared)
    positive, retrieved, semantic_denied = set(), set(), set()
    acl, lane, leaked = set(), set(), set()
    for case in evaluation.get("case_results") or []:
        if case.get("scope") != "delta":
            continue
        expected = delta & set(case.get("expected_record_ids") or [])
        positive.update(expected)
        retrieved.update(expected & set(case.get("candidate_retrieved_record_ids") or []))
        forbidden = delta & set(case.get("forbidden_record_ids") or [])
        _record_query_kind_forbidden(case.get("query_kind"), forbidden, semantic_denied, acl, lane)
        leaked.update(forbidden & set(case.get("candidate_retrieved_record_ids") or []))
    return {
        "covered": delta, "positive": positive, "retrieved": retrieved,
        "semantic_denied": semantic_denied,
        "acl_denied": acl, "lane_denied": lane, "leaked": leaked,
    }


def _is_hits_unverified(new_text: set[str], hits: dict[str, set[str]]) -> bool:
    if not new_text <= hits["covered"]:
        return True
    if not new_text <= (hits["retrieved"] | hits["semantic_denied"]):
        return True
    if not new_text <= hits["acl_denied"]:
        return True
    if not new_text <= hits["lane_denied"]:
        return True
    return bool(hits["leaked"])


def _determine_source_status(
    assertions: list[dict[str, Any]],
    eligible_ids: set[str],
    indexed_ids: set[str],
    lane_mismatches: list[str],
    eligible_records: dict[str, Any],
    new_text: set[str],
    evaluation: dict[str, Any] | None,
    hits: dict[str, set[str]],
    encoded: int | None,
) -> str:
    if not assertions:
        return "NO_SOURCE_ASSERTIONS"
    if not eligible_ids:
        return "EXCLUDED_BY_POLICY"
    if lane_mismatches:
        return "UNAUTHORIZED_LANE_PROMOTION"
    if not eligible_ids <= indexed_ids:
        return "INDEX_INCOMPLETE"
    if any(not item["has_vector"] for item in eligible_records.values()):
        return "INDEX_INCOMPLETE"
    if not new_text:
        return "UNCHANGED_TEXT"
    if not evaluation or evaluation.get("status") != "PASS":
        return "RETRIEVAL_EVALUATION_PENDING"
    if _is_hits_unverified(new_text, hits):
        return "SOURCE_RETRIEVAL_UNVERIFIED"
    if encoded is None or encoded < len(new_text):
        return "EMBEDDING_DELTA_UNVERIFIED"
    return "VERIFIED"


def source_retrieval_completion(
    *, source_id: str, graph: Path, sidecar: Path,
    previous_sidecar: Path | None, evaluation: dict[str, Any] | None,
    embedding_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    """Report actual source assertions, indexed text and source-specific delta hits."""
    if not source_id:
        raise ValueError("Source-specific retrieval completion requires a source ID")
    assertions, reasons = _source_assertions(graph, source_id)
    records = _source_records(sidecar, source_id)
    previous = _source_records(previous_sidecar, source_id) if previous_sidecar else {}
    eligible_ids = {item["assertion_id"] for item in assertions if item["eligible"]}
    eligible_records = {rid: item for rid, item in records.items()
                        if item["assertion_id"] in eligible_ids}
    expected_lanes = {item["assertion_id"]: (
        "approved" if item["canonical_status"] == "canonical" else "candidate"
    ) for item in assertions if item["eligible"]}
    lane_mismatches = sorted(rid for rid, item in eligible_records.items()
                             if item["lane"] != expected_lanes[item["assertion_id"]])
    new_text = {rid for rid, item in eligible_records.items()
                if rid not in previous or previous[rid]["text_sha256"] != item["text_sha256"]}
    encoded = (embedding_receipt or {}).get("delta_records",
                                           (embedding_receipt or {}).get("delta_vectors"))
    hits = _delta_hits(evaluation or {}, set(eligible_records))
    indexed_ids = {item["assertion_id"] for item in eligible_records.values()}
    status = _determine_source_status(
        assertions, eligible_ids, indexed_ids, lane_mismatches,
        eligible_records, new_text, evaluation, hits, encoded,
    )
    return {
        "source_id": source_id, "status": status,
        "assertion_count": len(assertions), "eligible_assertion_count": len(eligible_ids),
        "exclusion_reasons": {key: value for key, value in reasons.items() if key != "ELIGIBLE"},
        "assertions": assertions,
        "indexed_record_ids": sorted(eligible_records),
        "new_text_record_ids": sorted(new_text),
        "delta_tested_record_ids": sorted(hits["positive"]),
        "delta_retrieved_record_ids": sorted(hits["retrieved"]),
        "acl_denied_record_ids": sorted(hits["acl_denied"]),
        "lane_denied_record_ids": sorted(hits["lane_denied"]),
        "forbidden_leakage_record_ids": sorted(hits["leaked"]),
        "global_delta_vectors": encoded,
        "candidate_promoted": bool(lane_mismatches),
        "lane_mismatch_record_ids": lane_mismatches,
    }


def completion_from_publication(
    mutation_result: dict[str, Any] | None, runtime_root: Path,
) -> dict[str, Any]:
    """Never call a prepared or baseline-only release source-retrievable."""
    result = mutation_result or {}
    if result.get("status") != "COMPLETE":
        return {"status": "PUBLICATION_PENDING", "publication_status": result.get("status"),
                "reason_code": result.get("reason_code") or result.get("next_action")}
    release = result.get("release") or {}
    receipt = release.get("receipt")
    if receipt is None:
        return {"status": "NO_NEW_TEXT_UNVERIFIED", "publication_status": "COMPLETE",
                "global_delta_vectors": 0, "new_text_record_ids": [],
                "retrieval_claimed": False}
    pair = receipt["next_pair"]
    runtime = Path(runtime_root).resolve()
    active = json.loads((runtime / "active_pair.json").read_text(encoding="utf-8"))
    if any(active.get(key) != pair.get(key) for key in (
        "generation_id", "graph_sha256", "sidecar_sha256", "model_manifest_digest"
    )):
        return {"status": "STALE_GENERATION", "generation_id": pair["generation_id"],
                "active_generation_id": active.get("generation_id")}
    previous = receipt["previous_pair"]
    source_ids = list(dict.fromkeys(
        item["source_id"] for item in receipt.get("component_runs", [])
        if isinstance(item, dict) and item.get("source_id")
    )) or ([receipt["source_id"]] if receipt.get("source_id") else [])
    reports = [source_retrieval_completion(
        source_id=source_id, graph=runtime / pair["graph_path"],
        sidecar=runtime / pair["sidecar_path"],
        previous_sidecar=runtime / previous["sidecar_path"],
        evaluation=release.get("retrieval_evaluation"),
        embedding_receipt=receipt.get("embedding_generation_receipt"),
    ) for source_id in source_ids]
    status = "VERIFIED" if all(item["status"] in {"VERIFIED", "UNCHANGED_TEXT"}
                               for item in reports) else "SOURCE_RETRIEVAL_UNVERIFIED"
    return {"status": status, "generation_id": pair["generation_id"],
            "publication_status": result["status"], "sources": reports,
            "retrieval_evaluation_status": (release.get("retrieval_evaluation") or {}).get("status"),
            "global_delta_vectors": receipt.get("embedding_generation_receipt", {}).get(
                "delta_records", receipt.get("embedding_generation_receipt", {}).get("delta_vectors"))}
