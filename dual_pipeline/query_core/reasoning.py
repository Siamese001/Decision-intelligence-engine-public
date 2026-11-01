"""Prompt assembly, reasoning over evidence packets, answer validation, and X1 gate evaluation."""

from __future__ import annotations

import re
from typing import Any

from dual_pipeline.core import digest
from dual_pipeline.index import retrieval_metadata_for_record
from dual_pipeline.prompt_assembly import assemble_prompt as _assemble_prompt_impl
from dual_pipeline.semantic_cache import SemanticCacheLookup
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation

from .coverage import coverage_complete
from .dynamic_entities import select_effective_factual_records
from .thought_leadership import (
    build_governed_current_state,
    extract_audience_stakeholders,
    synthesize_next_best_actions as _synthesize_next_best_actions,
)


def assemble_prompt(question: str, plan: dict[str, Any], route: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    return _assemble_prompt_impl(question, plan, route, packet)


def _record_tokens(item: dict[str, Any]) -> set[str]:
    record = item["record"]
    return set(re.findall(r"[a-z0-9]+", f"{record['title']} {record['retrieval_text']}".casefold()))


def _record_answer_use(item: dict[str, Any]) -> str:
    metadata = item.get("retrieval_metadata", {})
    if isinstance(metadata, dict) and isinstance(metadata.get("answer_use"), str):
        return metadata["answer_use"]
    record = item["record"]
    return retrieval_metadata_for_record(record["record_type"], record["lane"])["answer_use"]


def _item_matches_terms_or_preds(
    item: dict[str, Any], terms: tuple[str, ...], preds: tuple[str, ...]
) -> bool:
    text = f"{item['record']['title']} {item['record']['retrieval_text']}"
    if any(term in text.casefold() for term in terms):
        return True
    gpath = item.get("graph_path") if isinstance(item.get("graph_path"), dict) else {}
    if str(gpath.get("predicate") or "").upper() in preds:
        return True
    upper_text = text.upper()
    return any(p in upper_text for p in preds)


def _select_opportunities_and_pain(
    recommendation_basis: list[dict[str, Any]], query_type: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_opp_terms = (
        "opportun", "pdlc", "enable", "solution", "pattern", "self-service",
        "intent", "outcome", "architect",
    )
    base_pain_terms = (
        "risk", "gap", "pain", "constraint", "problem", "inconsisten",
        "bottleneck", "barrier", "depend", "require", "mitigat", "opacity",
    )
    if query_type not in {"opportunity_nba", "general_query"}:
        opportunities = [
            it for it in recommendation_basis if _item_matches_terms_or_preds(it, base_opp_terms, ())
        ][:6] or recommendation_basis[:6]
        pain_points = [
            it for it in recommendation_basis
            if it["record"]["lane"] == "approved" and _item_matches_terms_or_preds(it, base_pain_terms, ())
        ][:6]
        return opportunities, pain_points

    opp_terms = base_opp_terms + (
        "strategic frame", "strategic stance", "evaluated options", "trade-offs",
        "tradeoffs", "tariff", "augmentation", "bess", "ppa", "ferc", "fpsc",
        "spog", "recommend", "action", "deliver", "partner", "google cloud", "hyperscale",
    )
    pain_terms = base_pain_terms + (
        "degrad", "outage", "delay", "latency", "friction", "compliance",
        "backlog", "tariff", "ferc", "fpsc",
    )
    opp_preds = ("DELIVERS", "CONTRIBUTES_TO", "OWNS_OR_LEADS", "USES_PATTERN", "VALIDATES")
    pain_preds = ("HAS_RISK", "CONSTRAINS", "DEPENDS_ON", "CONTRADICTS")
    opportunities = [
        it for it in recommendation_basis if _item_matches_terms_or_preds(it, opp_terms, opp_preds)
    ][:6] or recommendation_basis[:6]
    pain_points = [
        it for it in recommendation_basis
        if it["record"]["lane"] == "approved" and (_record_answer_use(it) == "decision_constraint" or _item_matches_terms_or_preds(it, pain_terms, pain_preds))
    ][:6]
    if not pain_points:
        pain_points = [it for it in recommendation_basis if it["record"]["lane"] == "approved"][:4]
    return opportunities, pain_points


def reason_over_packet(plan: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    approved = packet["approved"]
    is_factual = plan.get("retrieval_mode", "factual") == "factual" or "candidate" not in plan.get("allowed_lanes", ["approved", "candidate"])
    candidates = [] if is_factual else packet["candidate"]

    factual_approved = [item for item in approved if _record_answer_use(item) == "factual_answer"]
    guidance_records = [item for item in approved if _record_answer_use(item) == "guidance"]
    candidate_hypothesis_records = [item for item in candidates if _record_answer_use(item) == "candidate_hypothesis"]
    candidate_source_records = [item for item in candidates if _record_answer_use(item) == "citation_only"]
    recommendation_uses = {"factual_answer", "strategic_context", "decision_constraint", "external_analogy"}
    recommendation_basis = (
        [item for item in approved + packet.get("external_public", []) if _record_answer_use(item) in recommendation_uses]
        if plan["query_type"] in {"opportunity_nba", "general_query"}
        else factual_approved
    )
    opportunities, pain_points = _select_opportunities_and_pain(recommendation_basis, plan["query_type"])
    primary_entities = plan.get("primary_entities", [])
    question_str = str(plan.get("question") or "")
    effective_factual = select_effective_factual_records(
        factual_approved,
        primary_entities,
        max_per_entity=3,
        packet=packet,
        audience_entities=extract_audience_stakeholders(question_str),
    )
    current_state, ordered_factual = build_governed_current_state(
        factual_approved, effective_factual, packet
    )
    pain_rows = [
        {
            "pain_point": item["record"]["title"],
            "citations": [item["record"]["record_id"]],
            "evidence_ids": item["record"]["evidence_ids"],
            "lane": "approved",
        }
        for item in pain_points[:4]
    ]
    opportunity_rows: list[dict[str, Any]] = []
    for item in opportunities:
        opportunity_tokens = _record_tokens(item)
        ranked_pain = sorted(
            [pain for pain in pain_points if pain["record"]["record_id"] != item["record"]["record_id"]],
            key=lambda pain: (-len(opportunity_tokens & _record_tokens(pain)), pain["record"]["record_id"]),
        )
        selected_pain = ranked_pain[:2]
        if not selected_pain:
            continue
        opportunity_rows.append(
            {
                "opportunity": item["record"]["title"],
                "why_prioritize": item["record"]["retrieval_text"],
                "addresses": [value["record"]["title"] for value in selected_pain],
                "citations": [item["record"]["record_id"]] + [value["record"]["record_id"] for value in selected_pain],
                "evidence_ids": list(
                    dict.fromkeys(
                        item["record"]["evidence_ids"]
                        + [eid for value in selected_pain for eid in value["record"]["evidence_ids"]]
                    )
                ),
                "lane": item["record"]["lane"],
            }
        )
    query_entity_tokens = {
        tok for ent in primary_entities for tok in re.findall(r"[a-z0-9]+", str(ent).casefold())
    }
    ranked_candidates = sorted(
        candidate_hypothesis_records,
        key=lambda it: (
            -len(_record_tokens(it) & query_entity_tokens) if query_entity_tokens else 0,
            it["record"]["record_id"],
        ),
    )
    hypotheses = [
        {
            "hypothesis": f"CANDIDATE: {item['record']['retrieval_text']}",
            "citations": [item["record"]["record_id"]],
            "evidence_ids": item["record"]["evidence_ids"],
            "lane": "candidate",
        }
        for item in ranked_candidates[:10]
    ]
    source_evidence = [
        {
            "label": "CANDIDATE_SOURCE_EVIDENCE",
            "excerpt": item["record"]["retrieval_text"],
            "citations": [item["record"]["record_id"]],
            "evidence_ids": item["record"]["evidence_ids"],
            "lane": "candidate",
        }
        for item in candidate_source_records[:8]
    ]
    guidance = [
        {
            "label": "GUIDANCE_NOT_FACT",
            "guidance": item["record"]["retrieval_text"],
            "citations": [item["record"]["record_id"]],
            "evidence_ids": item["record"]["evidence_ids"],
            "lane": "approved",
        }
        for item in guidance_records[:5]
    ]
    next_best_actions = _synthesize_next_best_actions(
        plan["query_type"], opportunity_rows, recommendation_basis, question=question_str
    )
    return {
        "schema_version": "nee-governed-answer-1.0.0",
        "query_type": plan["query_type"],
        "current_state": current_state,
        "patterns": [value["opportunity"] for value in opportunity_rows[:3]]
        or [item["record"]["title"] for item in ordered_factual[:3]],
        "pain_points": pain_rows,
        "opportunities": opportunity_rows,
        "guidance": guidance,
        "hypotheses": hypotheses,
        "source_evidence": source_evidence,
        "next_best_actions": next_best_actions,
        "rationale": (
            "Prioritization separates approved current-state facts from official "
            "intent, design constraints, source positions, and external analogies. "
            "Those strategic roles may support recommendations without being "
            "converted into realized facts; candidates remain labeled hypotheses."
            if plan["query_type"] == "opportunity_nba"
            else "Prioritization uses approved factual evidence, governed graph "
            "paths, and evidence locators; approved guidance is emitted only as "
            "labeled GUIDANCE_NOT_FACT, and candidates remain separated as hypotheses."
        ),
        "counterevidence": packet["counterevidence"],
        "missing_information": packet["gaps"],
        "assumptions": [
            "The active graph/index pair is the governing current snapshot.",
            "Retrieval rank is relevance evidence, not independent authority.",
        ],
        "what_would_change_the_conclusion": [
            "New authoritative current-state evidence",
            "A resolved contradiction or ownership change",
            "A governed candidate-to-approved promotion",
        ],
        "proposed_assertions": [],
        "automatic_graph_mutation_count": 0,
    }


def evidence_only_over_packet(
    plan: dict[str, Any],
    packet: dict[str, Any],
    *,
    reason_code: str,
) -> dict[str, Any]:
    """Return a citation-complete, non-synthesized projection of sealed evidence.

    This is deliberately narrower than ``reason_over_packet``.  It never
    ranks, recommends, maps opportunities to risks, or creates an action.  It
    is used only when the query route requested synthesis but provider egress
    was not authorized, so the caller gets useful graph-derived evidence
    without mistaking it for a completed synthesis.
    """

    def answer_use(item: dict[str, Any]) -> str:
        metadata = item.get("retrieval_metadata", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("answer_use"), str):
            return metadata["answer_use"]
        record = item["record"]
        return retrieval_metadata_for_record(record["record_type"], record["lane"])["answer_use"]

    factual = [
        item
        for item in packet.get("approved", [])
        if answer_use(item) == "factual_answer"
    ]
    guidance_records = [
        item
        for item in packet.get("approved", [])
        if answer_use(item) == "guidance"
    ]
    is_factual = plan.get("retrieval_mode", "factual") == "factual" or "candidate" not in plan.get("allowed_lanes", ["approved", "candidate"])
    candidates = [] if is_factual else list(packet.get("candidate", []))
    candidate_hypothesis_records = [
        item for item in candidates if answer_use(item) == "candidate_hypothesis"
    ]
    candidate_source_records = [
        item for item in candidates if answer_use(item) == "citation_only"
    ]
    primary_entities = plan.get("primary_entities", [])
    effective_factual = select_effective_factual_records(
        factual, primary_entities, max_per_entity=4
    )
    all_entity_tokens = {
        t for ent in primary_entities
        for t in re.findall(r"[a-z0-9]+", ent.casefold())
        if t not in {"the", "and", "or", "in", "on", "at", "to", "for", "of"}
    }
    ranked_candidates = sorted(
        candidate_hypothesis_records,
        key=lambda it: (-len(_record_tokens(it) & all_entity_tokens) if all_entity_tokens else 0, it["record"]["record_id"]),
    )
    return {
        "schema_version": "nee-governed-answer-1.0.0",
        "query_type": plan["query_type"],
        "current_state": [
            {
                "statement": item["record"]["retrieval_text"],
                "citations": [item["record"]["record_id"]],
                "evidence_ids": item["record"]["evidence_ids"],
                "lane": "approved",
            }
            for item in effective_factual[:8]
        ],
        "patterns": [],
        "pain_points": [],
        "opportunities": [],
        "guidance": [
            {
                "label": "GUIDANCE_NOT_FACT",
                "guidance": item["record"]["retrieval_text"],
                "citations": [item["record"]["record_id"]],
                "evidence_ids": item["record"]["evidence_ids"],
                "lane": "approved",
            }
            for item in guidance_records[:8]
        ],
        "hypotheses": [
            {
                "hypothesis": f"CANDIDATE: {item['record']['retrieval_text']}",
                "citations": [item["record"]["record_id"]],
                "evidence_ids": item["record"]["evidence_ids"],
                "lane": "candidate",
            }
            for item in ranked_candidates[:10]
        ],
        "source_evidence": [
            {
                "label": "CANDIDATE_SOURCE_EVIDENCE",
                "excerpt": item["record"]["retrieval_text"],
                "citations": [item["record"]["record_id"]],
                "evidence_ids": item["record"]["evidence_ids"],
                "lane": "candidate",
            }
            for item in candidate_source_records[:8]
        ],
        "next_best_actions": [],
        "rationale": (
            "Evidence-only response. External synthesis was not run because "
            f"{reason_code}. The response reproduces governed retrieval records "
            "with their citations and makes no derived recommendation or ranking."
        ),
        "counterevidence": [],
        "missing_information": list(packet.get("gaps", []))
        + [
            "Synthesis is unavailable for this run; request it again when governed "
            "provider egress is authorized."
        ],
        "assumptions": [
            "The active graph/index pair is the governing current snapshot.",
            "This response is a direct projection of sealed graph evidence, not a synthesis.",
        ],
        "what_would_change_the_conclusion": [
            "Authorized grounded synthesis over this same sealed evidence packet",
            "New authoritative current-state evidence",
        ],
        "proposed_assertions": [],
        "automatic_graph_mutation_count": 0,
    }


def validate_answer(answer: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    strategic = answer.get("query_type") == "opportunity_nba"
    record_lane = {
        item["record"]["record_id"]: lane
        for lane in ("approved", "candidate", "external_public")
        for item in packet[lane]
    }
    record_answer_use = {
        item["record"]["record_id"]: (
            item.get("retrieval_metadata", {}).get("answer_use")
            or retrieval_metadata_for_record(
                item["record"]["record_type"], item["record"]["lane"]
            )["answer_use"]
        )
        for lane in ("approved", "candidate", "external_public")
        for item in packet[lane]
    }
    evidence_records: dict[str, set[str]] = {}
    for lane in ("approved", "candidate", "external_public"):
        for item in packet[lane]:
            record_id = item["record"]["record_id"]
            for evidence_id in item["record"].get("evidence_ids", []):
                evidence_records.setdefault(evidence_id, set()).add(record_id)
    citations: list[str] = []
    evidence_ids: list[str] = []
    evidence_row_failures: list[str] = []
    row_fields = (
        "current_state",
        "pain_points",
        "opportunities",
        "guidance",
        "hypotheses",
        "source_evidence",
        "counterevidence",
        "proposed_assertions",
    )
    strategic_recommendation_uses = {
        "factual_answer",
        "strategic_context",
        "decision_constraint",
        "external_analogy",
    }
    expected_answer_use = {
        "current_state": {"factual_answer"},
        "pain_points": (
            strategic_recommendation_uses - {"external_analogy"}
            if strategic
            else {"factual_answer", "decision_constraint"}
        ),
        "opportunities": (
            strategic_recommendation_uses if strategic else {"factual_answer", "decision_constraint"}
        ),
        "guidance": {"guidance"},
        "hypotheses": {"candidate_hypothesis"},
        "source_evidence": {"citation_only"},
        "counterevidence": {"factual_answer"},
        "proposed_assertions": {"candidate_hypothesis"},
    }
    answer_use_failures: list[str] = []
    for field in row_fields:
        for index, item in enumerate(answer.get(field, [])):
            row_citations = item.get("citations", [])
            row_evidence_ids = item.get("evidence_ids", [])
            if not row_citations or not row_evidence_ids:
                evidence_row_failures.append(f"{field}[{index}]")
            citations.extend(row_citations)
            evidence_ids.extend(row_evidence_ids)
            if any(not (evidence_records.get(evidence_id, set()) & set(row_citations)) for evidence_id in row_evidence_ids):
                evidence_row_failures.append(f"{field}[{index}]")
            if any(
                citation not in {record_id for evidence_id in row_evidence_ids for record_id in evidence_records.get(evidence_id, set())}
                for citation in row_citations
            ):
                evidence_row_failures.append(f"{field}[{index}]")
            if any(record_answer_use.get(citation) not in expected_answer_use[field] for citation in row_citations):
                answer_use_failures.append(f"{field}[{index}]")
    action_lane_failures: list[str] = []
    for index, item in enumerate(answer["next_best_actions"]):
        basis = item.get("basis_citations", [])
        citations.extend(basis)
        permitted_action_lanes = {"approved", "external_public"} if strategic else {"approved"}
        permitted_action_uses = strategic_recommendation_uses if strategic else {"factual_answer", "decision_constraint"}
        if not basis or any(
            record_lane.get(citation) not in permitted_action_lanes or record_answer_use.get(citation) not in permitted_action_uses
            for citation in basis
        ):
            action_lane_failures.append(f"next_best_actions[{index}]")
    unresolved = sorted(set(citations) - set(record_lane))
    unresolved_evidence = sorted(set(evidence_ids) - set(evidence_records))
    candidate_label_failures = [
        item["citations"][0] for item in answer.get("hypotheses", [])
        if not item["hypothesis"].startswith("CANDIDATE:") or item["lane"] != "candidate"
    ]
    source_evidence_label_failures = [
        (item.get("citations") or ["source_evidence"])[0] for item in answer.get("source_evidence", [])
        if item.get("label") != "CANDIDATE_SOURCE_EVIDENCE" or item.get("lane") != "candidate"
    ]
    guidance_label_failures = [
        (item.get("citations") or ["guidance"])[0] for item in answer.get("guidance", [])
        if item.get("label") != "GUIDANCE_NOT_FACT" or item.get("lane") != "approved"
    ]
    approved_lane_failures = [
        citation for field in ("current_state", "pain_points", "counterevidence")
        for item in answer.get(field, []) for citation in item.get("citations", [])
        if record_lane.get(citation) != "approved"
    ] + [
        citation for item in answer.get("opportunities", []) for citation in item.get("citations", [])
        if record_lane.get(citation) not in ({"approved", "external_public"} if strategic else {"approved"})
    ] + action_lane_failures
    checks = {
        "schema_complete": all(
            key in answer for key in (
                "current_state", "patterns", "pain_points", "opportunities", "guidance", "hypotheses",
                "next_best_actions", "rationale", "counterevidence", "missing_information", "assumptions",
                "what_would_change_the_conclusion",
            )
        ),
        "citations_resolve": not unresolved,
        "evidence_ids_resolve": not unresolved_evidence,
        "citation_evidence_wiring": not evidence_row_failures,
        "candidate_labels_preserved": not candidate_label_failures,
        "source_evidence_labels_preserved": not source_evidence_label_failures,
        "guidance_labels_preserved": not guidance_label_failures,
        "approved_facts_use_approved_lane": not approved_lane_failures,
        "factual_claims_use_factual_records": not answer_use_failures,
        "automatic_graph_mutation_count_zero": answer.get("automatic_graph_mutation_count") == 0,
    }
    anti_conflation_failures = validate_answer_anti_conflation(answer, packet)
    return {
        "status": "PASS" if (all(checks.values()) and not anti_conflation_failures) else "FAIL",
        "checks": checks, "unresolved_citations": unresolved, "unresolved_evidence_ids": unresolved_evidence,
        "evidence_row_failures": sorted(set(evidence_row_failures)), "candidate_label_failures": candidate_label_failures,
        "source_evidence_label_failures": source_evidence_label_failures, "guidance_label_failures": guidance_label_failures,
        "approved_lane_failures": approved_lane_failures, "answer_use_failures": answer_use_failures,
        "anti_conflation_failures": anti_conflation_failures,
    }


def build_x1_gate(
    *, answer: dict[str, Any] | None, packet: dict[str, Any], retrieval_evidence_report: dict[str, Any],
    claim_evidence_map: dict[str, Any], graph_hash_before: str, graph_hash_after_reasoning: str,
    prompt: dict[str, Any] | None, l2_decision_digest: str, cached_output: dict[str, Any] | None,
    cache_lookup: SemanticCacheLookup, coverage_manifest: dict[str, Any], connected_evidence_map: dict[str, Any],
    run_id: str, provider_failure_code: str | None,
) -> dict[str, Any]:
    """Run the deterministic X1 controls for either an initial or repaired answer."""
    if answer is None:
        x1 = {
            "status": "FAIL",
            "checks": {
                "schema_complete": False, "citations_resolve": False, "candidate_labels_preserved": False,
                "approved_facts_use_approved_lane": False, "automatic_graph_mutation_count_zero": True,
            },
            "unresolved_citations": [], "candidate_label_failures": [],
            "approved_lane_failures": [], "provider_failure_code": provider_failure_code,
        }
    else:
        x1 = validate_answer(answer, packet)
    x1["coverage_complete"] = coverage_complete(retrieval_evidence_report)
    x1["retrieval_evidence_report_valid"] = retrieval_evidence_report["evidence_packet_digest"] == packet["packet_digest"]
    x1["claim_evidence_map_valid"] = all(row["status"] == "grounded" for row in claim_evidence_map["claims"])
    x1["graph_snapshot_match"] = graph_hash_before == graph_hash_after_reasoning
    x1["prompt_hash_replay"] = prompt is None or prompt["prompt_hash"] == digest({key: value for key, value in prompt.items() if key != "prompt_hash"})
    x1["prompt_assembled"] = prompt is not None
    x1["l2_decision_digest"] = l2_decision_digest
    x1["semantic_cache_replay_valid"] = cached_output is None or (
        cache_lookup.hit and cache_lookup.output is not None and coverage_manifest["run_id"] == run_id
        and connected_evidence_map["run_id"] == run_id and retrieval_evidence_report["run_id"] == run_id
        and claim_evidence_map["run_id"] == run_id and claim_evidence_map["answer_digest"] == digest(answer)
    )
    x1["semantic_cache_prior_output_matches"] = cached_output is None or digest(cached_output["answer"]) == digest(answer)
    if (
        not x1["graph_snapshot_match"] or not x1["prompt_hash_replay"] or not x1["semantic_cache_replay_valid"]
        or not x1["semantic_cache_prior_output_matches"] or not x1["retrieval_evidence_report_valid"]
        or not x1["claim_evidence_map_valid"] or not x1["coverage_complete"]
    ):
        x1["status"] = "FAIL"
    return x1


def _append_labeled_markdown_section(lines: list[str], heading: str, items: list[dict[str, Any]], text_key: str) -> None:
    if not items:
        return
    lines.extend([heading, ""])
    for item in items:
        lines.append(f"- **{item['label']}**: {item[text_key]} [`{item['citations'][0]}`]")
    lines.append("")


def render_markdown(answer: dict[str, Any] | None, x3_outcome: str) -> str:
    lines = ["# Governed NEE answer", "", f"**Disposition:** `{x3_outcome}`", ""]
    if answer is None:
        lines.extend(["The query did not produce a deliverable answer because a required governed gate failed.", ""])
        return "\n".join(lines)
    if x3_outcome == "X3E_ALLOW_EVIDENCE_ONLY":
        lines.extend(["**Response mode:** Evidence-only. Governed graph evidence is available, but external synthesis was not authorized for this run.", ""])
    if answer.get("current_state"):
        lines.extend(["## Current state", ""])
        for item in answer["current_state"]:
            citations = ", ".join(f"`{value}`" for value in item.get("citations", []))
            lines.append(f"- {item['statement']} [{citations}]")
        lines.append("")
    if answer["opportunities"]:
        lines.extend(["## Opportunities", ""])
        for index, item in enumerate(answer["opportunities"], 1):
            citations = ", ".join(f"`{value}`" for value in item["citations"])
            lines.append(f"{index}. **{item['opportunity']}** — {item['why_prioritize']} [{citations}]")
        lines.append("")
    if answer.get("next_best_actions"):
        lines.extend(["## Next best actions", ""])
        for index, item in enumerate(answer["next_best_actions"], 1):
            citations = ", ".join(f"`{value}`" for value in item.get("basis_citations", []))
            lines.append(f"{index}. {item['action']} [{citations}]")
        lines.append("")
    if answer["pain_points"]:
        lines.extend(["## Pain points", ""])
        for item in answer["pain_points"]:
            lines.append(f"- {item['pain_point']} [`{item['citations'][0]}`]")
        lines.append("")
    _append_labeled_markdown_section(lines, "## Guidance", answer.get("guidance", []), "guidance")
    if answer["hypotheses"]:
        lines.extend(["## Candidate hypotheses", ""])
        for item in answer["hypotheses"]:
            lines.append(f"- {item['hypothesis']} [`{item['citations'][0]}`]")
        lines.append("")
    _append_labeled_markdown_section(lines, "## Candidate source evidence — citation-only", answer.get("source_evidence", []), "excerpt")
    lines.extend(["## Missing information", ""])
    lines.extend(f"- {value}" for value in answer["missing_information"])
    return "\n".join(lines) + "\n"


