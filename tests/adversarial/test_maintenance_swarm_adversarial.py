"""tests/adversarial/test_maintenance_swarm_adversarial.py

Adversarial and boundary test suite for ContinuousMaintenanceSwarm and MaintenanceSubAgent.
Verifies zero unauthorized graph mutations, RFC-8259 contract compliance,
anti-forgery validation, empty cluster resilience, and bounded loop execution.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    RankedMaintenanceProposal,
)
from dual_pipeline.maintenance_swarm import (
    ContinuousMaintenanceSwarm,
    MaintenanceSubAgent,
    validate_proposal_authenticity,
)


class TestMaintenanceSwarmAdversarial(unittest.TestCase):
    """Adversarial suite verifying physical proof of zero writes, proposal anti-forgery, and bounds."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_maint_adv_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Provision a test SQLite DB with tables and records
        self.test_db = self.temp_dir / "test_graph.sqlite"
        conn = sqlite3.connect(self.test_db)
        cur = conn.cursor()
        cur.execute("CREATE TABLE canonical_entities (id TEXT PRIMARY KEY, name TEXT)")
        cur.execute("INSERT INTO canonical_entities VALUES ('org:partner_advisory', 'PartnerAdvisory')")
        cur.execute("INSERT INTO canonical_entities VALUES ('org:partner_advisory_consulting', 'partner_advisory')")
        cur.execute("INSERT INTO canonical_entities VALUES ('client:acme', 'Acme Corp')")
        conn.commit()
        conn.close()

    def test_adversarial_zero_database_mutations_physical_proof(self):
        """Verifies physical proof of zero database writes across bounded maintenance scan."""
        # 1. Capture initial size and SHA-256
        with open(self.test_db, "rb") as f:
            initial_bytes = f.read()
        initial_hash = hashlib.sha256(initial_bytes).hexdigest()
        initial_size = len(initial_bytes)

        # 2. Run maintenance agent with bounded scan
        agent = MaintenanceSubAgent("adv-agent-01", turn_budget=3, db_path=self.test_db)
        proposals = agent.perform_bounded_scan(max_cycles=2, db_path=self.test_db)

        # 3. Independent physical readback and byte comparison
        with open(self.test_db, "rb") as f:
            final_bytes = f.read()
        final_hash = hashlib.sha256(final_bytes).hexdigest()
        final_size = len(final_bytes)

        self.assertEqual(initial_hash, final_hash, "Database SHA-256 changed: unauthorized write occurred!")
        self.assertEqual(initial_size, final_size, "Database file size changed: unauthorized write occurred!")

        # 4. Verify integrity
        conn = sqlite3.connect(f"file:{self.test_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        status = cur.fetchone()[0]
        conn.close()
        self.assertEqual(status, "ok")

        # 5. Verify proposals generated cleanly without mutation
        self.assertGreaterEqual(len(proposals), 1)
        for p in proposals:
            self.assertTrue(p.artifact["approval_required"])
            self.assertFalse(p.artifact["auto_apply"])

    def test_adversarial_forged_auto_apply_proposal_rejection(self):
        """Verify that forged proposals asserting auto_apply=True fail closed with PermissionError."""
        planner = HarmPrioritizedPlanner(run_id="adv-planner")
        prop = RankedMaintenanceProposal(
            proposal_id="learning-proposal-forged-01",
            harm_score=12.5,
            target_entities=["org:partner_advisory"],
            category="duplicate_node",
            severity="HIGH",
            recurrence_count=1,
            query_impact_score=1.0,
            artifact={
                "contract_id": "urn:nee:dual-pipeline:learning-change-proposal:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": "learning-proposal-forged-01",
                "approval_required": True,
                "auto_apply": True,  # FORGERY: auto_apply asserted without human approval
                "status": "proposed",
            },
        )
        with self.assertRaises(PermissionError) as ctx:
            validate_proposal_authenticity(prop)
        self.assertIn("FORGERY_DETECTED", str(ctx.exception))

    def test_adversarial_forged_unapproved_proposal_rejection(self):
        """Verify that proposals omitting approval_required fail closed."""
        prop = RankedMaintenanceProposal(
            proposal_id="learning-proposal-forged-02",
            harm_score=8.0,
            target_entities=["org:partner_advisory"],
            category="duplicate_node",
            severity="HIGH",
            recurrence_count=1,
            query_impact_score=1.0,
            artifact={
                "contract_id": "urn:nee:dual-pipeline:learning-change-proposal:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": "learning-proposal-forged-02",
                "approval_required": False,  # FORGERY: approval bypass attempt
                "auto_apply": False,
                "status": "proposed",
            },
        )
        with self.assertRaises(PermissionError) as ctx:
            validate_proposal_authenticity(prop)
        self.assertIn("FORGERY_DETECTED", str(ctx.exception))

    def test_adversarial_tampered_status_and_harm_score_rejection(self):
        """Verify that tampered statuses or out-of-bounds harm scores fail closed."""
        # Status not 'proposed'
        prop_applied = RankedMaintenanceProposal(
            proposal_id="prop-tampered-status",
            harm_score=5.0,
            target_entities=["org:partner_advisory"],
            category="duplicate_node",
            severity="HIGH",
            recurrence_count=1,
            query_impact_score=1.0,
            artifact={
                "approval_required": True,
                "auto_apply": False,
                "status": "applied",  # Unauthorized status
            },
        )
        with self.assertRaises(ValueError):
            validate_proposal_authenticity(prop_applied)

        # Negative harm score
        prop_negative = RankedMaintenanceProposal(
            proposal_id="prop-tampered-score",
            harm_score=-10.0,
            target_entities=["org:partner_advisory"],
            category="duplicate_node",
            severity="HIGH",
            recurrence_count=1,
            query_impact_score=1.0,
            artifact={
                "approval_required": True,
                "auto_apply": False,
                "status": "proposed",
            },
        )
        with self.assertRaises(ValueError):
            validate_proposal_authenticity(prop_negative)

    def test_adversarial_empty_clusters_handling(self):
        """Verify that 0 clusters or empty scan terminates gracefully without spurious proposals."""
        agent = MaintenanceSubAgent("adv-agent-empty", turn_budget=3)
        proposals = agent.perform_bounded_scan(max_cycles=2, clusters=[])
        self.assertEqual(len(proposals), 0)

    def test_adversarial_turn_budget_enforcement_and_loop_exhaustion(self):
        """Verify that scan strictly terminates within allocated turn budget."""
        clusters = [
            {"cluster_id": "c1", "nodes": ["a", "b"], "contention_score": 0.8},
        ]
        agent = MaintenanceSubAgent("adv-agent-budget", turn_budget=2)
        # Attempt to run 50 cycles; agent must cap at turn_budget=2
        proposals = agent.perform_bounded_scan(max_cycles=50, clusters=clusters)
        self.assertLessEqual(len(proposals), 2)

    def test_adversarial_proposal_rfc8259_contract_compliance(self):
        """Verify that generated proposals serialize cleanly into valid RFC-8259 JSON."""
        agent = MaintenanceSubAgent("adv-agent-rfc", turn_budget=1)
        proposals = agent.perform_bounded_scan(max_cycles=1)
        self.assertEqual(len(proposals), 1)

        artifact = proposals[0].artifact
        # Ensure RFC-8259 serialization
        json_output = json.dumps(artifact)
        self.assertTrue(len(json_output) > 50)
        parsed = json.loads(json_output)
        self.assertEqual(parsed["approval_required"], True)
        self.assertEqual(parsed["auto_apply"], False)
        self.assertEqual(parsed["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
