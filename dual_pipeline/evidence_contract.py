"""FinalEvidenceContract — typed boundary between C0 retrieval and PA prompt assembly.

Seals the complete evidence set with strata, coverage, provenance, and
graph/index generation binding into a contract-valid artifact.
"""
from __future__ import annotations

from typing import Any, Mapping

from .core import (
    seal_artifact,
    sha256_bytes,
    stable_id,
    utc_now,
)

FINAL_EVIDENCE_CONTRACT_ID = "urn:nee:dual-pipeline:final-evidence-contract:1.0.0"
SCHEMA_VERSION = "1.0.0"

# Valid strata conforming to final-evidence-contract.schema.json
VALID_STRATA = frozenset({
    "MUST_USE",
    "SUPPORTING",
    "CONTRADICTS",
    "BACKGROUND",
    "DEFINITIONS",
    "LINEAGE",
    "EXCLUDED",
})

VALID_LANES = frozenset({"approved", "candidate", "external_public"})
VALID_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})


def determine_stratum(
    record_id: str,
    record: Mapping[str, Any],
    lane: str,
    *,
    required_ids: set[str],
    contradiction_ids: set[str],
    anchor_ids: set[str],
) -> str:
    """Determine the evidence stratum for a record."""
    explicit_stratum = record.get("evidence_stratum")
    if isinstance(explicit_stratum, str) and explicit_stratum in VALID_STRATA:
        return explicit_stratum

    if record_id in contradiction_ids:
        return "CONTRADICTS"
    if record_id in required_ids:
        return "MUST_USE"
    if lane == "approved":
        return "MUST_USE" if record_id in anchor_ids else "SUPPORTING"
    if lane == "candidate":
        return "SUPPORTING"
    return "BACKGROUND"


def create_final_evidence_contract(
    *,
    query_id: str,
    run_id: str,
    graph_generation_id: str,
    graph_sha256: str,
    sidecar_sha256: str,
    packet: Mapping[str, Any],
    retrieval_evidence_report: Mapping[str, Any] | None = None,
    coverage_manifest: Mapping[str, Any] | None = None,
    classification: str = "internal",
    audiences: list[str] | None = None,
    retrieval_metadata: Mapping[str, Any] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and seal a FinalEvidenceContract artifact.

    Conforms to ``urn:nee:dual-pipeline:final-evidence-contract:1.0.0``.
    """
    created_at = utc_now()
    artifact_id = stable_id("final-evidence-contract", run_id, query_id, created_at)

    required_ids: set[str] = set()
    req_by_lane = packet.get("required_material_record_ids_by_lane")
    if isinstance(req_by_lane, Mapping):
        for ids in req_by_lane.values():
            if isinstance(ids, list):
                required_ids.update(ids)

    contradiction_ids: set[str] = set()
    for item in packet.get("counterevidence", []):
        if isinstance(item, Mapping):
            for key in ("record_id", "target_record_id", "source_record_id"):
                val = item.get(key)
                if isinstance(val, str) and val:
                    contradiction_ids.add(val)

    anchor_ids: set[str] = set(packet.get("exact_anchor_ids", [])) | set(packet.get("bm25_anchor_ids", []))

    evidence_records: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()

    for lane in ("approved", "candidate", "external_public"):
        for item in packet.get(lane, []):
            if isinstance(item, Mapping):
                record = item.get("record", item) if isinstance(item.get("record"), Mapping) else item
            else:
                continue

            raw_record_id = record.get("record_id") or "unknown-record"
            record_id = str(raw_record_id)
            if record_id in seen_record_ids:
                continue
            seen_record_ids.add(record_id)

            retrieval_text = str(record.get("retrieval_text", ""))
            raw_text_hash = record.get("retrieval_text_hash")
            retrieval_text_hash = (
                str(raw_text_hash)
                if isinstance(raw_text_hash, str) and len(raw_text_hash) == 64
                else sha256_bytes(retrieval_text.encode("utf-8"))
            )

            stratum = determine_stratum(
                record_id,
                record,
                lane,
                required_ids=required_ids,
                contradiction_ids=contradiction_ids,
                anchor_ids=anchor_ids,
            )

            rec: dict[str, Any] = {
                "record_id": record_id,
                "lane": lane,
                "stratum": stratum,
                "retrieval_text_hash": retrieval_text_hash,
            }

            evidence_ids = record.get("evidence_ids")
            if isinstance(evidence_ids, list) and evidence_ids:
                rec["evidence_ids"] = [str(eid) for eid in evidence_ids if eid]

            entity_ids = record.get("entity_ids")
            if isinstance(entity_ids, list) and entity_ids:
                rec["entity_ids"] = [str(eid) for eid in entity_ids if eid]

            retrieval_meta = item.get("retrieval_metadata") if isinstance(item.get("retrieval_metadata"), Mapping) else {}

            for score_key in ("dense_score", "sparse_score", "colbert_score", "rerank_score"):
                val = retrieval_meta.get(score_key) if score_key in retrieval_meta else record.get(score_key)
                if isinstance(val, (int, float)):
                    rec[score_key] = float(val)

            hop_depth = retrieval_meta.get("graph_hop_depth") if "graph_hop_depth" in retrieval_meta else record.get("graph_hop_depth")
            if isinstance(hop_depth, int) and hop_depth >= 0:
                rec["graph_hop_depth"] = hop_depth

            epistemic = record.get("epistemic_status")
            if epistemic in {"candidate", "approved", "disputed", "superseded", "retracted"}:
                rec["epistemic_status"] = epistemic
            elif lane == "approved":
                rec["epistemic_status"] = "approved"
            elif lane == "candidate":
                rec["epistemic_status"] = "candidate"

            rec["contradiction_flag"] = record_id in contradiction_ids

            evidence_records.append(rec)

    # Coverage summary computation
    if retrieval_evidence_report is not None and "requirement_outcomes" in retrieval_evidence_report:
        outcomes = retrieval_evidence_report["requirement_outcomes"]
        total_reqs = len(outcomes)
        covered_reqs = sum(1 for o in outcomes if isinstance(o, Mapping) and o.get("outcome") in {"covered", "excluded"})
        coverage_suff = all(isinstance(o, Mapping) and o.get("outcome") in {"covered", "excluded"} for o in outcomes)
        gaps = [
            str(o.get("reason_code") or f"uncovered requirement {o.get('requirement_id')}")
            for o in outcomes
            if isinstance(o, Mapping) and o.get("outcome") not in {"covered", "excluded"}
        ]
    elif coverage_manifest is not None and "requirements" in coverage_manifest:
        reqs = coverage_manifest["requirements"]
        total_reqs = len(reqs)
        covered_reqs = min(len(evidence_records), total_reqs)
        coverage_suff = covered_reqs >= total_reqs
        gaps = [] if coverage_suff else ["inferred coverage gap from manifest"]
    else:
        total_reqs = len(evidence_records)
        covered_reqs = len(evidence_records)
        coverage_suff = True
        gaps = []

    coverage_summary: dict[str, Any] = {
        "total_requirements": total_reqs,
        "covered_requirements": covered_reqs,
        "coverage_sufficient": coverage_suff,
    }
    if gaps:
        coverage_summary["gap_descriptions"] = gaps

    body: dict[str, Any] = {
        "contract_id": FINAL_EVIDENCE_CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "query_id": query_id,
        "run_id": run_id,
        "graph_generation_id": graph_generation_id or "gen-001",
        "graph_sha256": graph_sha256 if len(graph_sha256) == 64 else ("0" * 64),
        "sidecar_sha256": sidecar_sha256 if len(sidecar_sha256) == 64 else ("0" * 64),
        "evidence_records": evidence_records,
        "coverage_summary": coverage_summary,
        "classification": classification if classification in VALID_CLASSIFICATIONS else "internal",
        "audiences": audiences or ["nee-engineering"],
        "created_at": created_at,
        "extensions": dict(extensions) if extensions else {},
    }

    if retrieval_metadata is not None:
        body["retrieval_metadata"] = dict(retrieval_metadata)

    return seal_artifact(body)
