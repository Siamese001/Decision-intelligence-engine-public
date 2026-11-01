"""Review execution and decision runner for X1 quality controls."""
from __future__ import annotations

from typing import Any, Mapping

from .models import (
    QualityFailureCode,
    QualityProviderError,
    QualityProviderFactory,
    QualityProviderRequest,
    QualityReview,
    QualityReviewStatus,
    QualityRole,
    QualityVerdict,
    X1QualityConfig,
    X1QualityMode,
)
from .rubrics import _input_bindings, _parse_quality_output

__all__ = [
    "run_concurrent_quality_reviews",
    "run_quality_review",
]


def _skipped_review(
    *,
    role: QualityRole,
    config: X1QualityConfig,
    assurance_profile: str,
    eligible: bool,
    reason: str,
    input_bindings: Mapping[str, str],
    repair_attempt: int,
) -> QualityReview:
    return QualityReview(
        role=role,
        mode=config.mode,
        assurance_profile=assurance_profile,
        eligible=eligible,
        status=QualityReviewStatus.SKIPPED,
        verdict=QualityVerdict.NOT_RUN,
        skip_reason=reason,
        failure_code=None,
        provider=None,
        model=None,
        reasoning_effort=None,
        input_bindings=input_bindings,
        response_digest=None,
        defect_codes=(),
        evidence_refs=(),
        confidence=None,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0,
        repair_attempt=repair_attempt,
    )


def run_quality_review(
    *,
    role: QualityRole,
    config: X1QualityConfig,
    assurance_profile: str,
    question: str,
    answer: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any],
    deterministic_controls: Mapping[str, Any],
    challenger_review: Mapping[str, Any] | None = None,
    provider_factory: QualityProviderFactory | None = None,
    prerequisite_passed: bool = True,
    prior_calls_for_role: int = 0,
    repair_attempt: int = 0,
    skip_reason: str | None = None,
) -> QualityReview:
    """Run one bounded quality review or create a precise no-call receipt."""

    normalized_answer = dict(answer or {})
    bindings = _input_bindings(
        answer=normalized_answer,
        evidence_packet=evidence_packet,
        deterministic_controls=deterministic_controls,
        challenger_review=challenger_review,
    )
    bindings["quality_policy_digest"] = config.config_digest
    eligible = assurance_profile in config.eligible_assurance_profiles
    if skip_reason is not None:
        return _skipped_review(
            role=role,
            config=config,
            assurance_profile=assurance_profile,
            eligible=eligible,
            reason=skip_reason,
            input_bindings=bindings,
            repair_attempt=repair_attempt,
        )
    if config.mode is X1QualityMode.OFF:
        return _skipped_review(
            role=role,
            config=config,
            assurance_profile=assurance_profile,
            eligible=eligible,
            reason="QUALITY_MODE_OFF",
            input_bindings=bindings,
            repair_attempt=repair_attempt,
        )
    if not eligible:
        return _skipped_review(
            role=role,
            config=config,
            assurance_profile=assurance_profile,
            eligible=False,
            reason="ASSURANCE_NOT_ELIGIBLE",
            input_bindings=bindings,
            repair_attempt=repair_attempt,
        )
    if not prerequisite_passed or answer is None:
        return _skipped_review(
            role=role,
            config=config,
            assurance_profile=assurance_profile,
            eligible=True,
            reason="PREREQUISITE_NOT_PASSED",
            input_bindings=bindings,
            repair_attempt=repair_attempt,
        )
    model_policy = config.policy_for(role)
    if prior_calls_for_role >= model_policy.maximum_calls_per_run:
        return QualityReview(
            role=role,
            mode=config.mode,
            assurance_profile=assurance_profile,
            eligible=True,
            status=QualityReviewStatus.FAIL,
            verdict=QualityVerdict.ABSTAIN,
            skip_reason=None,
            failure_code=QualityFailureCode.CALL_CAP_EXHAUSTED.value,
            provider=model_policy.provider,
            model=model_policy.model,
            reasoning_effort=model_policy.reasoning_effort,
            input_bindings=bindings,
            response_digest=None,
            defect_codes=(),
            evidence_refs=(),
            confidence=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            repair_attempt=repair_attempt,
        )
    if not config.provider_egress_allowed:
        return QualityReview(
            role=role,
            mode=config.mode,
            assurance_profile=assurance_profile,
            eligible=True,
            status=QualityReviewStatus.FAIL,
            verdict=QualityVerdict.ABSTAIN,
            skip_reason=None,
            failure_code=QualityFailureCode.PROVIDER_EGRESS_NOT_AUTHORIZED.value,
            provider=model_policy.provider,
            model=model_policy.model,
            reasoning_effort=model_policy.reasoning_effort,
            input_bindings=bindings,
            response_digest=None,
            defect_codes=(),
            evidence_refs=(),
            confidence=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            repair_attempt=repair_attempt,
        )
    if provider_factory is None:
        return QualityReview(
            role=role,
            mode=config.mode,
            assurance_profile=assurance_profile,
            eligible=True,
            status=QualityReviewStatus.FAIL,
            verdict=QualityVerdict.ABSTAIN,
            skip_reason=None,
            failure_code=QualityFailureCode.PROVIDER_UNAVAILABLE.value,
            provider=model_policy.provider,
            model=model_policy.model,
            reasoning_effort=model_policy.reasoning_effort,
            input_bindings=bindings,
            response_digest=None,
            defect_codes=(),
            evidence_refs=(),
            confidence=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            repair_attempt=repair_attempt,
        )
    request = QualityProviderRequest(
        role=role,
        question=question,
        answer=normalized_answer,
        evidence_packet=evidence_packet,
        deterministic_controls=deterministic_controls,
        challenger_review=challenger_review,
        input_bindings=bindings,
        repair_attempt=repair_attempt,
    )
    provider_call_count = 0
    try:
        provider = provider_factory(model_policy)
        provider_call_count = 1
        response = provider.review(request)
        if response.model != model_policy.model:
            raise QualityProviderError(
                QualityFailureCode.INVALID_STRUCTURED_RESPONSE,
                "quality provider response model does not match the governed policy",
            )
        verdict, defects, evidence_refs, confidence = _parse_quality_output(
            response.output,
            evidence_packet=evidence_packet,
        )
    except QualityProviderError as failure:
        return QualityReview(
            role=role,
            mode=config.mode,
            assurance_profile=assurance_profile,
            eligible=True,
            status=QualityReviewStatus.FAIL,
            verdict=QualityVerdict.ABSTAIN,
            skip_reason=None,
            failure_code=failure.code.value,
            provider=model_policy.provider,
            model=model_policy.model,
            reasoning_effort=model_policy.reasoning_effort,
            input_bindings=bindings,
            response_digest=None,
            defect_codes=(),
            evidence_refs=(),
            confidence=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            repair_attempt=repair_attempt,
            provider_call_count=provider_call_count,
        )
    except Exception:
        return QualityReview(
            role=role,
            mode=config.mode,
            assurance_profile=assurance_profile,
            eligible=True,
            status=QualityReviewStatus.FAIL,
            verdict=QualityVerdict.ABSTAIN,
            skip_reason=None,
            failure_code=QualityFailureCode.PROVIDER_UNAVAILABLE.value,
            provider=model_policy.provider,
            model=model_policy.model,
            reasoning_effort=model_policy.reasoning_effort,
            input_bindings=bindings,
            response_digest=None,
            defect_codes=(),
            evidence_refs=(),
            confidence=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            repair_attempt=repair_attempt,
            provider_call_count=provider_call_count,
        )
    return QualityReview(
        role=role,
        mode=config.mode,
        assurance_profile=assurance_profile,
        eligible=True,
        status=QualityReviewStatus.PASS,
        verdict=verdict,
        skip_reason=None,
        failure_code=None,
        provider=model_policy.provider,
        model=response.model,
        reasoning_effort=model_policy.reasoning_effort,
        input_bindings=bindings,
        response_digest=response.response_digest,
        defect_codes=defects,
        evidence_refs=evidence_refs,
        confidence=confidence,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        total_tokens=response.usage.total_tokens,
        latency_ms=response.latency_ms,
        repair_attempt=repair_attempt,
        provider_call_count=provider_call_count,
    )


def run_concurrent_quality_reviews(
    *,
    config: X1QualityConfig,
    assurance_profile: str,
    question: str,
    answer: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any],
    challenger_controls: Mapping[str, Any],
    judge_controls: Mapping[str, Any],
    provider_factory: QualityProviderFactory | None = None,
    judge_prerequisite_passed: bool = True,
) -> tuple[QualityReview, QualityReview]:
    """Execute Challenger and Judge reviews concurrently in worker threads.

    Dispatches both reviews in parallel to reduce review stage latency from
    (T_challenger + T_judge) down to max(T_challenger, T_judge).
    Returns (challenger_review, judge_review).
    """
    import concurrent.futures

    def _challenger() -> QualityReview:
        return run_quality_review(
            role=QualityRole.CHALLENGER,
            config=config,
            assurance_profile=assurance_profile,
            question=question,
            answer=answer,
            evidence_packet=evidence_packet,
            deterministic_controls=challenger_controls,
            provider_factory=provider_factory,
            prerequisite_passed=True,
        )

    def _judge() -> QualityReview:
        return run_quality_review(
            role=QualityRole.JUDGE,
            config=config,
            assurance_profile=assurance_profile,
            question=question,
            answer=answer,
            evidence_packet=evidence_packet,
            deterministic_controls=judge_controls,
            challenger_review=None,
            provider_factory=provider_factory,
            prerequisite_passed=judge_prerequisite_passed,
        )

    import contextvars
    from config.request_deadline import remaining_timeout
    t_budget = remaining_timeout(180.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_c = executor.submit(contextvars.copy_context().run, _challenger)
        f_j = executor.submit(contextvars.copy_context().run, _judge)
        return f_c.result(timeout=t_budget), f_j.result(timeout=t_budget)
