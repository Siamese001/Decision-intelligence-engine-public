"""Ingestion reasoning service orchestrating decision, request building, provider execution, and telemetry."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from ..core import write_json
from ..provider_request import (
    aggregate_token_usage,
    measure_responses_request,
    provider_request_budget_verdict,
)
from .advisory import (
    _call_payload,
    _correlation,
    build_ingestion_provider_body,
    build_ingestion_reasoning_packet,
    create_ingestion_reasoning_proposal,
    ingestion_proposal_output_schema,
    validate_ingestion_proposal_output,
)
from .errors import IngestionProviderError, ProposalValidationError
from .execution_runner import execute_provider_loop, init_and_check_budget
from .models import (
    IngestionModelPolicy,
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningPolicy,
    ProviderFactory,
)
from .policy import build_ingestion_reasoning_decision
from .selective import (
    merge_advisory_subpacket,
    prepare_selective_candidates,
)


class IngestionReasoningService:
    """Execute one bounded advisory call after deterministic C0/L0 processing."""

    def __init__(
        self,
        *,
        policy: IngestionReasoningPolicy,
        mode: str,
        provider_factory: ProviderFactory,
        offline: bool = False,
        enable_selective_advisory: bool = False,
    ) -> None:
        if mode not in {"shadow", "advisory"}:
            raise ValueError("ingestion reasoning mode must be shadow or advisory")
        self.policy = policy
        self.mode = mode
        self.provider_factory = provider_factory
        self.offline = offline
        self.enable_selective_advisory = enable_selective_advisory

    @staticmethod
    def _base_result(
        *,
        decision: Mapping[str, Any],
        status: str,
        disposition: str,
        failure_code: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status, "mode": decision["mode"], "disposition": disposition,
            "decision_id": decision["decision_id"],
            "decision_digest": decision["canonical_digest"]["value"],
            "decision_reason_code": decision["reason_code"],
            "initial_complexity": decision["initial_complexity"],
            "final_complexity": decision["final_complexity"],
            "selected_model_tier": decision["selected_model_tier"],
            "selected_model": decision["selected_model"],
            "reasoning_effort": decision["reasoning_effort"],
            "provider_egress_allowed": decision["provider_egress_allowed"],
            "packet_digest": None, "proposal_id": None, "proposal_digest": None,
            "provider_call_count": 0, "primary_call_count": 0, "repair_call_count": 0,
            "escalation_call_count": 0, "judge_call_count": 0, "challenger_call_count": 0,
            "failure_code": failure_code, "graph_mutation_count": 0, "canonical_approval_count": 0,
        }

    @staticmethod
    def _record_l2d_decision(
        recorder: Any,
        source_id: str,
        decision: dict[str, Any],
        request_metrics: dict[str, Any],
        budget_verdict: dict[str, Any],
    ) -> None:
        recorder.record(
            stage="L2D",
            event_type="l2_execution_decision",
            payload=decision,
            correlation=_correlation(source_id),
            otel_attributes={
                "initial_complexity": decision["initial_complexity"],
                "final_complexity": decision["final_complexity"],
                "l2_policy": "conditional_ingestion_advisory",
                "l2_required": decision["call_selected"],
                "l2_reason_code": decision["reason_code"],
                "selected_model_tier": decision["selected_model_tier"],
                "selected_model": decision["selected_model"],
                "provider_egress_allowed": decision["provider_egress_allowed"],
                "request_input_chars": request_metrics.get("request_input_chars", 0),
                "request_schema_chars": request_metrics.get("request_schema_chars", 0),
                "request_serialized_chars": request_metrics.get("request_serialized_chars", 0),
                "request_payload_digest": request_metrics.get("request_payload_digest"),
                "budget_status": budget_verdict.get("status", "NOT_APPLICABLE"),
                "budget_reason_code": budget_verdict.get("reason_code", "PROVIDER_CALL_NOT_SELECTED"),
                "model_call_count": 0,
                "repair_call_count": 0,
                "escalation_call_count": 0,
                "judge_call_count": 0,
                "challenger_call_count": 0,
            },
        )

    def _stabilize_request(
        self,
        decision_inputs: dict[str, Any],
        decision: dict[str, Any],
        bundle: Mapping[str, Any],
        resolutions: list[Mapping[str, Any]],
        comparison: Mapping[str, Any],
        governed_context: Mapping[str, Any],
        routes: list[Mapping[str, Any]],
        target_ids: set[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], IngestionModelPolicy, IngestionProviderRequest, dict[str, Any], dict[str, Any]]:
        for _ in range(8):
            packet = build_ingestion_reasoning_packet(
                decision=decision,
                bundle=bundle,
                resolutions=resolutions,
                comparison=comparison,
                governed_context=governed_context,
                routes=routes,
                candidate_ids=target_ids,
            )
            schema = ingestion_proposal_output_schema(packet)
            model_policy = self.policy.model_for_tier(decision["final_complexity"])
            primary_request = IngestionProviderRequest(
                purpose="PRIMARY",
                packet=packet,
                output_schema=schema,
            )
            request_metrics = measure_responses_request(
                build_ingestion_provider_body(
                    policy=self.policy,
                    model_policy=model_policy,
                    request=primary_request,
                )
            )
            budget_verdict = provider_request_budget_verdict(
                request_metrics=request_metrics,
                maximum_provider_request_chars=int(self.policy.routing["maximum_provider_request_chars"]),
            )
            decision_budget = decision["extensions"]["com.partner_advisory.nee.read-write-budget"]
            if (
                decision_budget["prompt_size_source"] == "SERIALIZED_PROVIDER_REQUEST"
                and decision["estimated_prompt_size_chars"] == request_metrics["request_serialized_chars"]
            ):
                return decision, packet, schema, model_policy, primary_request, request_metrics, budget_verdict
            decision = build_ingestion_reasoning_decision(
                **decision_inputs,
                estimated_prompt_size_chars=int(request_metrics["request_serialized_chars"]),
                prompt_size_source="SERIALIZED_PROVIDER_REQUEST",
                request_metrics=request_metrics,
                budget_verdict=budget_verdict,
            )
        raise ValueError("ingestion request measurement did not stabilize after decision binding")

    def execute(
        self,
        *,
        run_dir: Path,
        recorder: Any,
        bundle: Mapping[str, Any],
        plan: Mapping[str, Any],
        resolutions: list[Mapping[str, Any]],
        comparison: Mapping[str, Any],
        governed_context: Mapping[str, Any],
        routes: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        decision_inputs = {
            "policy": self.policy,
            "mode": self.mode,
            "offline": self.offline,
            "bundle": bundle,
            "plan": plan,
            "resolutions": resolutions,
            "comparison": comparison,
            "governed_context": governed_context,
            "routes": routes,
        }
        decision = build_ingestion_reasoning_decision(**decision_inputs)
        packet: dict[str, Any] | None = None
        schema: dict[str, Any] | None = None
        model_policy: IngestionModelPolicy | None = None
        primary_request: IngestionProviderRequest | None = None
        request_metrics: dict[str, Any] = {}
        budget_verdict: dict[str, Any] = {}
        use_subpacket, hard_cands, easy_cands, target_ids = prepare_selective_candidates(
            bundle, resolutions, comparison, self.policy,
            self.enable_selective_advisory or bool(bundle.get("enable_selective_advisory", False)),
        )
        if decision["call_selected"]:
            decision, packet, schema, model_policy, primary_request, request_metrics, budget_verdict = (
                self._stabilize_request(
                    decision_inputs, decision, bundle, resolutions, comparison,
                    governed_context, routes, target_ids,
                )
            )
        write_json(run_dir / "ingestion_reasoning_decision.json", decision)
        self._record_l2d_decision(recorder, bundle["source_id"], decision, request_metrics, budget_verdict)
        if not decision["call_selected"]:
            result = self._base_result(
                decision=decision,
                status="SKIPPED",
                disposition="DETERMINISTIC_ONLY",
            )
            write_json(run_dir / "ingestion_reasoning_execution.json", result)
            return result

        if (
            packet is None
            or schema is None
            or model_policy is None
            or primary_request is None
        ):
            raise RuntimeError("selected ingestion call is missing its frozen request")

        result, provider = init_and_check_budget(
            service_ref=self,
            decision=decision,
            packet=packet,
            model_policy=model_policy,
            request_metrics=request_metrics,
            budget_verdict=budget_verdict,
            bundle=bundle,
            recorder=recorder,
            run_dir=run_dir,
        )
        if provider is None:
            return result

        response, responses, output, validation_errors, fallback = execute_provider_loop(
            policy=self.policy,
            provider=provider,
            primary_request=primary_request,
            schema=schema,
            model_policy=model_policy,
            packet=packet,
            bundle=bundle,
            resolutions=resolutions,
            comparison=comparison,
            use_subpacket=use_subpacket,
            easy_cands=easy_cands,
            result=result,
            recorder=recorder,
            run_dir=run_dir,
        )
        if fallback is not None:
            return fallback

        if response is None or output is None:
            result.update(aggregate_token_usage(responses))
            result.update(
                status="CONSERVATIVE_FALLBACK",
                disposition="INVALID_OUTPUT_DETERMINISTIC_UNCHANGED",
                failure_code="INVALID_STRUCTURED_RESPONSE",
            )
            recorder.record(
                stage="L2",
                event_type="l2_provider_failure",
                payload={
                    "decision_id": decision["decision_id"],
                    "failure_code": "INVALID_STRUCTURED_RESPONSE",
                    "validation_errors": list(validation_errors),
                    "deterministic_dispositions_unchanged": True,
                },
                correlation=_correlation(bundle["source_id"]),
                model_id=model_policy.model,
            )
            write_json(run_dir / "ingestion_reasoning_execution.json", result)
            return result

        proposal = create_ingestion_reasoning_proposal(
            mode=self.mode,
            decision=decision,
            packet=packet,
            model_policy=model_policy,
            response=response,
            output=output,
        )
        write_json(run_dir / "ingestion_reasoning_proposal.json", proposal)
        usage = aggregate_token_usage(responses)
        disp = "SHADOW_PROPOSAL_RECORDED" if self.mode == "shadow" else "ADVISORY_PROPOSAL_RECORDED"
        result.update(
            status="ADVISORY_READY",
            disposition=disp,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["canonical_digest"]["value"],
            delivered_model=response.delivered_model,
            response_digest=response.response_digest,
            **usage,
            failure_code=None,
        )
        if use_subpacket:
            result["selective_reasoning"] = {
                "hard_candidates_count": len(hard_cands),
                "easy_candidates_count": len(easy_cands),
                "subpacket_used": True,
            }
        recorder.record(
            stage="L2",
            event_type="l2_output_selection",
            payload={
                "decision_id": decision["decision_id"],
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["canonical_digest"]["value"],
                "selection": "ADVISORY_ONLY",
                "deterministic_dispositions_unchanged": True,
                "canonical_approval_count": 0,
                "graph_mutation_count": 0,
            },
            correlation=_correlation(bundle["source_id"]),
            model_id=response.delivered_model,
            record_count=len(proposal["recommendations"]),
        )
        write_json(run_dir / "ingestion_reasoning_execution.json", result)
        return result

