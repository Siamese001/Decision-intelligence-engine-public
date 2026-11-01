"""Data models and decisions for query routing."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from ..contracts import create_artifact
from ..core import stable_id


class QueryDestination(StrEnum):
    GENERAL_ASSISTANT_HANDOFF = "GENERAL_ASSISTANT_HANDOFF"
    OPERATIONAL_WORKFLOW_HANDOFF = "OPERATIONAL_WORKFLOW_HANDOFF"
    GRAPH_EVIDENCE_UTILITY = "GRAPH_EVIDENCE_UTILITY"
    NEE_DECISION_INTELLIGENCE = "NEE_DECISION_INTELLIGENCE"
    ROUTE_AMBIGUOUS = "ROUTE_AMBIGUOUS"


@dataclass(frozen=True)
class RoutingText:
    """Normalized instruction text used by the deterministic router only."""

    normalized: str
    instruction: str
    ignored_context: bool
    removed_negation: bool
    delegated_request: bool
    outer_wrapper_unwrapped: bool


@dataclass(frozen=True)
class QueryRoutingPolicy:
    schema_version: str
    mode: str
    automatic_handoff: bool
    external_classifier_allowed: bool
    operator_override_allowed: bool
    default_delivery_profile: str
    default_artifact_type: str
    allowed_destinations: frozenset[str]
    routing_lexicon_version: str
    routing_lexicon: Mapping[str, tuple[str, ...]]
    policy_digest: str


@dataclass(frozen=True)
class DestinationDecision:
    destination: QueryDestination
    eligibility_status: str
    eligibility_checks: Mapping[str, str]
    query_type: str | None
    materiality: str
    delivery_profile: str
    artifact_type: str
    reason_codes: tuple[str, ...]
    handoff_permitted: bool
    operator_override: bool
    policy_digest: str
    conflicting_destinations: tuple[str, ...] = ()
    resolution_options: tuple[str, ...] = ()

    @property
    def enters_query_runtime(self) -> bool:
        return self.destination in {
            QueryDestination.GRAPH_EVIDENCE_UTILITY,
            QueryDestination.NEE_DECISION_INTELLIGENCE,
        }

    def to_artifact(
        self,
        *,
        request_id: str,
        run_id: str,
        trace_id: str,
        decided_at: str,
    ) -> dict[str, Any]:
        artifact_id = stable_id("query-destination", request_id, self.policy_digest, length=28)
        extensions: dict[str, Any] = {}
        if self.destination is QueryDestination.ROUTE_AMBIGUOUS and self.conflicting_destinations:
            extensions["nee.ambiguity-resolution"] = {
                "conflicting_destinations": list(self.conflicting_destinations),
                "resolution_options": list(self.resolution_options),
                "guidance": (
                    "Ambiguous query destination. Supply an operator destination_override "
                    "or clarify whether the query is decision intelligence, operational, or general."
                ),
            }
        return create_artifact(
            "query-destination-decision",
            artifact_id,
            decision_id=artifact_id,
            request_id=request_id,
            run_id=run_id,
            trace_id=trace_id,
            destination=self.destination.value,
            eligibility_status=self.eligibility_status,
            eligibility_checks=dict(self.eligibility_checks),
            query_type=self.query_type,
            materiality=self.materiality,
            delivery_profile=self.delivery_profile,
            artifact_type=self.artifact_type,
            reason_codes=list(self.reason_codes),
            handoff_permitted=self.handoff_permitted,
            operator_override=self.operator_override,
            automatic_egress_count=0,
            policy_digest=self.policy_digest,
            decided_at=decided_at,
            extensions=extensions,
        )
