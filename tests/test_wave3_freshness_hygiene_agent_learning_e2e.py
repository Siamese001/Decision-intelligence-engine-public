"""
tests/test_wave3_freshness_hygiene_agent_learning_e2e.py

End-to-end lifecycle verification for Wave 3: Graph Freshness Decay & Harm-Prioritized Hygiene Learning.
Tests:
1. RetrievalSentinel, FreshnessWarden, and AccuracyReviewer MAINTENANCE_FINDING telemetry.
2. RemediationJudge calibrated TTL decay proposal generation with approval gates (auto_apply=False).
3. HarmPrioritizedPlanner extracts QueryImpactRecord from answer_ledger.sqlite and ranks high-harm entities above clutter.
4. ContinuousMaintenanceSwarm performs bounded scan with zero database mutation (pre/post SHA-256 identical).
5. SynthesisJudgeAgent ranks candidate entity merges and stale-claim directives by query harm.
6. MetricHistorian records durable graph hygiene snapshots and emits MAINTENANCE_FINDING telemetry.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from dual_pipeline.graph_freshness_core.agents.detection import (
    AccuracyReviewer,
    FreshnessFindings,
    FreshnessWarden,
)
from dual_pipeline.graph_freshness_core.agents.historian import MetricHistorian
from dual_pipeline.graph_freshness_core.agents.judge import RemediationJudge
from dual_pipeline.graph_freshness_core.agents.sentinel import RetrievalSentinel
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy
from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
    extract_query_impact_records_from_ledger,
)
from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    GraphHygienePanel,
    SynthesisJudgeAgent,
)
from dual_pipeline.learning_adapters.agent_outcome_adapter import OutcomeType
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.maintenance_swarm import ContinuousMaintenanceSwarm


@pytest.fixture
def sandbox_env():
    """Provides an isolated sandbox environment with a mock knowledge graph and answer ledger."""
    with tempfile.TemporaryDirectory(prefix="wave3_e2e_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)

        # 1. Setup mock graph database
        graph_db = tmp_path / "sandbox_graph.db"
        conn = sqlite3.connect(graph_db)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS canonical_entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                recorded_at TEXT,
                superseded_by_assertion_id TEXT,
                currentness_state TEXT,
                validation_state TEXT,
                retrieval_eligible INTEGER,
                qualifiers_json TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evidence_item (
                evidence_id TEXT PRIMARY KEY,
                location_status TEXT,
                direct_quote TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_document (
                id TEXT PRIMARY KEY,
                content TEXT
            );
        """)

        # Populate graph fixtures: duplicate entities and stale assertions
        cur.executemany(
            "INSERT INTO canonical_entities (id, name) VALUES (?, ?)",
            [
                ("org:partner_advisory", "PartnerAdvisory Consulting"),
                ("org:partner_advisory_consulting", "PartnerAdvisory Consulting"),
                ("org:apex", "Apex Power & Grid"),
            ],
        )
        cur.executemany(
            "INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("asrt-1", "org:partner_advisory", "OPERATES_IN", "2020-01-01", None, "current", "canonical", 1, None),
                ("asrt-2", "org:partner_advisory", "LEGACY_PRED", "2019-01-01", None, "current", "canonical", 1, '{"stale_after": "2021-01-01"}'),
                ("asrt-3", "org:apex", "DEPLOYED_SYSTEM", "2026-01-01", None, "current", "canonical", 1, None),
            ],
        )
        cur.executemany(
            "INSERT INTO evidence_item VALUES (?, ?, ?)",
            [
                ("ev-1", "exact", "PartnerAdvisory operates in Seattle."),
                ("ev-2", "inexact", ""),  # Inaccurate evidence
            ],
        )
        cur.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-1');")
        cur.execute("INSERT INTO search_document VALUES ('asrt-2', 'Legacy pred content');")
        conn.commit()
        conn.close()

        # 2. Setup mock answer ledger
        answer_db = tmp_path / "answer_ledger.sqlite"
        a_conn = sqlite3.connect(answer_db)
        a_cur = a_conn.cursor()
        a_cur.execute("""
            CREATE TABLE IF NOT EXISTS answer_records (
                answer_id TEXT PRIMARY KEY,
                retrieval_seeds TEXT,
                cited_assertion_ids TEXT
            );
        """)
        a_cur.execute("""
            CREATE TABLE IF NOT EXISTS answer_feedback (
                feedback_id TEXT PRIMARY KEY,
                answer_id TEXT,
                feedback_type TEXT,
                rating TEXT
            );
        """)
        # Populate answer ledger: org:partner_advisory is queried frequently with failure feedback
        a_cur.executemany(
            "INSERT INTO answer_records VALUES (?, ?, ?)",
            [
                ("ans-1", '["org:partner_advisory"]', '["asrt-1"]'),
                ("ans-2", '["org:partner_advisory"]', '["asrt-2"]'),
                ("ans-3", '["org:partner_advisory"]', '["asrt-1"]'),
                ("ans-4", '["org:apex"]', '["asrt-3"]'),
            ],
        )
        a_cur.executemany(
            "INSERT INTO answer_feedback VALUES (?, ?, ?, ?)",
            [
                ("fb-1", "ans-1", "recall_failure", "negative"),
                ("fb-2", "ans-2", "hallucination", "poor"),
            ],
        )
        a_conn.commit()
        a_conn.close()

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "graph_db": graph_db,
            "answer_db": answer_db,
            "integrator": integrator,
        }


def test_freshness_agents_maintenance_telemetry(sandbox_env):
    """Verifies RetrievalSentinel, FreshnessWarden, and AccuracyReviewer emit MAINTENANCE_FINDING events."""
    integrator = sandbox_env["integrator"]
    graph_db = sandbox_env["graph_db"]

    sentinel = RetrievalSentinel(integrator=integrator)
    warden = FreshnessWarden(integrator=integrator)
    reviewer = AccuracyReviewer(integrator=integrator)

    conn = sqlite3.connect(graph_db)

    # 1. Warden scan and telemetry
    policy = GraphFreshnessPolicy(default_ttl_days=90)
    freshness = warden.scan(conn, policy)
    assert "asrt-2" in freshness.stale_assertion_ids
    warden_evt = warden.record_freshness_finding(freshness.stale_assertion_ids, run_id="run-freshness-001")
    assert warden_evt is not None
    assert warden_evt.outcome_type == OutcomeType.MAINTENANCE_FINDING
    assert "asrt-2" in warden_evt.observed_outcome["entity_ids"]

    # 2. Sentinel scan and telemetry
    sentinel_res = sentinel.scan(conn, freshness.stale_assertion_ids)
    assert "asrt-2" in sentinel_res.search_docs_to_purge
    sentinel_evt = sentinel.record_sentinel_finding(sentinel_res.search_docs_to_purge, run_id="run-sentinel-001")
    assert sentinel_evt is not None
    assert sentinel_evt.outcome_type == OutcomeType.MAINTENANCE_FINDING
    assert "asrt-2" in sentinel_evt.observed_outcome["entity_ids"]

    # 3. Accuracy reviewer scan and telemetry
    accuracy = reviewer.scan(conn)
    assert "ev-2" in accuracy.evidence_needing_review
    acc_evt = reviewer.record_accuracy_finding(accuracy.evidence_needing_review, run_id="run-acc-001")
    assert acc_evt is not None
    assert acc_evt.outcome_type == OutcomeType.MAINTENANCE_FINDING
    assert "ev-2" in acc_evt.observed_outcome["entity_ids"]

    conn.close()

    events = integrator.get_recorded_events()
    assert len(events) >= 3


def test_remediation_judge_ttl_proposal_generation(sandbox_env):
    """Verifies RemediationJudge emits schema-valid learning-change-proposal:1.0.0 with auto_apply: false."""
    judge = RemediationJudge()
    freshness = FreshnessFindings(stale_assertion_ids={"asrt-1", "asrt-2", "asrt-3", "asrt-4", "asrt-5", "asrt-6", "asrt-7", "asrt-8", "asrt-9", "asrt-10", "asrt-11"})

    # High stale count (>10) should calibrate TTL downward from 90 to 75
    proposal = judge.generate_ttl_proposal(
        current_ttl_days=90,
        freshness_findings=freshness,
        auto_apply=False,
    )

    assert proposal["schema_version"] == "learning-change-proposal:1.0.0"
    assert proposal["target_parameter"] == "graph_freshness.default_ttl_days"
    assert proposal["current_value"] == 90
    assert proposal["proposed_value"] == 75
    assert proposal["approval_required"] is True
    assert proposal["auto_apply"] is False
    assert proposal["status"] == "proposed"
    assert proposal["drift_cohort_size"] == 11


def test_harm_prioritized_planner_ranks_query_impact(sandbox_env):
    """Verifies HarmPrioritizedPlanner extracts real QueryImpactRecord from answer ledger and prioritizes high-harm findings."""
    answer_db = sandbox_env["answer_db"]

    # Extract empirical impacts from ledger
    impacts = extract_query_impact_records_from_ledger(answer_db)
    assert len(impacts) >= 2
    impact_map = {i.entity_id: i for i in impacts}
    assert "org:partner_advisory" in impact_map
    assert impact_map["org:partner_advisory"].query_frequency == 3
    assert impact_map["org:partner_advisory"].failure_rate > 0.0

    planner = HarmPrioritizedPlanner(run_id="run-test-planner")

    # Finding 1: PartnerAdvisory duplicate (frequently queried with failures)
    f1 = HygieneFinding(
        finding_id="find-partner_advisory-dup",
        category="duplicate_node",
        severity="HIGH",
        target_entities=["org:partner_advisory", "org:partner_advisory_consulting"],
        description="Duplicate PartnerAdvisory entity pair",
        proposed_fix="merge_entities",
        recurrence_count=2,
    )

    # Finding 2: Unqueried or zero-failure clutter
    f2 = HygieneFinding(
        finding_id="find-apex-clutter",
        category="orphan_clutter",
        severity="HIGH",
        target_entities=["org:apex"],
        description="APEX clutter assertion",
        proposed_fix="archive_node",
        recurrence_count=1,
    )

    plan = planner.plan_maintenance([f2, f1], impacts)
    assert len(plan.proposals) == 2

    # High-harm PartnerAdvisory finding MUST rank strictly before APEX clutter
    top_prop = plan.proposals[0]
    second_prop = plan.proposals[1]
    assert "org:partner_advisory" in top_prop.target_entities
    assert top_prop.harm_score > second_prop.harm_score
    assert top_prop.artifact["approval_required"] is True
    assert top_prop.artifact["auto_apply"] is False


def test_continuous_maintenance_swarm_bounded_scan_zero_mutations(sandbox_env):
    """Verifies ContinuousMaintenanceSwarm executes bounded scan and leaves target graph DB unmutated."""
    graph_db = sandbox_env["graph_db"]
    answer_db = sandbox_env["answer_db"]

    initial_hash = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    initial_size = graph_db.stat().st_size

    swarm = ContinuousMaintenanceSwarm(
        agent_count=2,
        db_path=graph_db,
        answer_ledger_path=answer_db,
        turn_budget=2,
    )

    # Execute bounded scan
    proposals = swarm.perform_bounded_scan(
        max_cycles=1,
        clusters=[
            {
                "cluster_id": "cluster_partner_advisory",
                "nodes": ["org:partner_advisory", "org:partner_advisory_consulting"],
                "contention_score": 0.90,
            }
        ],
        db_path=graph_db,
    )

    assert len(proposals) >= 1
    assert proposals[0].harm_score > 0.0
    assert proposals[0].artifact["approval_required"] is True
    assert proposals[0].artifact["auto_apply"] is False

    # Verify physical unmutated invariant: bit-exact SHA-256 and size match
    final_hash = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    final_size = graph_db.stat().st_size
    assert final_hash == initial_hash
    assert final_size == initial_size


def test_synthesis_judge_and_panel_harm_ranking(sandbox_env):
    """Verifies SynthesisJudgeAgent and GraphHygienePanel prioritize directives based on query impact."""
    answer_db = sandbox_env["answer_db"]
    impacts = extract_query_impact_records_from_ledger(answer_db)

    judge = SynthesisJudgeAgent()
    finding_high = Finding(
        detective="StructuralValidator",
        severity="HIGH",
        description="Duplicate entity pair: PartnerAdvisory",
        entity_references=["org:partner_advisory", "org:partner_advisory_consulting"],
        proposed_fix="merge_entities",
        raw_data={"node_id_a": "org:partner_advisory", "node_id_b": "org:partner_advisory_consulting"},
    )
    finding_low = Finding(
        detective="StructuralValidator",
        severity="HIGH",
        description="Clutter node: APEX",
        entity_references=["org:apex"],
        proposed_fix="archive_node",
        raw_data={"edge_count": 0, "status": "active"},
    )

    # Synthesize with query impacts
    pkg = judge.synthesize([finding_low, finding_high], query_impacts=impacts)
    assert pkg.status == "proposed"
    assert pkg.approval_required is True
    assert pkg.auto_apply is False
    assert len(pkg.remediation_directives) == 2

    # High harm (partner_advisory) must rank first
    first_directive = pkg.remediation_directives[0]
    assert "org:partner_advisory" in first_directive["target"]


def test_metric_historian_snapshot_and_telemetry(sandbox_env):
    """Verifies MetricHistorian writes durable snapshots and records outcome telemetry."""
    integrator = sandbox_env["integrator"]
    historian = MetricHistorian(integrator=integrator)
    history_file = sandbox_env["root"] / "metrics" / "graph_hygiene_history.jsonl"

    metrics = {
        "stale_assertions_count": 5,
        "orphaned_evidence_count": 1,
        "duplicate_clusters_count": 2,
    }
    historian.record(metrics, history_file, run_id="run-hist-001")

    assert history_file.is_file()
    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    snapshot = json.loads(lines[0])
    assert snapshot["metrics"]["stale_assertions_count"] == 5

    events = integrator.get_recorded_events()
    hist_evts = [e for e in events if e.source_agent == "MetricHistorian"]
    assert len(hist_evts) == 1
    assert hist_evts[0].outcome_type == OutcomeType.MAINTENANCE_FINDING
