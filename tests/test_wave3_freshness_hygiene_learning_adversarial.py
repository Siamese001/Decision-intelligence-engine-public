"""
tests/test_wave3_freshness_hygiene_learning_adversarial.py

Adversarial and edge-case verification for Wave 3: Graph Freshness Decay & Harm-Prioritized Hygiene Learning.
Covers 5 required governance edge-case categories:
1. Boundary payloads: empty findings, empty/missing ledger, empty search documents, zero stale assertions.
2. Malformed input & corrupt state: corrupt JSON syntax in answer records, missing tables, invalid types.
3. Concurrency & race: multi-threaded outcome emission across freshness agents to LivePipelineIntegrator.
4. Invariant bypass attempts: auto_apply: true forbidden in RemediationJudge and validate_proposal_authenticity.
5. Rollback & physical unmutated invariant: bit-exact SHA-256 preservation across multiple swarm scan cycles.
"""
from __future__ import annotations

import concurrent.futures
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
from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
    extract_query_impact_records_from_ledger,
)
from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    SynthesisJudgeAgent,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.maintenance_swarm import (
    ContinuousMaintenanceSwarm,
    validate_proposal_authenticity,
)


@pytest.fixture
def adversarial_env():
    """Provides isolated sandbox environment for adversarial testing."""
    with tempfile.TemporaryDirectory(prefix="wave3_adv_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)

        graph_db = tmp_path / "adversarial_graph.db"
        conn = sqlite3.connect(graph_db)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("CREATE TABLE canonical_entities (id TEXT PRIMARY KEY, name TEXT);")
        cur.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT);")
        cur.executemany(
            "INSERT INTO canonical_entities VALUES (?, ?)",
            [
                ("org:alpha", "Alpha Corp"),
                ("org:beta", "Beta Corp"),
            ],
        )
        conn.commit()
        conn.close()

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "graph_db": graph_db,
            "integrator": integrator,
        }


# ==============================================================================
# Category 1: Boundary Payloads
# ==============================================================================


def test_boundary_empty_inputs(adversarial_env):
    """Category 1: Verifies empty findings, empty/missing ledger, and empty search documents."""
    root = adversarial_env["root"]
    integrator = adversarial_env["integrator"]

    # 1. Non-existent answer ledger returns empty list gracefully
    non_existent = root / "does_not_exist.sqlite"
    assert extract_query_impact_records_from_ledger(non_existent) == []

    # 2. Empty findings list into SynthesisJudgeAgent
    judge = SynthesisJudgeAgent()
    pkg = judge.synthesize([], query_impacts=[])
    assert pkg.status == "proposed"
    assert pkg.findings == []
    assert pkg.remediation_directives == []

    # 3. Empty search docs into RetrievalSentinel
    sentinel = RetrievalSentinel(integrator=integrator)
    sent_evt = sentinel.record_sentinel_finding(set())
    # Emits maintenance finding with empty entity_ids
    assert sent_evt is not None
    assert sent_evt.observed_outcome["entity_ids"] == []

    # 4. Zero stale assertions expands TTL upward within max bounds
    rem_judge = RemediationJudge()
    zero_freshness = FreshnessFindings(stale_assertion_ids=set())
    prop = rem_judge.generate_ttl_proposal(current_ttl_days=90, freshness_findings=zero_freshness, max_ttl_days=120)
    assert prop["current_value"] == 90
    assert prop["proposed_value"] == 105
    assert prop["drift_cohort_size"] == 0


# ==============================================================================
# Category 2: Malformed Input & Corrupt State
# ==============================================================================


def test_malformed_ledger_content(adversarial_env):
    """Category 2: Verifies graceful degradation when answer ledger contains malformed JSON or schema."""
    root = adversarial_env["root"]
    corrupt_db = root / "corrupt_ledger.sqlite"

    conn = sqlite3.connect(corrupt_db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE answer_records (answer_id TEXT, retrieval_seeds TEXT, cited_assertion_ids TEXT);")
    cur.execute("CREATE TABLE answer_feedback (answer_id TEXT, feedback_type TEXT, rating TEXT);")

    # Insert corrupt non-JSON strings and invalid types
    cur.executemany(
        "INSERT INTO answer_records VALUES (?, ?, ?)",
        [
            ("ans-1", "NOT_JSON_AT_ALL {{{", "CORRUPT_CITE [["),
            ("ans-2", None, None),
            ("ans-3", '["org:valid"]', '["asrt-valid"]'),
        ],
    )
    cur.execute("INSERT INTO answer_feedback VALUES ('ans-3', 'recall_failure', 'negative');")
    conn.commit()
    conn.close()

    # Must extract without throwing exceptions, treating raw strings gracefully
    impacts = extract_query_impact_records_from_ledger(corrupt_db)
    assert len(impacts) >= 1
    ent_ids = {i.entity_id for i in impacts}
    assert "org:valid" in ent_ids


# ==============================================================================
# Category 3: Concurrency & Race Conditions
# ==============================================================================


def test_concurrent_maintenance_outcome_recording(adversarial_env):
    """Category 3: Verifies multi-threaded outcome recording across freshness agents."""
    integrator = adversarial_env["integrator"]
    root = adversarial_env["root"]

    sentinel = RetrievalSentinel(integrator=integrator)
    warden = FreshnessWarden(integrator=integrator)
    reviewer = AccuracyReviewer(integrator=integrator)
    historian = MetricHistorian(integrator=integrator)
    history_file = root / "metrics" / "concurrent_history.jsonl"

    def worker(worker_id: int):
        sentinel.record_sentinel_finding(
            {f"doc_{worker_id}"},
            run_id=f"run-sentinel-{worker_id}",
        )
        warden.record_freshness_finding(
            {f"asrt_{worker_id}"},
            run_id=f"run-warden-{worker_id}",
        )
        reviewer.record_accuracy_finding(
            {f"ev_{worker_id}"},
            run_id=f"run-rev-{worker_id}",
        )
        historian.record(
            {f"metric_{worker_id}": worker_id},
            history_file,
            run_id=f"run-hist-{worker_id}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    log_path = integrator.events_log_path
    assert log_path.is_file()
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 40


# ==============================================================================
# Category 4: Invariant Bypass Attempts (Anti-Forgery)
# ==============================================================================


def test_anti_forgery_auto_apply_rejection():
    """Category 4: Verifies auto_apply: true attempts are strictly blocked with PermissionError."""
    judge = RemediationJudge()
    freshness = FreshnessFindings(stale_assertion_ids={"asrt-1"})

    # 1. RemediationJudge must refuse to generate proposal with auto_apply=True
    with pytest.raises(PermissionError, match="FORGERY_DETECTED"):
        judge.generate_ttl_proposal(
            current_ttl_days=90,
            freshness_findings=freshness,
            auto_apply=True,
        )

    # 2. validate_proposal_authenticity rejects forged proposal artifacts
    planner = HarmPrioritizedPlanner(run_id="run-test")
    finding = HygieneFinding(
        finding_id="f-1",
        category="duplicate_node",
        severity="HIGH",
        target_entities=["org:a", "org:b"],
        description="Duplicate",
        proposed_fix="merge",
    )
    plan = planner.plan_maintenance([finding], [])
    proposal = plan.proposals[0]

    # Authentic proposal passes
    assert validate_proposal_authenticity(proposal) is True

    # Tampered proposal asserting auto_apply=True fails closed
    proposal.artifact["auto_apply"] = True
    with pytest.raises(PermissionError, match="FORGERY_DETECTED"):
        validate_proposal_authenticity(proposal)

    # Tampered proposal asserting approval_required=False fails closed
    proposal.artifact["auto_apply"] = False
    proposal.artifact["approval_required"] = False
    with pytest.raises(PermissionError, match="FORGERY_DETECTED"):
        validate_proposal_authenticity(proposal)


# ==============================================================================
# Category 5: Rollback & Physical Unmutated Invariant
# ==============================================================================


def test_swarm_physical_unmutated_invariant(adversarial_env):
    """Category 5: Verifies that multiple swarm scanning cycles leave database bytes 100% untouched."""
    graph_db = adversarial_env["graph_db"]

    initial_bytes = graph_db.read_bytes()
    initial_hash = hashlib.sha256(initial_bytes).hexdigest()
    initial_size = len(initial_bytes)

    swarm = ContinuousMaintenanceSwarm(
        agent_count=3,
        db_path=graph_db,
        turn_budget=3,
    )

    # Perform multi-cycle scan
    proposals = swarm.perform_bounded_scan(
        max_cycles=3,
        clusters=[
            {"cluster_id": "c1", "nodes": ["org:alpha", "org:beta"], "contention_score": 0.88},
            {"cluster_id": "c2", "nodes": ["org:gamma", "org:delta"], "contention_score": 0.72},
        ],
        db_path=graph_db,
    )

    assert len(proposals) >= 2

    # Physical byte scan
    final_bytes = graph_db.read_bytes()
    final_hash = hashlib.sha256(final_bytes).hexdigest()
    final_size = len(final_bytes)

    assert final_hash == initial_hash
    assert final_size == initial_size
    assert final_bytes == initial_bytes
