"""Prompt Assembly (PA) stage and CompiledPromptArtifact.

Standalone, testable stage that transforms retrieved evidence, plan, and route
into a structured, slot-based prompt for L2 reasoning, and seals the result
as a contract-valid CompiledPromptArtifact.
"""
from __future__ import annotations

from typing import Any, Mapping

from .core import (
    digest,
    seal_artifact,
    sha256_bytes,
    stable_id,
    utc_now,
)
from .l2_output_contract import (
    material_citation_obligations,
    record_evidence_binding_map,
)
from .l2_providers import provider_quality_requirements
from .model_registry import ModelTier, resolve_tier_model
from .strategic_reasoning_core.quantitative_ontology import extract_audience_stakeholders

COMPILED_PROMPT_CONTRACT_ID = "urn:nee:dual-pipeline:compiled-prompt-artifact:1.0.0"
SCHEMA_VERSION = "1.0.0"

VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "max"})
VALID_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})


def assemble_prompt(
    question: str,
    plan: dict[str, Any],
    route: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a slot-based prompt dictionary for L2 reasoning.

    Preserves exact canonical slot definitions and hashing.
    """
    def evidence_rows(values: list[dict[str, Any]], lane: str) -> list[dict[str, Any]]:
        return [
            {
                "lane": lane,
                "record_id": item["record"]["record_id"],
                "title": item["record"]["title"],
                "retrieval_text": item["record"]["retrieval_text"],
                "evidence_ids": item["record"]["evidence_ids"],
                "answer_use": item["retrieval_metadata"]["answer_use"],
                "strategic_profile": item["record"].get("strategic_profile"),
            }
            for item in values
        ]

    slots: dict[str, Any] = {
        "S0": (
            "Use governed evidence only. Factual statements may cite approved material only; "
            "strategic context and decision constraints may support recommendations but "
            "cannot be restated as current-state fact; official intent is not realized "
            "outcome evidence; external patterns are analogies, not NEE implementation; "
            "candidate material must remain an explicitly labeled hypothesis; guidance "
            "records must be emitted only as labeled GUIDANCE_NOT_FACT; citation-only "
            "candidate source excerpts must be emitted only as labeled "
            "CANDIDATE_SOURCE_EVIDENCE."
        ),
        "D0": {
            "query_type": plan["query_type"],
            "route": route["query_route"],
            "materiality": plan["materiality"],
            "retrieval_mode": plan["retrieval_mode"],
        },
        "I0": plan["subquestions"],
        "C0A": evidence_rows(packet["approved"], "approved"),
        "C0C": evidence_rows(packet["candidate"], "candidate"),
        "C0W": evidence_rows(packet["external_public"], "external_public"),
        "C0M": record_evidence_binding_map(packet),
        "G0": {
            "graph_paths": packet["graph_paths"],
            "counterevidence": packet["counterevidence"],
            "gaps": packet["gaps"],
            "exclusions": packet["exclusions"],
            "required_material_record_ids_by_lane": packet.get(
                "required_material_record_ids_by_lane", {"approved": [], "candidate": [], "external_public": []}
            ),
            "material_citation_obligations": material_citation_obligations(packet, query_type=plan["query_type"]),
            "exact_anchor_ids": packet["exact_anchor_ids"],
            "bm25_anchor_ids": packet.get("bm25_anchor_ids", []),
            "graph_snapshot": packet["graph_snapshot"],
            "connected_evidence_map": packet.get("connected_evidence_map"),
            "strategic_context": packet.get("strategic_context"),
            **({"workstream_roster_and_roles": packet["workstream_roster_and_roles"]} if "workstream_roster_and_roles" in packet else {}),
        },
        "U0": question,
        "R0": {
            "output_schema": plan["required_output_schema"],
            "route_output_schema": plan["output_schema"],
            "citation_required": True,
            "candidate_label_required": plan["retrieval_mode"]
            in {"candidate_review", "strategic"},
            "source_evidence_label_required": plan["retrieval_mode"]
            == "candidate_review",
            "guidance_label_required": plan["retrieval_mode"]
            in {"guidance", "strategic"},
            "challenger_required": plan.get("challenger_required", False)
            or plan.get("assurance_profile") == "enhanced",
            **provider_quality_requirements(
                plan=plan,
                evidence_packet=packet,
            ),
        },
    }
    slot_digests = {name: digest(value) for name, value in slots.items()}
    prompt = {
        "template_version": plan["prompt_blueprint"],
        "slots": slots,
        "slot_digests": slot_digests,
        "evidence_packet_hash": packet["packet_digest"],
    }
    prompt["prompt_hash"] = digest(prompt)
    return prompt


def compile_prompt_artifact(
    *,
    query_id: str,
    run_id: str,
    evidence_contract: Mapping[str, Any],
    l2_execution_decision_id: str,
    compiled_prompt: Mapping[str, Any],
    question: str,
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    target_model: str | None = None,
    reasoning_effort: str | None = None,
    materiality: str | None = None,
    assurance_profile: str | None = None,
    classification: str = "internal",
    audiences: list[str] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile and seal a CompiledPromptArtifact between PA and L2 reasoning.

    Conforms to ``urn:nee:dual-pipeline:compiled-prompt-artifact:1.0.0``.
    """
    if target_model is None or reasoning_effort is None:
        def_m, def_e = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        target_model = target_model or def_m
        reasoning_effort = reasoning_effort or def_e or "high"
    created_at = utc_now()
    artifact_id = stable_id("compiled-prompt", run_id, query_id, created_at)

    evidence_records = evidence_contract.get("evidence_records", [])
    must_use_count = sum(1 for r in evidence_records if isinstance(r, Mapping) and r.get("stratum") == "MUST_USE")
    supporting_count = sum(1 for r in evidence_records if isinstance(r, Mapping) and r.get("stratum") == "SUPPORTING")
    contradicting_count = sum(1 for r in evidence_records if isinstance(r, Mapping) and r.get("stratum") == "CONTRADICTS")
    background_count = sum(1 for r in evidence_records if isinstance(r, Mapping) and r.get("stratum") == "BACKGROUND")

    # Estimate evidence tokens (roughly 1 token per 4 chars of retrieval text)
    total_chars = sum(len(str(r.get("retrieval_text", ""))) for r in evidence_records if isinstance(r, Mapping))
    total_evidence_tokens = max(100, total_chars // 4)

    slots = compiled_prompt.get("slots", {}) if isinstance(compiled_prompt.get("slots"), Mapping) else {}
    s0_constraint = slots.get("S0")
    epistemic_constraints = [str(s0_constraint)] if s0_constraint else ["Use governed evidence only."]

    coverage_summary = evidence_contract.get("coverage_summary", {})
    known_gaps = (
        coverage_summary.get("gap_descriptions", [])
        if isinstance(coverage_summary, Mapping) and isinstance(coverage_summary.get("gap_descriptions"), list)
        else []
    )

    plan_digest = plan.get("plan_digest")
    if not isinstance(plan_digest, str) or len(plan_digest) != 64:
        plan_digest = digest(dict(plan))

    required_schema = plan.get("required_output_schema")
    output_schema_digest = digest(required_schema) if required_schema else sha256_bytes(b"{}")

    prompt_components: dict[str, Any] = {
        "question": question,
        "question_digest": sha256_bytes(question.encode("utf-8")),
        "l1_plan_digest": plan_digest,
        "route": route.get("query_route") or "CURRENT_STATE_CORROBORATION",
        "must_use_evidence_count": must_use_count,
        "supporting_evidence_count": supporting_count,
        "contradicting_evidence_count": contradicting_count,
        "background_evidence_count": background_count,
        "total_evidence_tokens_estimate": total_evidence_tokens,
        "epistemic_constraints": epistemic_constraints,
        "applicable_policies": ["lane-separation-v1"],
        "known_gaps": [str(g) for g in known_gaps],
    }

    evidence_contract_id = evidence_contract.get("artifact_id") or "final-evidence-contract-unknown"
    raw_evidence_digest = evidence_contract.get("canonical_digest")
    if isinstance(raw_evidence_digest, Mapping) and "value" in raw_evidence_digest:
        evidence_contract_digest = str(raw_evidence_digest["value"])
    elif isinstance(raw_evidence_digest, str):
        evidence_contract_digest = raw_evidence_digest
    else:
        evidence_contract_digest = digest(dict(evidence_contract))

    body: dict[str, Any] = {
        "contract_id": COMPILED_PROMPT_CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "query_id": query_id,
        "run_id": run_id,
        "evidence_contract_id": evidence_contract_id,
        "evidence_contract_digest": evidence_contract_digest,
        "l2_execution_decision_id": l2_execution_decision_id,
        "prompt_components": prompt_components,
        "target_model": target_model,
        "reasoning_effort": reasoning_effort if reasoning_effort in VALID_REASONING_EFFORTS else "high",
        "output_schema_digest": output_schema_digest,
        "classification": classification if classification in VALID_CLASSIFICATIONS else "internal",
        "audiences": audiences or extract_audience_stakeholders(question) or ["nee-engineering"],
        "created_at": created_at,
        "extensions": dict(extensions) if extensions else {},
    }

    if materiality in {"low", "medium", "high"}:
        body["materiality"] = materiality
    if assurance_profile in {"standard", "enhanced"}:
        body["assurance_profile"] = assurance_profile

    return seal_artifact(body)
