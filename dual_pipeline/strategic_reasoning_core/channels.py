"""
dual_pipeline/strategic_reasoning_core/channels.py

Strategic portfolio assembly, role separation, and decision trace artifact generation.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import digest, stable_id

from .constants import _CHANNEL_BY_ROLE, STRATEGIC_CHANNELS
from .profiling import (
    extract_quantitative_constraints,
    strategic_profile_for_record,
    validate_strategic_profile,
)


def _process_lane_item(item: Any, lane: str) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(item, Mapping) or not isinstance(item.get("record"), Mapping):
        raise ValueError(f"strategic evidence lane {lane} has an invalid record")
    record = item["record"]
    profile = record.get("strategic_profile")
    if not isinstance(profile, Mapping):
        profile = strategic_profile_for_record(
            record_type=str(record.get("record_type", "assertion")),
            lane=lane,
            classification=str(record.get("classification", "internal")),
            text=str(record.get("retrieval_text") or record.get("title") or ""),
        )
    profile = validate_strategic_profile(profile)
    role = profile["knowledge_role"]
    channel = _CHANNEL_BY_ROLE[role]
    metadata = item.get("retrieval_metadata")
    answer_use = (
        metadata.get("answer_use")
        if isinstance(metadata, Mapping)
        else record.get("answer_use")
    )
    row = {
        "record_id": record.get("record_id"),
        "lane": lane,
        "knowledge_role": role,
        "authority_scope": profile["authority_scope"],
        "applicability_scope": profile["applicability_scope"],
        "modality": profile["modality"],
        "realization_state": profile["realization_state"],
        "answer_use": answer_use,
        "evidence_ids": list(record.get("evidence_ids", [])),
    }
    return channel, role, row


def build_strategic_channels(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Build a role-separated portfolio from sealed hydrated retrieval records."""

    channels: dict[str, list[dict[str, Any]]] = {
        channel: [] for channel in STRATEGIC_CHANNELS
    }
    role_counts: Counter[str] = Counter()
    quant_by_record: dict[str, list[dict[str, Any]]] = {}
    for lane in ("approved", "candidate", "external_public"):
        values = packet.get(lane, [])
        if not isinstance(values, list):
            raise ValueError(f"strategic evidence lane {lane} must be an array")
        for item in values:
            channel, role, row = _process_lane_item(item, lane)
            channels[channel].append(row)
            role_counts[role] += 1
            rec = item["record"]
            quant = item.get("quantitative_constraints") or extract_quantitative_constraints(
                str(rec.get("retrieval_text") or rec.get("title") or "")
            )
            if quant and row.get("record_id"):
                quant_by_record[str(row["record_id"])] = list(quant)

    for channel in channels:
        channels[channel].sort(
            key=lambda row: (str(row["lane"]), str(row["record_id"]))
        )
    return {
        "channels": channels,
        "role_counts": dict(sorted(role_counts.items())),
        "channel_counts": {
            channel: len(values) for channel, values in channels.items()
        },
        "portfolio_digest": digest(channels),
        "quantitative_constraints_by_record": quant_by_record,
    }


def create_strategic_evidence_artifact(
    *,
    query_run_id: str,
    graph_snapshot: str,
    source_packet_digest: str,
    portfolio: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal the role-separated projection without copying source prose."""

    artifact_id = stable_id(
        "strategic-evidence-packet",
        query_run_id,
        graph_snapshot,
        source_packet_digest,
        portfolio.get("portfolio_digest"),
        length=28,
    )
    return create_artifact(
        "strategic-evidence-packet",
        artifact_id,
        packet_id=artifact_id,
        query_run_id=query_run_id,
        query_type="opportunity_nba",
        graph_snapshot=graph_snapshot,
        source_packet_digest=source_packet_digest,
        channels=dict(portfolio["channels"]),
        role_counts=dict(portfolio["role_counts"]),
        channel_counts=dict(portfolio["channel_counts"]),
        portfolio_digest=str(portfolio["portfolio_digest"]),
        automatic_graph_mutation_count=0,
        extensions={},
    )


def strategic_role_by_record(packet: Mapping[str, Any]) -> dict[str, str]:
    portfolio = packet.get("strategic_context")
    if not isinstance(portfolio, Mapping):
        portfolio = build_strategic_channels(packet)
    channels = portfolio.get("channels", {})
    result: dict[str, str] = {}
    if isinstance(channels, Mapping):
        for values in channels.values():
            if not isinstance(values, list):
                continue
            for row in values:
                if isinstance(row, Mapping) and isinstance(row.get("record_id"), str):
                    result[str(row["record_id"])] = str(row.get("knowledge_role", ""))
    return result


def create_strategic_reasoning_output(
    *,
    query_run_id: str,
    packet: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal a non-authoritative decision trace for one completed NBA answer."""

    if answer.get("query_type") != "opportunity_nba":
        raise ValueError("strategic reasoning output requires an opportunity_nba answer")
    if answer.get("automatic_graph_mutation_count") != 0:
        raise ValueError("strategic reasoning output cannot bind a graph-mutating answer")
    roles = strategic_role_by_record(packet)
    options: list[dict[str, Any]] = []
    for row in answer.get("opportunities", []):
        citations = [value for value in row.get("citations", []) if isinstance(value, str)]
        options.append(
            {
                "option": row.get("opportunity"),
                "rationale": row.get("why_prioritize"),
                "addresses": list(row.get("addresses", [])),
                "citations": citations,
                "basis_roles": sorted(
                    {roles.get(citation, "current_state_fact") for citation in citations}
                ),
            }
        )
    actions: list[dict[str, Any]] = []
    for row in answer.get("next_best_actions", []):
        citations = [
            value for value in row.get("basis_citations", []) if isinstance(value, str)
        ]
        actions.append(
            {
                "action": row.get("action"),
                "basis_citations": citations,
                "basis_roles": sorted(
                    {roles.get(citation, "current_state_fact") for citation in citations}
                ),
            }
        )
    artifact_id = stable_id(
        "strategic-reasoning-output",
        query_run_id,
        packet.get("packet_digest"),
        digest(answer),
        length=28,
    )
    return create_artifact(
        "strategic-reasoning-output",
        artifact_id,
        output_id=artifact_id,
        query_run_id=query_run_id,
        evidence_packet_digest=str(packet.get("packet_digest", "")),
        decision_frame={
            "query_type": "opportunity_nba",
            "rationale": str(answer.get("rationale", "")),
        },
        options=options,
        recommendation={
            "summary": str(answer.get("rationale", "")),
            "action_count": len(actions),
        },
        next_best_actions=actions,
        assumptions=list(answer.get("assumptions", [])),
        counterarguments=[
            str(row.get("statement", ""))
            for row in answer.get("counterevidence", [])
            if isinstance(row, Mapping)
        ],
        evidence_gaps=list(answer.get("missing_information", [])),
        outcome_measures=[],
        automatic_graph_mutation_count=0,
        extensions={},
    )
