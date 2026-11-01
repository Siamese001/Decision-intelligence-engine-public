#!/usr/bin/env python3
"""
Unit tests for Pipeline C Retrieval & Copilot Fusion (`pipeline_c_retrieval.py`).
Verifies:
  - Law 6: Policy Before Retrieval (Pre-FAISS/SQL filtering, dropping quarantined nodes)
  - Law 2: INTERNAL_ADVISORY governance (Stakeholder Dynamics, Decision Influence, Adoption Risk, Blockers)
  - Law 3: Untrusted Fast Path (Ambient live context flagged as unverified)
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from pipeline_c_retrieval import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CopilotFusionEngine,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
)


@pytest.fixture
def populated_engine():
    temp_dir = Path(tempfile.mkdtemp())
    mgr = DatabaseManager(
        base_dir=temp_dir,
        allow_unencrypted_dev_fallback=True,
    )

    # 1. Base Evidence
    ev1 = EvidenceRecord(
        evidence_id="ev_ret_01",
        meeting_id="meet_gov",
        speaker="Lead Architect",
        text="Adopted WAL journal mode and decoupled the compute fork.",
        start_timestamp=0.0
    )
    ev2 = EvidenceRecord(
        evidence_id="ev_ret_02",
        meeting_id="meet_gov",
        speaker="PM",
        text="Schedule calendar follow-up for tomorrow.",
        start_timestamp=5.0
    )
    mgr.append_evidence(ev1)
    mgr.append_evidence(ev2)

    # 2. Canonical Nodes (1 Strategic, 1 Quarantined Tactical)
    c_good = CanonicalGraphNode(
        node_id="c_wal_dec",
        evidence_id="ev_ret_01",
        type="ARCHITECTURAL_DECISION",
        name="WAL Journal Mode Adoption",
        strategic_value=9.0,
        tacticality=1.0,
        is_quarantined=False,
    )
    c_quarantined = CanonicalGraphNode(
        node_id="c_tact_todo",
        evidence_id="ev_ret_02",
        type="OPERATIONAL_TODO",
        name="Schedule Team Calendar Follow-up",
        strategic_value=1.5,
        tacticality=8.5,
        is_quarantined=True,
        quarantine_reason="TACTICAL_SUPPRESSION",
    )
    mgr.upsert_canonical_node(c_good)
    mgr.upsert_canonical_node(c_quarantined)

    # 3. Internal Advisory Node
    adv_blocker = InternalAdvisoryNode(
        node_id="adv_budget_blocker",
        evidence_id="ev_ret_01",
        type="BLOCKER",
        name="Budgetary Freeze Risk",
        category=AdvisoryCategory.BLOCKER,
        properties={"rationale": "VP hesitation around Q4 cloud spend."},
        strategic_value=8.5,
        stealth_score=1.0,
        namespace=INTERNAL_ADVISORY_NAMESPACE,
    )
    mgr.upsert_internal_advisory_node(adv_blocker)

    retrieval = PolicyGatedRetrievalEngine(db_manager=mgr)
    fusion = CopilotFusionEngine(retrieval_engine=retrieval)

    yield retrieval, fusion, mgr
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_policy_before_retrieval_quarantine_filter(populated_engine):
    """Verify Law 6: Quarantined nodes are never returned in search."""
    retrieval, _, _ = populated_engine

    # Search for "calendar" or "follow-up" which is in quarantined node
    results = retrieval.search(query="follow-up", stealth_mode=True, include_quarantined=False)
    # Must be empty because the matching node is soft-quarantined!
    assert len(results) == 0

    # Search for "WAL" which is in valid node
    results_good = retrieval.search(query="WAL", stealth_mode=True, include_quarantined=False)
    assert len(results_good) == 1
    assert results_good[0].node_id == "c_wal_dec"
    assert results_good[0].namespace == CLIENT_SAFE_NAMESPACE


def test_stealth_mode_isolation(populated_engine):
    """Verify Law 2 & 6: Stealth mode returns ONLY CLIENT_SAFE."""
    retrieval, _, _ = populated_engine

    # 1. Stealth Mode = True
    results_stealth = retrieval.search(query="", stealth_mode=True)
    assert len(results_stealth) == 1
    assert results_stealth[0].namespace == CLIENT_SAFE_NAMESPACE

    # 2. Stealth Mode = False (Internal Advisory Unmasked)
    results_all = retrieval.search(query="", stealth_mode=False)
    assert len(results_all) == 2
    namespaces = {r.namespace for r in results_all}
    assert namespaces == {CLIENT_SAFE_NAMESPACE, INTERNAL_ADVISORY_NAMESPACE}


def test_copilot_context_fusion(populated_engine):
    """Verify Law 3: Live Fast Path context is fused with Settled Slow Path facts."""
    _, fusion, _ = populated_engine

    live_events = [
        {"speaker": "Amit Ayer", "text": "Are we still on track for WAL cutover?", "is_ephemeral": True}
    ]

    payload = fusion.fuse_context(
        query="WAL",
        live_fast_path_events=live_events,
        stealth_mode=True,
    )

    assert payload.policy_check_passed is True
    assert len(payload.reconciled_canonical_facts) == 1
    assert len(payload.reconciled_advisory_facts) == 0  # Stealth mode active
    assert len(payload.fast_path_context) == 1
    assert "Amit Ayer" in payload.executive_synthesis
    assert "Settled Architecture Baseline" in payload.executive_synthesis
