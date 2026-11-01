"""Injected, network-free reasoning providers for conditional-L2 tests."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from dual_pipeline.core import digest
from dual_pipeline.l2 import (
    CallPurpose,
    L2ProviderRequest,
    L2ProviderResponse,
    ModelPolicy,
    TokenUsage,
)
from dual_pipeline.query import reason_over_packet

ResponseOverride = Any | Callable[[L2ProviderRequest, "FakeProviderFactory"], Any]
DEBUG_REPAIR_SENTINEL = "debug-only value must not become a governed claim"


@dataclass(frozen=True)
class RecordedRequest:
    model_policy: ModelPolicy
    request: L2ProviderRequest


class FakeReasoningProvider:
    """Provider facade whose output is governed by a shared fake factory."""

    provider_id = "openai-test-double"

    def __init__(self, factory: "FakeProviderFactory", model_policy: ModelPolicy) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def generate(self, request: L2ProviderRequest) -> L2ProviderResponse:
        return self.factory.generate(self.model_policy, request)


class FakeProviderFactory:
    """Count initialization/calls and return schema-valid local responses by default."""

    def __init__(
        self,
        overrides: Mapping[CallPurpose | str, list[ResponseOverride]] | None = None,
        *,
        usage: TokenUsage | None = None,
        latency_ms: float = 12.5,
    ) -> None:
        self._overrides: dict[CallPurpose, deque[ResponseOverride]] = defaultdict(deque)
        for purpose, values in (overrides or {}).items():
            self._overrides[CallPurpose(purpose)].extend(values)
        self.usage = usage or TokenUsage(
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=50,
            total_tokens=150,
            usage_reported=True,
        )
        self.latency_ms = latency_ms
        self.initialized_policies: list[ModelPolicy] = []
        self.requests: list[RecordedRequest] = []
        self._last_synthesis_request: L2ProviderRequest | None = None

    def __call__(self, model_policy: ModelPolicy) -> FakeReasoningProvider:
        self.initialized_policies.append(model_policy)
        return FakeReasoningProvider(self, model_policy)

    @property
    def initialized_models(self) -> list[str]:
        return [policy.model for policy in self.initialized_policies]

    @property
    def call_purposes(self) -> list[str]:
        return [record.request.purpose.value for record in self.requests]

    @property
    def called_models(self) -> list[str]:
        return [record.model_policy.model for record in self.requests]

    def valid_output(self, request: L2ProviderRequest) -> dict[str, Any]:
        source = request
        if request.purpose is CallPurpose.SCHEMA_REPAIR:
            if self._last_synthesis_request is None:
                raise AssertionError("schema repair requires a preceding synthesis request")
            source = self._last_synthesis_request
        if source.evidence_packet is None:
            raise AssertionError("a synthesis response requires the sealed evidence packet")
        output = reason_over_packet(dict(source.plan), dict(source.evidence_packet))
        if output["query_type"] == "candidate_promotion_review":
            candidates = [
                item
                for item in source.evidence_packet.get("candidate", [])
                if item.get("retrieval_metadata", {}).get("answer_use")
                == "candidate_hypothesis"
            ]
            if candidates:
                record = candidates[0]["record"]
                output["proposed_assertions"] = [
                    {
                        "proposal": f"CANDIDATE: review {record['title']}",
                        "citations": [record["record_id"]],
                        "evidence_ids": list(record["evidence_ids"]),
                        "lane": "candidate",
                        "proposal_only": True,
                    }
                ]
        approved = source.evidence_packet.get("approved", [])
        factual_approved = [
            item
            for item in approved
            if item.get("retrieval_metadata", {}).get("answer_use")
            == "factual_answer"
        ]
        if factual_approved:
            fallback = factual_approved[0]["record"]
            if not output.get("opportunities"):
                output["opportunities"] = [
                    {
                        "opportunity": f"Optimize {fallback['title']}",
                        "why_prioritize": fallback["retrieval_text"],
                        "addresses": [fallback["title"]],
                        "citations": [fallback["record_id"]],
                        "evidence_ids": list(fallback["evidence_ids"]),
                        "lane": fallback.get("lane", "approved"),
                    }
                ]
            for row in output["opportunities"]:
                if row["addresses"]:
                    continue
                row["addresses"] = [fallback["title"]]
                row["citations"] = list(
                    dict.fromkeys(row["citations"] + [fallback["record_id"]])
                )
                row["evidence_ids"] = list(
                    dict.fromkeys(row["evidence_ids"] + fallback["evidence_ids"])
                )
        if not output.get("next_best_actions"):
            basis_citations = [
                citation
                for field in ("current_state", "pain_points", "opportunities")
                for row in output.get(field, [])
                if isinstance(row, dict)
                for citation in row.get("citations", [])
                if isinstance(citation, str)
            ][:2]
            if basis_citations:
                output["next_best_actions"] = [
                    {
                        "action": (
                            "Validate the recommendations with the accountable workstream owner before taking action."
                        ),
                        "basis_citations": basis_citations,
                    }
                ]
        return output

    def generate(
        self, model_policy: ModelPolicy, request: L2ProviderRequest
    ) -> L2ProviderResponse:
        self.requests.append(RecordedRequest(model_policy, request))
        if request.purpose in {CallPurpose.PRIMARY, CallPurpose.QUALITY_ESCALATION}:
            self._last_synthesis_request = request
        queue = self._overrides[request.purpose]
        if queue:
            override = queue.popleft()
            output = override(request, self) if callable(override) else deepcopy(override)
        else:
            output = self.valid_output(request)
        return L2ProviderResponse(
            output=output,
            response_digest=digest(output),
            model=model_policy.model,
            usage=self.usage,
            latency_ms=self.latency_ms,
        )


def schema_valid_quality_failure(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Return a valid object that fails typed route/materiality obligations."""

    output = factory.valid_output(request)
    output["current_state"] = []
    output["patterns"] = []
    output["pain_points"] = []
    output["opportunities"] = []
    output["next_best_actions"] = []
    output["rationale"] = "Incomplete governed rationale."
    output["assumptions"] = []
    output["what_would_change_the_conclusion"] = []
    return output


def truncated_valid_json(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> str:
    """Remove only the final object delimiter from an otherwise valid response."""

    serialized = json.dumps(factory.valid_output(request), sort_keys=True)
    if not serialized.endswith("}"):
        raise AssertionError("valid-output serialization must end with an object delimiter")
    return serialized[:-1]


def missing_unambiguous_current_state_citation(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Remove a citation whose record is uniquely derivable from present evidence IDs."""

    output = factory.valid_output(request)
    if request.evidence_packet is None:
        raise AssertionError("citation fixture requires the sealed evidence packet")
    support_counts: dict[tuple[str, str], int] = {}
    for lane in ("approved", "candidate", "external_public"):
        for item in request.evidence_packet.get(lane, []):
            for evidence_id in item["record"]["evidence_ids"]:
                key = (lane, evidence_id)
                support_counts[key] = support_counts.get(key, 0) + 1
    row = next(
        value
        for value in output["current_state"]
        if value["evidence_ids"]
        and all(
            support_counts[(value["lane"], evidence_id)] == 1
            for evidence_id in value["evidence_ids"]
        )
    )
    row.pop("citations")
    return output


def missing_schema_version(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Return an otherwise valid response with one repairable structural defect."""

    output = factory.valid_output(request)
    output.pop("schema_version")
    return output


def repair_with_invented_claim(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Return schema-valid repair output that improperly invents a factual value."""

    output = factory.valid_output(request)
    if not output["current_state"]:
        raise AssertionError("repair invention fixture requires current-state output")
    output["current_state"][0]["statement"] += " Invented repair-only claim."
    return output


def response_with_unknown_debug_field(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Return valid factual rows plus one repairable unknown diagnostic field."""

    output = factory.valid_output(request)
    output["debug"] = DEBUG_REPAIR_SENTINEL
    return output


def repair_moves_debug_to_current_state(
    request: L2ProviderRequest, factory: FakeProviderFactory
) -> dict[str, Any]:
    """Attempt to move an unknown diagnostic string into a governed claim role."""

    output = factory.valid_output(request)
    if not output["current_state"]:
        raise AssertionError("debug-move fixture requires current-state output")
    output["current_state"][0]["statement"] = DEBUG_REPAIR_SENTINEL
    return output
