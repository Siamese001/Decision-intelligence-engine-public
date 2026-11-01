"""Quality review stage execution and concurrency orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.core import write_json
from dual_pipeline.delivery_quality import evaluate_delivery_quality
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.l1_review_contracts import L1ReviewVerdict
from dual_pipeline.query_core.reasoning import build_x1_gate
from dual_pipeline.x1_quality_core import (
    QualityProviderFactory,
    QualityReview,
    QualityRole,
    X1QualityConfig,
    X1QualityMode,
    run_concurrent_quality_reviews,
    run_quality_review,
)

__all__ = [
    "build_and_evaluate_deterministic_x1",
    "execute_challenger_and_concurrent_judge",
    "resolve_initial_judge_review",
]


def build_and_evaluate_deterministic_x1(
    *,
    answer: Mapping[str, Any] | None,
    packet: Mapping[str, Any],
    retrieval_evidence_report: Mapping[str, Any],
    claim_evidence_map: Mapping[str, Any],
    graph_hash_before: str,
    graph_path: Path,
    prompt: Mapping[str, Any] | None,
    l2_decision_artifact: Mapping[str, Any],
    cached_output: Mapping[str, Any] | None,
    cache_lookup: Mapping[str, Any] | None,
    coverage_manifest: Mapping[str, Any] | None,
    connected_evidence_map: Mapping[str, Any] | None,
    run_id: str,
    provider_failure_code: str | None,
    destination_decision: Any,
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], bool, str]:
    """Build X1 deterministic gate, evaluate delivery quality, and return results."""
    graph_hash_after_reasoning = cached_sha256_file(graph_path)
    x1 = build_x1_gate(
        answer=answer,
        packet=packet,
        retrieval_evidence_report=retrieval_evidence_report,
        claim_evidence_map=claim_evidence_map,
        graph_hash_before=graph_hash_before,
        graph_hash_after_reasoning=graph_hash_after_reasoning,
        prompt=prompt,
        l2_decision_digest=l2_decision_artifact["canonical_digest"]["value"],
        cached_output=cached_output,
        cache_lookup=cache_lookup,
        coverage_manifest=coverage_manifest,
        connected_evidence_map=connected_evidence_map,
        run_id=run_id,
        provider_failure_code=provider_failure_code,
    )
    delivery_quality_decision = evaluate_delivery_quality(
        run_id=run_id,
        answer=answer,
        delivery_profile=destination_decision.delivery_profile,
        artifact_type=destination_decision.artifact_type,
        coverage_complete=x1["coverage_complete"],
    )
    write_json(run_dir / "delivery_quality_decision.json", delivery_quality_decision)
    x1["delivery_quality"] = delivery_quality_decision
    if delivery_quality_decision["status"] != "PASS":
        x1["status"] = "FAIL"
    deterministic_x1_passed = x1["status"] == "PASS"
    return x1, delivery_quality_decision, deterministic_x1_passed, graph_hash_after_reasoning


def execute_challenger_and_concurrent_judge(
    *,
    quality_config: X1QualityConfig,
    assurance_profile: str,
    question: str,
    answer: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any],
    challenger_controls: Mapping[str, Any],
    quality_provider_factory: QualityProviderFactory | None,
    packet: Mapping[str, Any],
    retrieval_evidence_report: Mapping[str, Any],
    claim_evidence_map: Mapping[str, Any],
    graph_hash_before: str,
    graph_path: Path,
    prompt: Mapping[str, Any] | None,
    l2_decision_artifact: Mapping[str, Any],
    cached_output: Mapping[str, Any] | None,
    cache_lookup: Mapping[str, Any] | None,
    coverage_manifest: Mapping[str, Any] | None,
    connected_evidence_map: Mapping[str, Any] | None,
    run_id: str,
    provider_failure_code: str | None,
    destination_decision: Any,
    run_dir: Path,
) -> tuple[
    QualityReview,
    QualityReview | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    bool,
    str | None,
    bool,
]:
    """Execute quality reviews for challenger stage.

    When enhanced assurance is active, pre-computes deterministic X1 gate and dispatches
    Challenger and Judge concurrently, returning:
    (challenger_review, initial_judge_review, x1, delivery_quality, deterministic_x1_passed, graph_hash_after, is_concurrent).
    """
    if (
        assurance_profile == "enhanced"
        and quality_config.mode is not X1QualityMode.OFF
        and answer is not None
    ):
        x1, dq, x1_passed, gh_after = build_and_evaluate_deterministic_x1(
            answer=answer,
            packet=packet,
            retrieval_evidence_report=retrieval_evidence_report,
            claim_evidence_map=claim_evidence_map,
            graph_hash_before=graph_hash_before,
            graph_path=graph_path,
            prompt=prompt,
            l2_decision_artifact=l2_decision_artifact,
            cached_output=cached_output,
            cache_lookup=cache_lookup,
            coverage_manifest=coverage_manifest,
            connected_evidence_map=connected_evidence_map,
            run_id=run_id,
            provider_failure_code=provider_failure_code,
            destination_decision=destination_decision,
            run_dir=run_dir,
        )
        c_rev, j_rev = run_concurrent_quality_reviews(
            config=quality_config,
            assurance_profile=assurance_profile,
            question=question,
            answer=answer,
            evidence_packet=evidence_packet,
            challenger_controls=challenger_controls,
            judge_controls=x1,
            provider_factory=quality_provider_factory,
            judge_prerequisite_passed=x1_passed,
        )
        return c_rev, j_rev, x1, dq, x1_passed, gh_after, True

    challenger_review = run_quality_review(
        role=QualityRole.CHALLENGER,
        config=quality_config,
        assurance_profile=assurance_profile or "standard",
        question=question,
        answer=answer,
        evidence_packet=evidence_packet,
        deterministic_controls=challenger_controls,
        provider_factory=quality_provider_factory,
        prerequisite_passed=(answer is not None and assurance_profile == "enhanced"),
    )
    return challenger_review, None, None, None, False, None, False


def resolve_initial_judge_review(
    *,
    initial_judge_review: QualityReview | None,
    quality_config: X1QualityConfig,
    assurance_profile: str,
    question: str,
    answer: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any],
    deterministic_controls: Mapping[str, Any],
    challenger_review_artifact: Mapping[str, Any] | None,
    quality_provider_factory: QualityProviderFactory | None,
    deterministic_x1_passed: bool,
    l1_post_l2_review: Mapping[str, Any] | None,
    claim_evidence_map: Mapping[str, Any],
) -> QualityReview:
    """Resolve initial judge review, using concurrent result if available, or early-exit, or sequential fallback."""
    if initial_judge_review is not None:
        return initial_judge_review

    l1_passed = (
        l1_post_l2_review is None
        or l1_post_l2_review.get("verdict") == L1ReviewVerdict.SUFFICIENT.value
    )
    all_grounded = all(
        row.get("status") == "grounded"
        for row in claim_evidence_map.get("claims", [])
    )
    if (
        assurance_profile != "enhanced"
        and quality_config.mode != X1QualityMode.ENFORCED
        and l1_passed
        and all_grounded
        and deterministic_x1_passed
    ):
        return run_quality_review(
            role=QualityRole.JUDGE,
            config=quality_config,
            assurance_profile=assurance_profile or "standard",
            question=question,
            answer=answer,
            evidence_packet=evidence_packet,
            deterministic_controls=deterministic_controls,
            challenger_review=challenger_review_artifact,
            provider_factory=quality_provider_factory,
            prerequisite_passed=deterministic_x1_passed,
            skip_reason="EARLY_EXIT_CLEAN_L1_PASS",
        )

    return run_quality_review(
        role=QualityRole.JUDGE,
        config=quality_config,
        assurance_profile=assurance_profile or "standard",
        question=question,
        answer=answer,
        evidence_packet=evidence_packet,
        deterministic_controls=deterministic_controls,
        challenger_review=challenger_review_artifact,
        provider_factory=quality_provider_factory,
        prerequisite_passed=deterministic_x1_passed,
    )
