"""System instruction, user payload, and governed prompt verification."""

from __future__ import annotations

from typing import Any, Mapping

from dual_pipeline.core import digest
from dual_pipeline.l2_output_contract import (
    SYSTEM_INSTRUCTION_EXTENSION,
    material_citation_obligations,
    record_bound_decision_schema,
    record_evidence_binding_map,
)
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    L2ExecutionDecision,
    ModelPolicy,
    canonical_query_type,
)
from dual_pipeline.provider_request import build_responses_request

from .constants import _PROMPT_SLOT_NAMES, DECISION_MODEL_OUTPUT_SCHEMA
from .types import L2ProviderRequest


L2_STABLE_GOVERNANCE_AND_FEW_SHOT_PREFIX: str = (
    "\n\n### OPERATIONAL GOVERNANCE & DIALECTICAL SYNTHESIS CONTRACT\n"
    "1. Epistemic Posture: The model acts strictly as an epistemic synthesizer over sealed evidence. "
    "It cannot introduce outside real-world claims, extrapolate beyond cited evidence items, or make speculative assertions without grounding.\n"
    "2. Dialectical Triad:\n"
    "   - Thesis: Ground the factual baseline strictly in approved evidence records.\n"
    "   - Antithesis: Actively examine conflicting, candidate, or unverified claims, citing specific evidence IDs for tension or contradiction.\n"
    "   - Synthesis: Reconcile trade-offs, state explicit assumptions regarding missing information or scope boundaries, and formulate calibrated next best actions.\n"
    "3. Citation Pairing Invariant: Every citation must be a 1-to-1 or 1-to-many valid pair matching slot C0M. "
    "A statement must cite at least one record_id and one evidence_id belonging to the declared lane.\n\n"
    "### CANONICAL FEW-SHOT DEMONSTRATION (STATIC REFERENCE)\n"
    "Input Query: 'Evaluate enterprise cloud migration risks and next actions for project Nexus'\n"
    "Sample Compliant Output:\n"
    "{\n"
    '  "schema_version": "nee-governed-answer-1.0.0",\n'
    '  "query_type": "strategic_recommendation",\n'
    '  "current_state": [\n'
    "    {\n"
    '      "statement": "Core telemetry pipeline runs in dual-region active-passive topology with verified SQLite state replication.",\n'
    '      "citations": ["rec-arch-001"],\n'
    '      "evidence_ids": ["evi-arch-001"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "patterns": ["Dual-region active-passive failover with synchronous snapshot replication"],\n'
    '  "pain_points": [\n'
    "    {\n"
    '      "statement": "Database lock contention during peak concurrent batch ingestion exceeds 1500ms.",\n'
    '      "citations": ["rec-perf-002"],\n'
    '      "evidence_ids": ["evi-perf-002"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "opportunities": [\n'
    "    {\n"
    '      "statement": "Migrate batch telemetry commits to WAL-mode SQLite with serialized single-writer queue.",\n'
    '      "citations": ["rec-opt-003"],\n'
    '      "evidence_ids": ["evi-opt-003"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "guidance": [\n'
    "    {\n"
    '      "statement": "Enforce 500ms maximum lock timeout on ingestion transactions before rolling back.",\n'
    '      "citations": ["rec-guide-004"],\n'
    '      "evidence_ids": ["evi-guide-004"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "hypotheses": [\n'
    "    {\n"
    '      "statement": "CANDIDATE: Ingestion queue concurrency may degrade if worker count exceeds core capacity.",\n'
    '      "citations": ["rec-cand-005"],\n'
    '      "evidence_ids": ["evi-cand-005"],\n'
    '      "lane": "candidate"\n'
    "    }\n"
    "  ],\n"
    '  "source_evidence": [\n'
    "    {\n"
    '      "statement": "CANDIDATE_SOURCE_EVIDENCE: Benchmark trace nexus_load_test_20260920.json.",\n'
    '      "citations": ["rec-source-006"],\n'
    '      "evidence_ids": ["evi-source-006"],\n'
    '      "lane": "candidate"\n'
    "    }\n"
    "  ],\n"
    '  "next_best_actions": [\n'
    "    {\n"
    '      "statement": "Deploy SQLite WAL tuning and benchmark p95 commit latency under 20-worker load.",\n'
    '      "citations": ["rec-opt-003"],\n'
    '      "evidence_ids": ["evi-opt-003"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "rationale": "WAL mode decouples readers from writers, resolving the primary contention bottleneck observed in rec-perf-002.",\n'
    '  "counterevidence": [\n'
    "    {\n"
    '      "statement": "Previous test in single-writer mode showed lock contention when checkpointing large transactions.",\n'
    '      "citations": ["rec-counter-007"],\n'
    '      "evidence_ids": ["evi-counter-007"],\n'
    '      "lane": "approved"\n'
    "    }\n"
    "  ],\n"
    '  "missing_information": ["Worker disk I/O IOPS ceilings in staging environment"],\n'
    '  "assumptions": ["Active governed snapshot represents production configuration; worker hardware conforms to 8-core baseline."],\n'
    '  "what_would_change_the_conclusion": ["If disk fsync latency exceeds 10ms, WAL checkpointing will become the new bottleneck."],\n'
    '  "proposed_assertions": [],\n'
    '  "automatic_graph_mutation_count": 0\n'
    "}\n"
)


def l2_system_instruction(purpose: CallPurpose = CallPurpose.PRIMARY) -> str:
    """Return the governed query-L2 instruction without provider state.

    The primary system prompt prefix is invariant across all CallPurposes to maximize
    provider prompt caching efficiency. Purpose-specific repair instructions are
    situated in the user payload.
    """
    return (
        "Return only the governed structured output. Use only the supplied sealed "
        "graph-derived evidence; do not browse, retrieve, call tools, mutate the graph, "
        "or introduce unsupported facts. Do not expose hidden reasoning. Preserve "
        "approved, candidate, and external-public evidence lanes and cite only supplied "
        "record and evidence identifiers."
        + SYSTEM_INSTRUCTION_EXTENSION
        + L2_STABLE_GOVERNANCE_AND_FEW_SHOT_PREFIX
    )


def l2_user_payload(request: L2ProviderRequest) -> dict[str, Any]:
    """Return the minimized user payload for one governed call purpose."""

    purpose_instructions = {
        CallPurpose.SCHEMA_REPAIR: (
            "Repair only the listed serialization, required-field, enum, or "
            "citation-wiring defects. Introduce no new factual claims."
        ),
        CallPurpose.QUALITY_ESCALATION: (
            "Address every typed quality deficiency without adding evidence."
        ),
        CallPurpose.CONTENT_REPAIR: (
            "Revise only the supplied answer to resolve the listed client-quality "
            "defects. Preserve the answer's governed posture and do not introduce "
            "facts, citations, evidence, or claims outside the sealed packet."
        ),
    }

    if request.purpose is CallPurpose.SCHEMA_REPAIR:
        return {
            "purpose": request.purpose.value,
            "purpose_instruction": purpose_instructions.get(request.purpose, ""),
            "original_response": request.original_response,
            "validation_errors": list(request.validation_errors),
        }
    value: dict[str, Any] = {
        "purpose": request.purpose.value,
        "question": request.question,
        "query_plan": request.plan,
        "governed_prompt": request.prompt,
    }
    if request.purpose in purpose_instructions:
        value["purpose_instruction"] = purpose_instructions[request.purpose]
    if request.purpose in {CallPurpose.QUALITY_ESCALATION, CallPurpose.CONTENT_REPAIR}:
        value["quality_deficiencies"] = list(request.quality_deficiencies)
    if request.purpose is CallPurpose.CONTENT_REPAIR:
        value["original_response"] = request.original_response
    return value


def provider_quality_requirements(
    *,
    plan: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Project deterministic semantic gates into the provider-visible prompt."""

    approved_record_ids = [
        str(item["record"]["record_id"])
        for item in evidence_packet.get("approved", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("record"), Mapping)
        and isinstance(item["record"].get("record_id"), str)
    ]
    candidate_hypothesis_required = bool(
        plan.get("retrieval_mode") == "candidate_review"
        and any(
            isinstance(item, Mapping)
            and isinstance(item.get("retrieval_metadata"), Mapping)
            and item["retrieval_metadata"].get("answer_use")
            == "candidate_hypothesis"
            for item in evidence_packet.get("candidate", [])
        )
    )
    challenger_active = bool(
        plan.get("challenger_required", False)
        or plan.get("assurance_profile") == "enhanced"
    )
    requirements: dict[str, Any] = {
        "required_approved_citation_ids": approved_record_ids[:2],
        "candidate_hypothesis_required": candidate_hypothesis_required,
        "candidate_text_prefix": "CANDIDATE:",
        "quote_pointer_extraction_format": "[start_idx, end_idx]",
    }
    if challenger_active:
        requirements["dialectical_reasoning_required"] = True
        requirements["dialectical_structure"] = (
            "thesis_grounded_in_approved_facts -> "
            "antithesis_stress_tested_against_counterevidence_and_candidates -> "
            "synthesis_reconciling_conditions_and_assumptions"
        )
    return requirements


def build_l2_provider_body(
    *,
    config: L2Config,
    model_policy: ModelPolicy,
    request: L2ProviderRequest,
    use_static_schema: bool = True,
) -> dict[str, Any]:
    """Build the exact query-L2 request before secret resolution or network access."""
    if use_static_schema:
        schema = DECISION_MODEL_OUTPUT_SCHEMA
    else:
        schema = record_bound_decision_schema(
            DECISION_MODEL_OUTPUT_SCHEMA,
            request.evidence_packet,
            expected_query_type=(
                str(request.plan["query_type"])
                if request.plan.get("query_type") is not None
                else None
            ),
            canonicalize_query_type=canonical_query_type,
        )
    cache_key = None
    if getattr(config, "prompt_cache_enabled", True):
        from dual_pipeline.prompt_cache import derive_cache_key

        prompt_dict = request.prompt if isinstance(request.prompt, Mapping) else {}
        cache_key = derive_cache_key(
            workflow="l2_decision",
            model=model_policy.model,
            static_prefix=l2_system_instruction(request.purpose),
            template_version=str(prompt_dict.get("template_version", "v1")),
        )

    return build_responses_request(
        model=model_policy.model,
        system_instruction=l2_system_instruction(request.purpose),
        user_payload=l2_user_payload(request),
        reasoning_effort=model_policy.reasoning_effort,
        schema_name="nee_decision_model_output",
        output_schema=schema,
        maximum_output_tokens=config.output.maximum_output_tokens,
        prompt_cache_key=cache_key,
    )


def _prompt_evidence_rows(
    evidence_packet: Mapping[str, Any], lane: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    values = evidence_packet.get(lane, [])
    if not isinstance(values, list):
        raise ValueError(f"sealed evidence lane {lane} must be an array")
    for item in values:
        if not isinstance(item, Mapping) or not isinstance(item.get("record"), Mapping):
            raise ValueError(f"sealed evidence lane {lane} contains an invalid record")
        record = item["record"]
        rows.append(
            {
                "lane": lane,
                "record_id": record.get("record_id"),
                "title": record.get("title"),
                "retrieval_text": record.get("retrieval_text"),
                "evidence_ids": record.get("evidence_ids"),
                "answer_use": (
                    item.get("retrieval_metadata", {}).get("answer_use")
                    if isinstance(item.get("retrieval_metadata"), Mapping)
                    else None
                ),
                "strategic_profile": record.get("strategic_profile"),
            }
        )
    return rows


def _verify_governed_prompt(
    *,
    prompt: Mapping[str, Any],
    question: str,
    plan: Mapping[str, Any],
    decision: L2ExecutionDecision,
    evidence_packet: Mapping[str, Any],
    evidence_packet_hash: str,
) -> None:
    expected_keys = {
        "template_version",
        "slots",
        "slot_digests",
        "evidence_packet_hash",
        "prompt_hash",
    }
    if set(prompt) != expected_keys:
        raise ValueError("governed prompt has an invalid top-level shape")
    slots = prompt.get("slots")
    slot_digests = prompt.get("slot_digests")
    if not isinstance(slots, Mapping) or set(slots) != _PROMPT_SLOT_NAMES:
        raise ValueError("governed prompt must contain the exact ten-slot contract")
    if not isinstance(slot_digests, Mapping) or set(slot_digests) != _PROMPT_SLOT_NAMES:
        raise ValueError("governed prompt slot digests are incomplete")
    expected_slots: dict[str, Any] = {
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
            "query_type": decision.query_type,
            "route": decision.route,
            "materiality": decision.materiality,
            "retrieval_mode": plan.get("retrieval_mode"),
        },
        "I0": plan.get("subquestions"),
        "C0A": _prompt_evidence_rows(evidence_packet, "approved"),
        "C0C": _prompt_evidence_rows(evidence_packet, "candidate"),
        "C0W": _prompt_evidence_rows(evidence_packet, "external_public"),
        "C0M": record_evidence_binding_map(evidence_packet),
        "G0": {
            "graph_paths": evidence_packet.get("graph_paths"),
            "counterevidence": evidence_packet.get("counterevidence"),
            "gaps": evidence_packet.get("gaps"),
            "exclusions": evidence_packet.get("exclusions"),
            "required_material_record_ids_by_lane": evidence_packet.get(
                "required_material_record_ids_by_lane",
                {"approved": [], "candidate": [], "external_public": []},
            ),
            "material_citation_obligations": material_citation_obligations(
                evidence_packet,
                query_type=decision.query_type,
            ),
            "exact_anchor_ids": evidence_packet.get("exact_anchor_ids"),
            "bm25_anchor_ids": evidence_packet.get("bm25_anchor_ids", []),
            "graph_snapshot": evidence_packet.get("graph_snapshot"),
            "connected_evidence_map": evidence_packet.get("connected_evidence_map"),
            "strategic_context": evidence_packet.get("strategic_context"),
            **({"workstream_roster_and_roles": evidence_packet["workstream_roster_and_roles"]} if "workstream_roster_and_roles" in evidence_packet else {}),
        },
        "U0": question,
        "R0": {
            "output_schema": plan.get("required_output_schema"),
            "route_output_schema": plan.get("output_schema"),
            "citation_required": True,
            "candidate_label_required": plan.get("retrieval_mode")
            in {"candidate_review", "strategic"},
            "source_evidence_label_required": plan.get("retrieval_mode")
            == "candidate_review",
            "guidance_label_required": plan.get("retrieval_mode")
            in {"guidance", "strategic"},
            "challenger_required": plan.get("challenger_required", False)
            or plan.get("assurance_profile") == "enhanced",
            **provider_quality_requirements(
                plan=plan,
                evidence_packet=evidence_packet,
            ),
        },
    }
    if dict(slots) != expected_slots:
        raise ValueError("governed prompt is not a complete projection of the sealed packet")
    expected_slot_digests = {name: digest(value) for name, value in expected_slots.items()}
    if dict(slot_digests) != expected_slot_digests:
        raise ValueError("governed prompt slot digest mismatch")
    if prompt.get("template_version") != plan.get("prompt_blueprint"):
        raise ValueError("governed prompt template is not bound to the L1 plan")
    if prompt.get("evidence_packet_hash") != evidence_packet_hash:
        raise ValueError("governed prompt is not bound to the sealed evidence packet")
    actual_prompt_hash = digest(
        {key: value for key, value in prompt.items() if key != "prompt_hash"}
    )
    if prompt.get("prompt_hash") != actual_prompt_hash:
        raise ValueError("governed prompt hash does not match its content")
