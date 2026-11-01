"""End-to-end lifecycle verification for NEE component repair and selective advisory depth (Wave 7).

Verifies:
1. An appendix-only failure changes only that component (output_2:appendix); protected sections remain invariant.
2. Approved upstream corrections invalidate exactly their dependents (e.g. speaker correction).
3. Mixed easy/hard candidate packets cover every candidate exactly once while allocating advisory effort only to useful subsets.
4. Restart cannot reset attempts or reuse stale approval.
5. Speaker ambiguity remains human-controlled; advisory never changes deterministic authority/disposition.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    get_prior_attempt_count,
    verify_approval_not_stale,
)
from dual_pipeline.ingestion_reasoning_core.advisory import (
    build_ingestion_reasoning_packet,
    ingestion_proposal_output_schema,
    validate_ingestion_proposal_output,
)
from dual_pipeline.ingestion_reasoning_core.models import (
    IngestionProviderRequest,
    IngestionProviderResponse,
    ProviderFactory,
)
from dual_pipeline.ingestion_reasoning_core.policy import load_ingestion_reasoning_policy
from dual_pipeline.ingestion_reasoning_core.selective import (
    build_deterministic_recommendation,
    merge_advisory_and_deterministic_proposals,
    partition_candidates_by_complexity,
)
from dual_pipeline.ingestion_reasoning_core.service import IngestionReasoningService
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentPlan,
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    compute_section_hashes,
)


class MockRecorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class MockProvider:
    def __init__(self, responses_by_request: Dict[str, Any] | None = None) -> None:
        self.calls: List[IngestionProviderRequest] = []
        self.responses_by_request = responses_by_request or {}

    def generate(self, request: IngestionProviderRequest) -> IngestionProviderResponse:
        self.calls.append(request)
        if request.packet:
            self.primary_packet = request.packet
            candidates = request.packet.get("candidates", [])
        else:
            candidates = getattr(self, "primary_packet", {}).get("candidates", [])

        output = {
            "recommendations": [
                {
                    "candidate_id": c["candidate_id"],
                    "recommendation": (
                        "HITL_REQUIRED"
                        if c.get("entity_resolution_outcomes")
                        else "CANDIDATE"
                    ),
                    "evidence_ids": sorted(c["evidence_ids"]),
                    "reason_codes": ["EVIDENCE_CONFIRMED"],
                    "reviewer_question": (
                        "Confirm the governed identity."
                        if c.get("entity_resolution_outcomes")
                        else None
                    ),
                }
                for c in candidates
            ],
            "overall_review_priority": "medium",
            "summary_reason_codes": ["SELECTIVE_ADVISORY_DEPTH"],
        }
        output_bytes = json.dumps(output, sort_keys=True).encode("utf-8")
        resp_digest = hashlib.sha256(output_bytes).hexdigest()
        return IngestionProviderResponse(
            output=output,
            response_digest=resp_digest,
            requested_model="gpt-5.6-luna",
            delivered_model="gpt-5.6-luna",
            input_tokens=150,
            cached_input_tokens=50,
            output_tokens=80,
            total_tokens=230,
            latency_ms=120.0,
        )


def _build_test_bundle() -> Dict[str, Any]:
    return {
        "run_id": "run-w7-e2e-01",
        "trace_id": "a" * 32,
        "source_id": "src-w7-01",
        "source_version_id": "v1.0",
        "canonical_digest": {"value": "b" * 64},
        "candidates": [
            {
                "candidate_id": "cand-hard-01",
                "candidate_type": "risk",
                "materiality": "high",
                "normalized_text": "High materiality contract risk regarding SLA liabilities.",
                "evidence_ids": ["ev-01"],
                "classification": "confidential",
                "entity_mentions": [{"mention_id": "m-01", "text": "Apex Global"}],
            },
            {
                "candidate_id": "cand-hard-02",
                "candidate_type": "decision",
                "materiality": "medium",
                "normalized_text": "Operational decision with ambiguous speaker attribution.",
                "evidence_ids": ["ev-02"],
                "classification": "confidential",
                "entity_mentions": [{"mention_id": "m-02", "text": "Bob"}],
            },
            {
                "candidate_id": "cand-easy-01",
                "candidate_type": "assertion",
                "materiality": "low",
                "normalized_text": "Standard sprint cadence is two weeks.",
                "evidence_ids": ["ev-03"],
                "classification": "internal",
                "entity_mentions": [],
            },
            {
                "candidate_id": "cand-easy-02",
                "candidate_type": "assertion",
                "materiality": "low",
                "normalized_text": "Sprint retro scheduled for Thursday.",
                "evidence_ids": ["ev-04"],
                "classification": "internal",
                "entity_mentions": [],
            },
        ],
    }


def _build_test_dependencies() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    resolutions = [
        {"mention_id": "m-01", "outcome": "AUTO_LINK_EXACT"},
        {"mention_id": "m-02", "outcome": "HITL_CONFIRM_ENTITY"},
    ]
    comparison = {
        "comparisons": {
            "cand-hard-01": {"exact_matches": [], "semantic_matches": [{"record_id": "rec-01", "lane": "candidate", "score": 0.85}]},
            "cand-hard-02": {"exact_matches": [], "semantic_matches": []},
            "cand-easy-01": {"exact_matches": [], "semantic_matches": []},
            "cand-easy-02": {"exact_matches": [{"record_id": "rec-dup", "lane": "approved", "score": 1.0}], "semantic_matches": []},
        }
    }
    governed_context = {"project": "DI-Core", "generation_id": "gen-01"}
    routes = [
        {"subject_id": "cand-hard-01", "intake_route": "NEW_ASSERTION"},
        {"subject_id": "cand-hard-02", "intake_route": "NEW_ASSERTION"},
        {"subject_id": "cand-easy-01", "intake_route": "NEW_ASSERTION"},
        {"subject_id": "cand-easy-02", "intake_route": "NEW_ASSERTION"},
    ]
    return resolutions, comparison, governed_context, routes


class TestNEESelectiveReasoningE2E:
    """E2E verification for Wave 7 NEE component repair and selective reasoning."""

    def test_appendix_only_repair_preserves_protected_sections_e2e(self):
        """Criterion 1: An appendix-only failure changes only that component; protected sections remain invariant."""
        initial_f2 = (
            "# Executive Summary\n\n"
            "## Header and Overview\n"
            "This document summarizes the engineering sprint.\n\n"
            "## Key Updates and Technical Substance\n"
            "The auth migration completed successfully with p99 latency under 50ms.\n\n"
            "## Action Items and Ownership\n"
            "- Bob to finalize documentation by Friday.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "Initial draft appendix notes.\n"
        )
        baseline_hashes = compute_section_hashes(initial_f2)
        planner = ComponentRetryPlanner()

        # Generate scoped repair plan
        plan = planner.plan_appendix_retry(
            initial_f2,
            attempt=1,
            defect_message="Appendix word count below floor (50/200 words)",
        )

        assert plan.target_component == "output_2:appendix"
        assert plan.retry_class == RetryClass.CONTENT_REPAIR
        assert "output_1" in plan.protected_paths
        assert "output_2:header_and_overview" in plan.protected_paths
        assert "output_2:key_updates_technical_substance" in plan.protected_paths
        assert plan.defect_signature == "output_2:appendix:appendix word count below floor (50/200 words)"

        # Simulate repair modifying ONLY the appendix
        repaired_f2 = (
            "# Executive Summary\n\n"
            "## Header and Overview\n"
            "This document summarizes the engineering sprint.\n\n"
            "## Key Updates and Technical Substance\n"
            "The auth migration completed successfully with p99 latency under 50ms.\n\n"
            "## Action Items and Ownership\n"
            "- Bob to finalize documentation by Friday.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "Expanded appendix detailing stakeholder perspectives from architecture, operations, "
            "and security teams regarding migration risks and mitigation strategies.\n"
        )
        current_hashes = compute_section_hashes(repaired_f2)

        # Protected component invariance check must pass with zero violations
        is_invariant, violations = planner.verify_protected_component_invariance(plan, current_hashes)
        assert is_invariant is True, f"Invariance violations: {violations}"
        assert len(violations) == 0

        # Outcome event bridged to learning adapter
        evt = AgentOutcomeAdapter.build_component_repair_event(
            target_component=plan.target_component,
            retry_class=plan.retry_class.value,
            defect_message=plan.defect_message or "",
            was_successful=True,
            attempt=1,
        )
        assert evt.outcome_type == OutcomeType.COMPONENT_REPAIR
        assert evt.status == "PASS"

    def test_approved_upstream_speaker_correction_invalidates_dependents_e2e(self):
        """Criterion 2: Approved upstream corrections invalidate exactly their dependents."""
        planner = ComponentRetryPlanner()

        dep_plan = planner.plan_dependency_invalidation(
            event_type="speaker_correction",
            changed_artifact="output_1",
            detail="Speaker assigned to Bob reclassified to Charlie",
            attempt=2,
        )

        assert dep_plan.retry_class == RetryClass.DEPENDENCY_REVALIDATION
        assert dep_plan.target_component == "output_1:speakers"
        # Dependent criteria exactly match downstream attributions
        expected_affected = [
            "output_2:dimension_2_technical_substance",
            "output_2:dimension_3_strategic_synthesis",
            "output_2:appendix_stakeholder_perspectives",
            "output_3:entities",
            "output_3:decisions",
        ]
        assert dep_plan.affected_criteria == expected_affected
        assert "Transcript speaker correction invalidates" in (dep_plan.dependency_reason or "")

    def test_mixed_easy_hard_candidate_packets_selective_allocation_e2e(self):
        """Criterion 3: Mixed easy/hard candidate packets cover every candidate exactly once while allocating advisory effort only to useful subsets."""
        bundle = _build_test_bundle()
        resolutions, comparison, governed_context, routes = _build_test_dependencies()
        policy_path = Path("requirements/ingestion-reasoning-policy.json")
        policy = load_ingestion_reasoning_policy(policy_path)

        # 1. Candidate partitioning
        hard_cands, easy_cands = partition_candidates_by_complexity(
            bundle=bundle,
            resolutions=resolutions,
            comparison=comparison,
            policy=policy,
        )
        assert len(hard_cands) == 2  # cand-hard-01 (high materiality) and cand-hard-02 (ambiguous mention m-02)
        assert len(easy_cands) == 2  # cand-easy-01 and cand-easy-02
        assert {c["candidate_id"] for c in hard_cands} == {"cand-hard-01", "cand-hard-02"}
        assert {c["candidate_id"] for c in easy_cands} == {"cand-easy-01", "cand-easy-02"}

        # 2. Build advisory subpacket containing only hard candidates
        decision = {
            "decision_id": "dec-test-01",
            "canonical_digest": {"value": "d" * 64},
        }
        subpacket = build_ingestion_reasoning_packet(
            decision=decision,
            bundle=bundle,
            resolutions=resolutions,
            comparison=comparison,
            governed_context=governed_context,
            routes=routes,
            candidate_ids={c["candidate_id"] for c in hard_cands},
        )
        assert len(subpacket["candidates"]) == 2
        assert {c["candidate_id"] for c in subpacket["candidates"]} == {"cand-hard-01", "cand-hard-02"}

        # 3. Model execution for subpacket only
        mock_provider = MockProvider()
        factory = lambda mp: mock_provider
        service = IngestionReasoningService(
            policy=policy,
            mode="advisory",
            provider_factory=factory,
            enable_selective_advisory=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            plan = {"canonical_digest": {"value": "a" * 64}}
            recorder = MockRecorder()
            bundle["enable_selective_advisory"] = True
            result = service.execute(
                run_dir=run_dir,
                recorder=recorder,
                bundle=bundle,
                plan=plan,
                resolutions=resolutions,
                comparison=comparison,
                governed_context=governed_context,
                routes=routes,
            )
            assert result["status"] == "ADVISORY_READY"
            assert "selective_reasoning" in result
            assert result["selective_reasoning"]["hard_candidates_count"] == 2
            assert result["selective_reasoning"]["easy_candidates_count"] == 2
            assert result["selective_reasoning"]["subpacket_used"] is True

            proposal_file = run_dir / "ingestion_reasoning_proposal.json"
            assert proposal_file.is_file()
            proposal_data = json.loads(proposal_file.read_text(encoding="utf-8"))

            # Crucial assertion: Proposal covers EVERY candidate from original bundle exactly once
            recs = proposal_data["recommendations"]
            assert len(recs) == 4
            covered_ids = {r["candidate_id"] for r in recs}
            assert covered_ids == {"cand-hard-01", "cand-hard-02", "cand-easy-01", "cand-easy-02"}

            # Verify deterministic routing reason codes on easy candidates
            easy_01_rec = next(r for r in recs if r["candidate_id"] == "cand-easy-01")
            assert easy_01_rec["recommendation"] == "CANDIDATE"
            assert "DETERMINISTIC_ROUTED" in easy_01_rec["reason_codes"]

            easy_02_rec = next(r for r in recs if r["candidate_id"] == "cand-easy-02")
            assert easy_02_rec["recommendation"] == "REJECTED_NOOP"
            assert "DETERMINISTIC_DUPLICATE_NOOP" in easy_02_rec["reason_codes"]

    def test_restart_preserves_cumulative_attempts_and_rejects_stale_approval_e2e(self):
        """Criterion 4: Restart cannot reset attempts or reuse stale approval."""
        budget = RetryBudget(max_total_attempts=3, total_attempts=2, content_repairs=1)
        planner = ComponentRetryPlanner(budget=budget)

        # 1. Budget serialization across restart
        budget_dict = budget.to_dict()
        restarted_planner = ComponentRetryPlanner()
        restarted_planner.restore_budget(budget_dict)

        assert restarted_planner.budget.total_attempts == 2
        can_attempt, _ = restarted_planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
        assert can_attempt is True  # 1 remaining before max 3

        restarted_planner.budget.record_attempt(RetryClass.CONTENT_REPAIR)
        assert restarted_planner.budget.total_attempts == 3

        # Exceeding budget stops further repairs
        exhausted, reason = restarted_planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
        assert exhausted is False
        assert "Global retry budget exhausted" in reason

        # 2. Reject stale approval reuse
        valid_digest = hashlib.sha256(b"original_deliverables_v1").hexdigest()
        plan = ComponentPlan(
            retry_class=RetryClass.CONTENT_REPAIR,
            target_component="output_2:appendix",
            writable_paths=[],
            protected_paths=[],
            affected_criteria=[],
            approval_binding_digest=valid_digest,
        )

        # Same deliverables -> approval fresh
        is_fresh, _ = restarted_planner.validate_approval_freshness(plan, valid_digest)
        assert is_fresh is True

        # Modified deliverables -> stale approval rejected fail-closed
        tampered_digest = hashlib.sha256(b"modified_deliverables_v2").hexdigest()
        is_fresh_bad, reason_bad = restarted_planner.validate_approval_freshness(plan, tampered_digest)
        assert is_fresh_bad is False
        assert "STALE_APPROVAL_DETECTED" in reason_bad

        # Helper also raises ValueError
        meta = {
            "approval_receipt": {"proposal_digest": valid_digest},
            "is_stale_approval": False,
        }
        with pytest.raises(ValueError, match="STALE_APPROVAL_REUSED"):
            verify_approval_not_stale(meta, tampered_digest)

    def test_speaker_ambiguity_remains_human_controlled_e2e(self):
        """Criterion 5: Speaker ambiguity remains human-controlled; advisory never changes deterministic authority/disposition."""
        bundle = _build_test_bundle()
        resolutions, comparison, _, _ = _build_test_dependencies()

        # cand-hard-02 has mention m-02 with AMBIGUOUS_NAME_VARIANT
        # Even if the advisory model outputs "CANDIDATE", the merge logic forces it to HITL_REQUIRED
        advisory_recs = [
            {
                "candidate_id": "cand-hard-01",
                "recommendation": "CANDIDATE",
                "evidence_ids": ["ev-01"],
                "reason_codes": ["EVIDENCE_CONFIRMED"],
                "reviewer_question": None,
            },
            {
                "candidate_id": "cand-hard-02",
                "recommendation": "CANDIDATE",  # Model attempted to bypass HITL!
                "evidence_ids": ["ev-02"],
                "reason_codes": ["EVIDENCE_CONFIRMED"],
                "reviewer_question": None,
            },
        ]
        deterministic_cands = [c for c in bundle["candidates"] if c["candidate_id"].startswith("cand-easy")]

        merged = merge_advisory_and_deterministic_proposals(
            advisory_recommendations=advisory_recs,
            deterministic_candidates=deterministic_cands,
            all_bundle_candidates=bundle["candidates"],
            resolutions=resolutions,
            comparison=comparison,
        )

        hard_02_rec = next(r for r in merged if r["candidate_id"] == "cand-hard-02")
        # Invariant enforced: Recommendation forced to HITL_REQUIRED
        assert hard_02_rec["recommendation"] == "HITL_REQUIRED"
        assert "SPEAKER_AMBIGUITY_HITL_REQUIRED" in hard_02_rec["reason_codes"]
