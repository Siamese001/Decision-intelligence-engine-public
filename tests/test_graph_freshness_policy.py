"""Unit tests for Graph Freshness Policy Registry."""
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.policy import load_policy


class TestGraphFreshnessPolicy(unittest.TestCase):
    def test_default_policy(self):
        policy = load_policy()
        self.assertEqual(policy.supersession_strategy, "newest_source_unless_authority_differs")
        self.assertEqual(policy.default_ttl_days, 90)
        self.assertEqual(policy.orphaned_evidence_strategy, "tombstone_and_purge")

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "policy.json"
            with open(p, "w") as f:
                json.dump({"default_ttl_days": 30, "orphaned_evidence_strategy": "soft_delete_quarantine"}, f)

            policy = load_policy(p)
            self.assertEqual(policy.default_ttl_days, 30)
            self.assertEqual(policy.orphaned_evidence_strategy, "soft_delete_quarantine")
            self.assertEqual(policy.supersession_strategy, "newest_source_unless_authority_differs")
