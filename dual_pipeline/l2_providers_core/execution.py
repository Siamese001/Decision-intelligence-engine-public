"""Execution orchestration for conditional L2 and content repair."""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from config.credentials import CredentialManager
from dual_pipeline.core import digest
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    L2Disposition,
    L2ExecutionDecision,
    ModelPolicy,
    ProviderFailureCode,
    SchemaFailureCode,
    canonical_query_type,
    query_model_policy_for_assurance,
    resolve_openai_model_id,
)
from dual_pipeline.provider_request import (
    measure_responses_request,
    provider_request_budget_verdict,
)

from .decision import (
    _artifact_digest,
    _verified_evidence_packet_hash,
    decide_l2_execution,
)
from .payload import (
    _verify_governed_prompt,
    build_l2_provider_body,
)
from .provider import build_call_record
from .types import (
    ContentRepairResult,
    L2CallRecord,
    L2ExecutionResult,
    L2ProviderError,
    L2ProviderRequest,
    L2ProviderResponse,
    QualityDeficiency,
    ReasoningProvider,
    ReasoningProviderFactory,
    SchemaIssue,
    _CallBudget,
)
from .validation import (
    _schema_repair_preserves_facts,
    evaluate_semantic_quality,
    validate_decision_model_output,
)


def _failure_result(
    *,
    call_records: Sequence[L2CallRecord],
    provider_initialization_count: int,
    failure: L2ProviderError,
    schema_issues: Sequence[SchemaIssue] = (),
    quality_deficiencies: Sequence[QualityDeficiency] = (),
) -> L2ExecutionResult:
    return L2ExecutionResult(
        disposition=L2Disposition.FAILED,
        answer=None,
        selected_output_digest=None,
        call_records=tuple(call_records),
        provider_initialization_count=provider_initialization_count,
        schema_issues=tuple(schema_issues),
        quality_deficiencies=tuple(quality_deficiencies),
        provider_failure_code=failure.code.value,
        provider_failure=failure.safe_message,
    )


def _invoke_provider(
    *,
    provider: ReasoningProvider,
    config: L2Config,
    model_policy: ModelPolicy,
    model_profile: str,
    request: L2ProviderRequest,
    budget: _CallBudget,
    call_records: list[L2CallRecord],
) -> tuple[L2ProviderResponse | None, L2ProviderError | None]:
    request_metrics = measure_responses_request(
        build_l2_provider_body(
            config=config,
            model_policy=model_policy,
            request=request,
        )
    )

    from dual_pipeline.token_preflight_gate import validate_context_window
    validate_context_window(request_metrics, model_policy)

    from dual_pipeline.cost_tracker import get_token_tracker
    estimated_tokens = request_metrics.get("request_input_chars", 0) // 4
    get_token_tracker().intercept_token_call(estimated_tokens)

    budget_verdict = provider_request_budget_verdict(
        request_metrics=request_metrics,
        maximum_provider_request_chars=(

            config.request_budget.maximum_provider_request_chars
        ),
    )
    if budget_verdict["status"] != "ALLOW":
        return None, L2ProviderError(
            ProviderFailureCode.BUDGET_EXHAUSTED,
            "serialized provider request exceeds the governed G20 budget",
        )
    try:
        ordinal = budget.consume(request.purpose)
    except L2ProviderError as failure:
        return None, failure
    started = time.perf_counter()
    response: L2ProviderResponse | None = None
    failure: L2ProviderError | None = None
    try:
        response = provider.generate(request)
        if response.model != model_policy.model:
            failure = L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "provider response model did not match the selected pinned model",
            )
    except L2ProviderError as exc:
        failure = exc
    except Exception:
        failure = L2ProviderError(
            ProviderFailureCode.PROVIDER_UNAVAILABLE,
            "external provider failed without a governed response",
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    call_records.append(
        build_call_record(
            model_policy=model_policy,
            model_profile=model_profile,
            purpose=request.purpose,
            ordinal=ordinal,
            response=response,
            latency_ms=(
                response.latency_ms
                if response is not None
                else failure.latency_ms
                if failure is not None and failure.latency_ms is not None
                else latency_ms
            ),
            failure=failure,
        )
    )
    return (None if failure else response), failure



def execute_conditional_l2(  # noqa: C901
    *,
    decision: L2ExecutionDecision,
    config: L2Config,
    validated_request: Mapping[str, Any],
    route: Mapping[str, Any],
    question: str,
    plan: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    prompt: Mapping[str, Any] | None,
    decision_prompt: Mapping[str, Any] | None = None,
    provider_factory: ReasoningProviderFactory | None = None,
) -> L2ExecutionResult:
    """Execute a single bounded primary/repair/escalation path or fail closed."""

    try:
        packet_hash = _verified_evidence_packet_hash(evidence_packet)
        plan_hash = _artifact_digest(plan)
        route_hash = _artifact_digest(route)
        decision_environment = CredentialManager.get_process_environment()
        if validated_request.get("external_provider_offline") is True:
            decision_environment["NEE_EXTERNAL_L2_OFFLINE"] = "1"
        expected_decision = decide_l2_execution(
            validated_request=validated_request,
            plan=plan,
            route=route,
            evidence_packet=evidence_packet,
            config=config,
            prompt=decision_prompt if decision_prompt is not None else prompt,
            environment=decision_environment,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                str(exc),
            ),
        )
    if (
        decision != expected_decision
        or decision.validated_request_digest != digest(validated_request)
        or str(validated_request.get("normalized_question", "")) != question
        or str(validated_request.get("run_id", "")) != decision.run_id
        or str(validated_request.get("trace_id", "")) != decision.trace_id
        or decision.evidence_packet_hash != packet_hash
        or decision.policy_config_digest != config.config_digest
        or decision.query_plan_digest != plan_hash
        or decision.route_decision_digest != route_hash
        or decision.query_type != canonical_query_type(str(plan.get("query_type", "")))
        or decision.materiality != str(plan.get("materiality", ""))
    ):
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "L2 decision bindings do not match the sealed packet, L1 plan, and policy",
            ),
        )
    if not decision.required:
        return L2ExecutionResult(
            disposition=L2Disposition.SKIPPED,
            answer=None,
            selected_output_digest=None,
            call_records=(),
            provider_initialization_count=0,
        )
    if not decision.provider_egress_allowed:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED,
                "required external L2 cannot run because provider egress is not authorized",
            ),
        )
    if prompt is None:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                "required external L2 cannot run without a governed prompt",
            ),
        )
    try:
        _verify_governed_prompt(
            prompt=prompt,
            question=question,
            plan=plan,
            decision=decision,
            evidence_packet=evidence_packet,
            evidence_packet_hash=packet_hash,
        )
    except ValueError as exc:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                str(exc),
            ),
        )
    try:
        selected_model_policy = query_model_policy_for_assurance(
            config,
            str(decision.selected_model_profile),
        )
    except (TypeError, ValueError):
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "required L2 decision does not identify a governed assurance profile",
            ),
        )
    resolved_model_id = resolve_openai_model_id(selected_model_policy)
    if resolved_model_id != decision.selected_model:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "selected model does not match the governed policy snapshot",
            ),
        )
    primary_request = L2ProviderRequest(
        purpose=CallPurpose.PRIMARY,
        question=question,
        plan=plan,
        evidence_packet=evidence_packet,
        prompt=prompt,
    )
    exact_request_metrics = measure_responses_request(
        build_l2_provider_body(
            config=config,
            model_policy=selected_model_policy,
            request=primary_request,
        )
    )
    exact_budget_verdict = provider_request_budget_verdict(
        request_metrics=exact_request_metrics,
        maximum_provider_request_chars=(
            config.request_budget.maximum_provider_request_chars
        ),
    )
    if exact_budget_verdict["status"] != "ALLOW":
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.BUDGET_EXHAUSTED,
                "serialized provider request exceeds the governed G20 budget",
            ),
        )
    factory = provider_factory
    if factory is None:
        return _failure_result(
            call_records=(),
            provider_initialization_count=0,
            failure=L2ProviderError(
                ProviderFailureCode.PROVIDER_UNAVAILABLE,
                "required external L2 has no explicitly injected provider factory",
            ),
        )
    call_records: list[L2CallRecord] = []
    budget = _CallBudget(decision)
    provider_initialization_count = 1
    try:
        provider = factory(selected_model_policy)
    except L2ProviderError as failure:
        return _failure_result(
            call_records=call_records,
            provider_initialization_count=provider_initialization_count,
            failure=failure,
        )
    except Exception:
        return _failure_result(
            call_records=call_records,
            provider_initialization_count=provider_initialization_count,
            failure=L2ProviderError(
                ProviderFailureCode.PROVIDER_UNAVAILABLE,
                "required external provider could not be initialized",
            ),
        )

    primary, failure = _invoke_provider(
        provider=provider,
        config=config,
        model_policy=selected_model_policy,
        model_profile=str(decision.selected_model_profile),
        request=primary_request,
        budget=budget,
        call_records=call_records,
    )
    if failure is not None or primary is None:
        return _failure_result(
            call_records=call_records,
            provider_initialization_count=provider_initialization_count,
            failure=failure
            or L2ProviderError(
                ProviderFailureCode.PROVIDER_UNAVAILABLE,
                "required primary L2 call did not return a governed response",
            ),
        )

    candidate_output = primary.output
    schema = validate_decision_model_output(
        candidate_output,
        evidence_packet=evidence_packet,
        expected_query_type=decision.query_type,
    )
    repaired = False
    if not schema.valid:
        if not schema.repairable:
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    "primary L2 output has non-repairable structured-output defects",
                ),
                schema_issues=schema.issues,
            )
        repair_request = L2ProviderRequest(
            purpose=CallPurpose.SCHEMA_REPAIR,
            question="",
            plan={},
            evidence_packet=None,
            prompt=None,
            original_response=candidate_output,
            validation_errors=tuple(issue.to_text() for issue in schema.issues),
        )
        repaired_response, failure = _invoke_provider(
            provider=provider,
            config=config,
            model_policy=selected_model_policy,
            model_profile=str(decision.selected_model_profile),
            request=repair_request,
            budget=budget,
            call_records=call_records,
        )
        if failure is not None or repaired_response is None:
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=failure
                or L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    "schema repair did not return a governed response",
                ),
                schema_issues=schema.issues,
            )
        candidate_output = repaired_response.output
        schema = validate_decision_model_output(
            candidate_output,
            evidence_packet=evidence_packet,
            expected_query_type=decision.query_type,
        )
        if not schema.valid:
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    "structured output remains invalid after the one permitted schema repair",
                ),
                schema_issues=schema.issues,
            )
        assert schema.value is not None
        if not _schema_repair_preserves_facts(
            original_response=primary.output,
            repaired_output=schema.value,
            evidence_packet=evidence_packet,
        ):
            preservation_issue = SchemaIssue(
                SchemaFailureCode.GOVERNANCE_VIOLATION,
                "$",
                "schema repair introduced factual values absent from the original response",
                False,
            )
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    "schema repair violated factual-value preservation",
                ),
                schema_issues=(preservation_issue,),
            )
        repaired = True

    assert schema.value is not None
    deficiencies = evaluate_semantic_quality(
        question=question,
        plan=plan,
        evidence_packet=evidence_packet,
        answer=schema.value,
    )
    while (
        deficiencies
        and not repaired
        and budget.escalation < decision.maximum_escalation_calls
    ):
        escalation_request = L2ProviderRequest(
            purpose=CallPurpose.QUALITY_ESCALATION,
            question=question,
            plan=plan,
            evidence_packet=evidence_packet,
            prompt=prompt,
            quality_deficiencies=tuple(
                deficiency.to_text() for deficiency in deficiencies
            ),
        )
        escalated_response, failure = _invoke_provider(
            provider=provider,
            config=config,
            model_policy=selected_model_policy,
            model_profile=str(decision.selected_model_profile),
            request=escalation_request,
            budget=budget,
            call_records=call_records,
        )
        if failure is not None or escalated_response is None:
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=failure
                or L2ProviderError(
                    ProviderFailureCode.SEMANTIC_QUALITY_GATE_FAILED,
                    "quality escalation did not return a governed response",
                ),
                quality_deficiencies=deficiencies,
            )
        escalation_schema = validate_decision_model_output(
            escalated_response.output,
            evidence_packet=evidence_packet,
            expected_query_type=decision.query_type,
        )
        if not escalation_schema.valid:
            return _failure_result(
                call_records=call_records,
                provider_initialization_count=provider_initialization_count,
                failure=L2ProviderError(
                    ProviderFailureCode.INVALID_STRUCTURED_RESPONSE,
                    "quality escalation output has invalid structured-output defects",
                ),
                schema_issues=escalation_schema.issues,
            )
        assert escalation_schema.value is not None
        deficiencies = evaluate_semantic_quality(
            question=question,
            plan=plan,
            evidence_packet=evidence_packet,
            answer=escalation_schema.value,
        )
        if not deficiencies:
            return L2ExecutionResult(
                disposition=L2Disposition.ESCALATED,
                answer=escalation_schema.value,
                selected_output_digest=digest(escalation_schema.value),
                call_records=tuple(call_records),
                provider_initialization_count=provider_initialization_count,
                delivered_model=escalated_response.model,
                delivered_model_profile=decision.selected_model_profile,
            )
    if deficiencies:
        return _failure_result(
            call_records=call_records,
            provider_initialization_count=provider_initialization_count,
            failure=L2ProviderError(
                ProviderFailureCode.SEMANTIC_QUALITY_GATE_FAILED,
                "typed semantic-quality gates failed; runtime X1 must adjudicate a new bounded run",
            ),
            quality_deficiencies=deficiencies,
        )

    return L2ExecutionResult(
        disposition=L2Disposition.REPAIRED if repaired else L2Disposition.PRIMARY,
        answer=schema.value,
        selected_output_digest=digest(schema.value),
        call_records=tuple(call_records),
        provider_initialization_count=provider_initialization_count,
        delivered_model=primary.model,
        delivered_model_profile=decision.selected_model_profile,
    )


def execute_content_repair(  # noqa: C901
    *,
    decision: L2ExecutionDecision,
    config: L2Config,
    question: str,
    plan: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    prompt: Mapping[str, Any] | None,
    original_answer: Mapping[str, Any],
    defect_codes: Sequence[str],
    provider_factory: ReasoningProviderFactory | None,
    prior_model_calls: int,
) -> ContentRepairResult:
    """Make one answer-only revision after a repairable X1 judge verdict.

    This is intentionally narrower than generic L2 execution: it is only for
    an enhanced-assurance answer, it uses the same pinned Terra policy, and it
    can neither retrieve new evidence nor make a second repair.
    """

    if decision.assurance_profile != "enhanced":
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            skip_reason="ASSURANCE_NOT_ELIGIBLE",
        )
    if not defect_codes:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            skip_reason="NO_REPAIRABLE_DEFECTS",
        )
    if prompt is None:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.INVALID_STRUCTURED_RESPONSE.value,
            provider_failure="content repair requires the original governed prompt",
        )
    if not decision.provider_egress_allowed or not config.provider_egress_allowed:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED.value,
            provider_failure="content repair provider egress is not authorized",
        )
    original_schema = validate_decision_model_output(
        original_answer,
        evidence_packet=evidence_packet,
        expected_query_type=decision.query_type,
    )
    if not original_schema.valid:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.INVALID_STRUCTURED_RESPONSE.value,
            provider_failure="content repair requires a deterministically valid original answer",
        )
    deterministic_defects = {
        "CLIENT_READINESS_GAP",
        "CLIENT_JARGON",
        "UNDEFINED_CLIENT_TERM",
        "VISUAL_REVIEW_REQUIRED",
    }
    if defect_codes and all(code in deterministic_defects for code in defect_codes):
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

        healed_answer, changes = DeterministicHealer.heal_decision_answer(
            original_answer, defect_codes
        )
        if changes:
            return ContentRepairResult(
                answer=healed_answer,
                call_record=None,
                provider_initialization_count=0,
                skip_reason="DETERMINISTIC_HEALING_PREFILTER_RESOLVED",
            )
    if provider_factory is None:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.PROVIDER_UNAVAILABLE.value,
            provider_failure="content repair has no explicitly injected provider factory",
        )
    policy = query_model_policy_for_assurance(config, "enhanced")
    if (
        policy.model != decision.selected_model
        or decision.selected_model_profile != "enhanced"
        or policy.reasoning_effort != "max"
    ):
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.UNSUPPORTED_MODEL.value,
            provider_failure="content repair model does not match the sealed enhanced-assurance decision",
        )
    request = L2ProviderRequest(
        purpose=CallPurpose.CONTENT_REPAIR,
        question=question,
        plan=plan,
        evidence_packet=evidence_packet,
        prompt=prompt,
        original_response=original_answer,
        quality_deficiencies=tuple(str(value) for value in defect_codes),
    )
    try:
        request_metrics = measure_responses_request(
            build_l2_provider_body(config=config, model_policy=policy, request=request)
        )
        budget_verdict = provider_request_budget_verdict(
            request_metrics=request_metrics,
            maximum_provider_request_chars=config.request_budget.maximum_provider_request_chars,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.INVALID_STRUCTURED_RESPONSE.value,
            provider_failure=str(exc),
        )
    if budget_verdict["status"] != "ALLOW":
        return ContentRepairResult(
            answer=None, call_record=None, provider_initialization_count=0,
            provider_failure_code=ProviderFailureCode.BUDGET_EXHAUSTED.value,
            provider_failure="content repair request exceeds the governed G20 budget",
        )
    try:
        provider = provider_factory(policy)
    except L2ProviderError as failure:
        return ContentRepairResult(
            answer=None,
            call_record=None,
            provider_initialization_count=1,
            provider_failure_code=failure.code.value,
            provider_failure=failure.safe_message,
        )
    except Exception:
        return ContentRepairResult(
            answer=None,
            call_record=None,
            provider_initialization_count=1,
            provider_failure_code=ProviderFailureCode.PROVIDER_UNAVAILABLE.value,
            provider_failure="content repair provider could not be initialized",
        )
    started = time.perf_counter()
    response: L2ProviderResponse | None = None
    failure: L2ProviderError | None = None
    try:
        response = provider.generate(request)
        if response.model != policy.model:
            failure = L2ProviderError(
                ProviderFailureCode.UNSUPPORTED_MODEL,
                "content repair response model does not match the pinned most-complex model",
            )
    except L2ProviderError as exc:
        failure = exc
    except Exception:
        failure = L2ProviderError(
            ProviderFailureCode.PROVIDER_UNAVAILABLE,
            "content repair provider failed without a governed response",
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    call_record = build_call_record(
        model_policy=policy,
        model_profile="enhanced",
        purpose=CallPurpose.CONTENT_REPAIR,
        ordinal=prior_model_calls + 1,
        response=response,
        latency_ms=response.latency_ms if response is not None else latency_ms,
        failure=failure,
    )
    if failure is not None or response is None:
        return ContentRepairResult(
            answer=None,
            call_record=call_record,
            provider_initialization_count=1,
            provider_failure_code=(
                failure.code.value
                if failure is not None
                else ProviderFailureCode.PROVIDER_UNAVAILABLE.value
            ),
            provider_failure=(
                failure.safe_message
                if failure is not None
                else "content repair provider did not return a governed response"
            ),
        )
    repaired_schema = validate_decision_model_output(
        response.output,
        evidence_packet=evidence_packet,
        expected_query_type=decision.query_type,
    )
    if not repaired_schema.valid or repaired_schema.value is None:
        return ContentRepairResult(
            answer=None,
            call_record=call_record,
            provider_initialization_count=1,
            provider_failure_code=ProviderFailureCode.INVALID_STRUCTURED_RESPONSE.value,
            provider_failure="content repair output failed deterministic structured validation",
        )
    deficiencies = evaluate_semantic_quality(
        question=question,
        plan=plan,
        evidence_packet=evidence_packet,
        answer=repaired_schema.value,
    )
    if deficiencies:
        return ContentRepairResult(
            answer=None,
            call_record=call_record,
            provider_initialization_count=1,
            provider_failure_code=ProviderFailureCode.SEMANTIC_QUALITY_GATE_FAILED.value,
            provider_failure="content repair output failed deterministic semantic-quality gates",
        )
    return ContentRepairResult(
        answer=repaired_schema.value,
        call_record=call_record,
        provider_initialization_count=1,
    )

