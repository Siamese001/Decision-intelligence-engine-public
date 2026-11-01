"""Wave 3 E2E tests: LLM Judge verdict integrity, Intake A2A fail-closed, and Query/Cache safety."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from dual_pipeline.decision_service_core.service import DecisionService
from dual_pipeline.llm_judge_core.judges.recap import (
    _apply_defect_penalties,
    _finalize_envelope,
)
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler
from dual_pipeline.query_router import QueryDestination
from dual_pipeline.semantic_cache_core.merkle_cache import (
    MerkleSemanticCacheBoundary,
    _parse_candidate_date,
    extract_earliest_stale_after,
)


def test_recap_judge_preserves_fail_verdict_on_incomplete_span_and_defect_penalty() -> None:
    """Truncated or penalized recaps must never be overwritten back to PASS when score >= 90."""
    raw_truncated = {
        "dimension_1_executive_altitude": 24,
        "dimension_2_technical_substance": 24,
        "dimension_3_strategic_synthesis": 24,
        "dimension_4_scoping_and_modality": 24,
        "incomplete_evaluated_span": True,
        "verdict": "FAIL",
        "status": "FAIL",
        "forensic_rationale": "Incomplete evaluated span (coverage 62.0%).",
    }
    env = _finalize_envelope(raw_truncated)
    assert env["verdict"] == "FAIL"

    clean_f2 = (
        "# Meeting Objective\nAlign on architecture.\n\n"
        "# Key Discussion Points & Findings\nDetailed discussion.\n\n"
        "# Action & Governance Ledger\n"
        "No formal actions were established in this session; discussion remained informational.\n"
    )
    after_penalties = _apply_defect_penalties(env, clean_f2, "", f1_text="Amit: Let's review.")
    assert after_penalties["verdict"] == "FAIL"
    assert after_penalties["status"] == "FAIL"


def test_recap_judge_critique_envelope_unlocks_action_ledger_on_ledger_failure() -> None:
    """When Action & Governance Ledger fails integrity checks, critique_diff_envelope targets and unlocks it."""
    raw_card = {
        "dimension_1_executive_altitude": 24,
        "dimension_2_technical_substance": 24,
        "dimension_3_strategic_synthesis": 24,
        "dimension_4_scoping_and_modality": 24,
        "section_scores": {
            "header_and_overview": 9,
            "key_updates_technical_substance": 33,
            "conditional_extensions": 19,
            "appendix_stakeholder_perspectives": 33,
        },
    }
    env = _finalize_envelope(raw_card)
    bad_ledger_f2 = (
        "# Meeting Objective\nAlign on architecture.\n\n"
        "# Key Discussion Points & Findings\nDetailed discussion.\n\n"
        "# Action & Governance Ledger\n"
        "| Action ID | Owner (Single DRI + Role) | Specific Action | Target Timing |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| A-01 | Unassigned (Pending Owner Assignment) | Draft plan | TBD |\n"
    )
    res = _apply_defect_penalties(
        env,
        bad_ledger_f2,
        "",
        f1_text="**Amit:** [00:10] I will draft the migration plan by Friday.",
    )
    assert res["verdict"] == "FAIL"
    critique = res["critique_diff_envelope"]
    assert critique["target_path"] == "# Action & Governance Ledger"
    assert "# Action & Governance Ledger" not in critique["protected_paths"]
    assert all("Action & Governance Ledger" not in m for m in critique["forbidden_mutations"])


def test_judge_orchestrator_forwards_ref_artifact_text_to_recap_judge() -> None:
    """DecomposedMultiJudge.evaluate_deliverable must forward ref_artifact_text to judge_recap."""
    orch = DecomposedMultiJudge()
    captured = {}

    def _fake_eval(f2_text, f1_text, ref_artifact_text="", **kwargs):
        captured["ref_artifact_text"] = ref_artifact_text
        return {
            "verdict": "PASS",
            "status": "PASS",
            "composite_score": 94,
            "dimension_1_executive_altitude": 24,
            "dimension_2_technical_substance": 24,
            "dimension_3_strategic_synthesis": 23,
            "dimension_4_scoping_and_modality": 23,
            "forensic_rationale": "Clean.",
            "defect_codes": [],
        }

    orch.judge_recap.evaluate = _fake_eval  # type: ignore[method-assign]
    orch.evaluate_deliverable(
        "output_02",
        content="# Meeting Objective\nTest",
        reference_text="Amit: Test",
        ref_artifact_text="Slide 1: PostgreSQL pgvector architecture",
        mock=True,
    )
    assert captured["ref_artifact_text"] == "Slide 1: PostgreSQL pgvector architecture"


def test_reconciler_enforces_strict_mm_dd_yyyy_and_action_owner_entity_parity() -> None:
    """CrossArtifactReconciler must flag hyphenated dates without mutating metadata and check O2-to-O3 owners."""
    reconciler = CrossArtifactReconciler()
    metadata = {"date": "09-23-2026"}
    f2_text = (
        "# Action & Governance Ledger\n"
        "| Action ID | Owner | Action | Target Timing |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| A-01 | Ghost Person (Lead) | Ship pipeline | 09/30/2026 |\n"
    )
    f3_data = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "canonical_name": "Amit Ayer", "mutation": "UPSERT"}
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
    }
    res = reconciler.reconcile("Amit Ayer: Let's ship.", f2_text, f3_data, metadata)
    assert res["status"] == "FAIL"
    assert metadata["date"] == "09-23-2026"
    assert any("strict MM/DD/YYYY" in f for f in res["failures"])
    assert any("Ghost Person" in f for f in res["failures"])


def test_decision_service_fast_lookup_does_not_hijack_project_status_queries(tmp_path) -> None:
    """_try_fast_system_lookup must only run on exact_lookup queries without temporal/status intent."""
    svc = DecisionService(query_engine=MagicMock(), runtime_root=tmp_path)
    status_route = SimpleNamespace(
        destination=QueryDestination.GRAPH_EVIDENCE_UTILITY,
        query_type="project_status_current",
    )
    assert (
        svc._try_fast_system_lookup(
            status_route,
            "What is the current status of the LAMS integration?",
            "req-1",
            0.0,
        )
        is None
    )

    exact_route = SimpleNamespace(
        destination=QueryDestination.GRAPH_EVIDENCE_UTILITY,
        query_type="exact_lookup",
    )
    assert (
        svc._try_fast_system_lookup(
            exact_route,
            "What is the current status of LAMS?",
            "req-2",
            0.0,
        )
        is None
    )
    fast_hit = svc._try_fast_system_lookup(
        exact_route,
        "What is the system of record for LAMS?",
        "req-3",
        0.0,
    )
    assert fast_hit is not None
    assert fast_hit["result"]["query_type"] == "exact_lookup"


def test_merkle_cache_evicts_malformed_stale_after_entries() -> None:
    """Malformed stale_after strings must parse to date.min and fail closed as stale."""
    assert _parse_candidate_date("NOT-A-VALID-DATE") == date.min
    assert _parse_candidate_date(None) is None

    corrupt_payload = {
        "status": "COMPLETED",
        "nested_summary": {"stale_after": "CORRUPT_DATE_VALUE"},
    }
    earliest = extract_earliest_stale_after(corrupt_payload)
    assert earliest == "0001-01-01T23:59:59Z"

    fake_cache = MagicMock()
    fake_cache.mode = "ACTIVE"
    fake_cache.lookup.return_value = SimpleNamespace(
        mode="ACTIVE",
        hit=True,
        output=corrupt_payload,
        entry_id="entry-corrupt-01",
        similarity=0.99,
        outcome="COMPLETED",
        source_run_id="run-01",
        source_trace_id="trace-01",
        source_output_digest="dig-01",
    )
    boundary = MerkleSemanticCacheBoundary(fake_cache)
    res = boundary.lookup(context={}, intent_vector=[1.0, 0.0])
    assert res.hit is False
    assert res.reason == "CACHE_MISS_W3_TEMPORAL_EXPIRED"
