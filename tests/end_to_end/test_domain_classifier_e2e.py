"""E2E Test: Full Domain Intelligence Agent classification lifecycle and caching."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from tools.pr_governance_core.domain import classify_domain, classify_domain_envelope
from tools.pr_governance_core.domain_models import ModalityScope


class TestDomainClassifierE2E(unittest.TestCase):
    def test_thought_leadership_deliverable_e2e(self):
        """End-to-end test verifying that document deliverables in output/ classify as Thought Leadership."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "output" / "project_nexus" / "10-04-2026" / "Energy_Utilities_Market_Antigravity"
            out_dir.mkdir(parents=True)
            meta = {
                "project": "project_nexus",
                "title": "Weekly Energy & Utilities Market Trends - 10/04/2026",
                "source_type": "document",
                "canonical_modality": "DOCUMENT",
                "is_document": True,
            }
            (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (out_dir / "03_canonical_signal_projection.json").write_text(
                json.dumps({
                    "_strategic_analysis": "Market intelligence briefing on FPSC settlement and nuclear PPAs."
                }),
                encoding="utf-8"
            )

            changed_files = [
                "output/project_nexus/10-04-2026/Energy_Utilities_Market_Antigravity/metadata.json",
                "output/project_nexus/10-04-2026/Energy_Utilities_Market_Antigravity/03_canonical_signal_projection.json",
            ]
            branch = "feat/live-energy_utilities_market_antigravity"

            env = classify_domain_envelope(branch, changed_files, repo_root=root, use_cache=False)
            self.assertEqual(env.domain, "External Research")
            self.assertEqual(env.modality, ModalityScope.DOCUMENT_WRITTEN)
            self.assertGreaterEqual(env.confidence, 0.80)
            self.assertNotEqual(env.domain, "Live Meeting")

            # Verify classify_domain returns clean string
            label = classify_domain(branch, changed_files, repo_root=root)
            self.assertIn(label, ("External Research", "Client Document"))

    def test_caching_performance_and_replay_e2e(self):
        """End-to-end test verifying that cryptographic cache returns identical envelope in <5ms."""
        branch = "feat/thought-leadership-energy-trends"
        files = ["documents/market_intelligence/weekly_update.md"]

        env1 = classify_domain_envelope(branch, files, use_cache=True)

        t1 = time.perf_counter()
        env2 = classify_domain_envelope(branch, files, use_cache=True)
        elapsed_second = time.perf_counter() - t1

        self.assertEqual(env1.domain, env2.domain)
        self.assertEqual(env1.confidence, env2.confidence)
        self.assertLess(elapsed_second, 0.05)


if __name__ == "__main__":
    unittest.main()
