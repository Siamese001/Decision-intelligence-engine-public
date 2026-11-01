"""Pipeline C -> Pipeline D: Single-Trip Decision Evidence-Pack Assembler.

Eliminates sequential per-citation retrieval roundtrips by compiling a complete,
policy-filtered, tenant-scoped EvidencePack in a single retrieval operation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    EvidencePack,
    PolicyContext,
)
from dual_pipeline.l0.policy import filter_candidates_pre_retrieval
from dual_pipeline.l0.provenance import compute_content_digest


_ADVISORY_CATEGORIES = {"STAKEHOLDER_DYNAMICS", "DECISION_INFLUENCE", "ADOPTION_RISK", "BLOCKER"}


def _normalize_candidate_namespaces(candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in candidates:
        cand = dict(item)
        if not cand.get("namespace"):
            if float(cand.get("stealth_score", 0.0) or 0.0) >= 0.8 or cand.get("category") in _ADVISORY_CATEGORIES:
                cand["namespace"] = INTERNAL_ADVISORY_NAMESPACE
            elif "namespace" in item and item["namespace"] in (None, ""):
                cand["namespace"] = ""
            else:
                cand["namespace"] = CLIENT_SAFE_NAMESPACE
        normalized.append(cand)
    return normalized


def _resolve_pack_namespace(canonical_facts: List[Dict[str, Any]], policy_context: PolicyContext) -> str:
    if any(f.get("namespace") == INTERNAL_ADVISORY_NAMESPACE for f in canonical_facts):
        return INTERNAL_ADVISORY_NAMESPACE
    return policy_context.allowed_namespaces[0] if policy_context.allowed_namespaces else CLIENT_SAFE_NAMESPACE


def assemble_decision_evidence_pack(
    inquiry_id: str,
    query_text: str,
    candidates: Sequence[Dict[str, Any]],
    policy_context: PolicyContext,
    limit: int = 10,
) -> EvidencePack:
    """
    Executes pre-retrieval policy filtering and compiles a single composite EvidencePack.
    Enforces Law 6: Zero un-authorized facts enter the evidence portfolio.
    """
    normalized_candidates = _normalize_candidate_namespaces(candidates)
    authorized_candidates = filter_candidates_pre_retrieval(normalized_candidates, policy_context)

    canonical_facts: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for item in authorized_candidates[:limit]:
        node_id, evidence_id = item.get("node_id", ""), item.get("evidence_id", "")
        snippet = item.get("snippet", item.get("text", ""))
        canonical_facts.append({
            "node_id": node_id,
            "evidence_id": evidence_id,
            "name": item.get("name", ""),
            "snippet": snippet,
            "namespace": item.get("namespace", CLIENT_SAFE_NAMESPACE),
            "strategic_value": item.get("strategic_value", 0.5),
            "confidence": item.get("confidence", 0.5),
            "tacticality": item.get("tacticality", 0.5),
            "is_quarantined": item.get("is_quarantined", False),
        })
        if evidence_id:
            citations.append({
                "citation_id": f"cite_{evidence_id}",
                "evidence_id": evidence_id,
                "node_id": node_id,
                "quote": snippet[:160] if snippet else "",
            })

    pack_id = f"pack_{inquiry_id}_{compute_content_digest(query_text).sha256[:8]}"
    return EvidencePack(
        pack_id=pack_id,
        inquiry_id=inquiry_id,
        query_text=query_text,
        canonical_facts=canonical_facts,
        citations=citations,
        namespace=_resolve_pack_namespace(canonical_facts, policy_context),
        tenant_id=policy_context.tenant_id,
        retrieved_at=time.time(),
    )
