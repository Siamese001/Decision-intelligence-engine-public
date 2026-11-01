"""Wave 2: UWG staging promotion returns a passing physical proof (and fails closed without one).

Validation helpers are patched out because they need real BGE evaluation artifacts (those
suites are gated behind NEE_RUN_BGE_TESTS). The write path itself - the SQL UPDATE,
audit insert, commit, checkpoint, and the proof - runs for real on a temp graph.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tests.end_to_end.test_wave1_mutation_hardening_e2e as _wave1_fixture
from dual_pipeline.graph_mutation_proof import GraphMutationProofError
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.uwg import UniversalWriteGateway

TARGET = "as-wave2-uwg-01"


@pytest.fixture()
def staged(monkeypatch):
    case = _wave1_fixture.Wave1MutationHardeningE2ETests("test_e2e_mutation_wal_checkpoint_and_hash_change")
    case.setUp()
    GovernedGraphWriter(case.graph_path).apply(case._make_change_set("op-wave2-uwg-01", TARGET))
    graph: Path = case.graph_path
    gateway = object.__new__(UniversalWriteGateway)
    monkeypatch.setattr(gateway, "_require_fresh_staging_graph", lambda **_: graph, raising=False)
    monkeypatch.setattr(gateway, "_validate_evaluation_policy", lambda _e: None, raising=False)
    monkeypatch.setattr(gateway, "_validate_decision_scope", lambda *_a: [TARGET], raising=False)
    monkeypatch.setattr(
        gateway,
        "_validate_evidence_bindings",
        lambda *_a: {"approval_reference": "ref-e2e-001", "bindings": {TARGET: ["ev-e2e-001"]}},
        raising=False,
    )
    monkeypatch.setattr(gateway, "manager", type("M", (), {"_resolve": staticmethod(lambda p: graph)})(), raising=False)
    try:
        yield case, gateway
    finally:
        case.tearDown()


def _call(gateway):
    return gateway._stage_graph_promotion(
        generation_id="gen-test",
        expected_active={"graph_path": "x"},
        candidate_ids=[TARGET],
        evaluation={"evaluation_id": "eval-1", "canonical_digest": {"value": "e" * 64}},
        decision={"promotion_id": "promo-1", "origin_run_id": "run-1", "canonical_digest": {"value": "d" * 64}},
    )


def test_uwg_promotion_returns_passing_physical_proof(staged):
    case, gateway = staged
    result = _call(gateway)
    assert result["status"] == "COMMITTED"
    assert result["physical_proof"]["overall"] == "PASS"
    assert result["physical_proof"]["canonical_deltas"]["canonical_assertions"] == 1
    assert Path(result["physical_proof"]["receipt_path"]).is_file()
    assert result["physical_proof"]["receipt_path"].count("uwg-promotion_promo-1") == 1


def test_uwg_promotion_fails_closed_when_proof_cannot_pass(staged, monkeypatch):
    case, gateway = staged
    import dual_pipeline.uwg_core.gateway as gw

    class _FailGuard(gw.MutationGuard):
        def finalize(self, record_ids, audit_events, **kwargs):
            return super().finalize(record_ids=["never-written"], audit_events=audit_events, **kwargs)

    monkeypatch.setattr(gw, "MutationGuard", _FailGuard)
    with pytest.raises(GraphMutationProofError) as err:
        _call(gateway)
    assert "PHYSICAL_PROOF_FAILED" in str(err.value)
