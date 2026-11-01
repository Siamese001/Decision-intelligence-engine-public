"""E2E: PR queue Domain column classification against the real taxonomy config."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from tools.pr_governance_core.domain import classify_domain, load_taxonomy
from tools.pr_governance_core.inspection import _render_queue_table

ALLOWED = {
    "External Research", "Client Document", "Architecture", "Live Meeting", "Knowledge Graph", "Ingestion", "Decision Intelligence",
    "Performance", "Governance", "Testing", "Bug Fix", "Enhancement", "Refactor", "Housekeeping", "Archive",
}


class TestPRQueueDomainE2E(unittest.TestCase):
    CASES = [
        ("feat/live-transcript-value-refactor-plan", ["dual_pipeline/audio/x.py"], "Live Meeting"),
        ("feat/mlx-whisper-fast-start-arch", ["dual_pipeline/audio_ingestion_core/d.py"], "Architecture"),
        ("feat/git-hook-performance-optimization", ["implementation_plan.md", "reports/r.json"], "Performance"),
        ("fix/graph-hygiene-remediation-10-04-2026", ["output/project_nexus/x.md"], "Client Document"),
        ("feat/bge-m3-fact-vector-ingestion-completeness", ["dual_pipeline/x.py"], "Knowledge Graph"),
        ("feat/hitl-ask-question-council-review", ["storage/a.py"], "Governance"),
        ("feat/live-dociq-q4-scope-bailey-amit", ["output/project_nexus/10-01-2026/s/01_live_transcript.md",
                                                 "output/project_nexus/10-01-2026/s/02_executive_recap.md"], "Live Meeting"),
        ("ingest/doc-market-intelligence-weekly-10-16-2026", ["output/market_intelligence/10-16-2026/weekly/02_executive_recap.md"], "External Research"),
        ("backup/p03-pre-rebase", ["dual_pipeline/live_meeting_recorder.py"], "Archive"),
        ("fix/typo-in-readme", ["README.md"], "Bug Fix"),
        ("feat/new-widget", ["web/widget.ts"], "Enhancement"),
        ("chore/cleanup", [], "Housekeeping"),
        ("feat/misc-change", ["pipeline_b_extraction/a.py", "pipeline_b_extraction/b.py"], "Ingestion"),
    ]

    def test_real_taxonomy_loads(self):
        tax = load_taxonomy()
        self.assertTrue(tax.get("domains"), "config/pr_domain_taxonomy.json must define domains")

    def test_classification_matrix(self):
        for branch, files, expected in self.CASES:
            with self.subTest(branch=branch):
                got = classify_domain(branch, files)
                self.assertEqual(got, expected)
                self.assertIn(got, ALLOWED)

    def test_rendered_table_uses_domain_label_not_rubric_trace(self):
        row = {
            "sub_rank": "P0.1", "readiness": "ready", "review_verdict": "MERGE_READY", "rebase_effort": "NONE",
            "domain_impact": "P0 R3.cost: audio_ingestion_core · 1.4k prod LOC, 107T",
            "domain": classify_domain("feat/live-transcript-value-refactor-plan", []),
            "branch": "feat/live-transcript-value-refactor-plan", "completed_waves": "—", "open_waves": "1",
            "ahead": 1, "behind": 0, "next_action": "[KEEP]", "ranking_rationale": "1st", "scope": "", "registry_rationale": "",
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_queue_table([row])
        out = buf.getvalue()
        self.assertIn("| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |", out)
        self.assertIn("| **1** | feat/live-transcript-value-refactor-plan | Live Meeting |", out)
        self.assertNotIn("1.4k prod LOC", out)


if __name__ == "__main__":
    unittest.main()
