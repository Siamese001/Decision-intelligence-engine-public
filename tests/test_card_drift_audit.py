import sqlite3
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from tools.audit_card_drift import audit_all_cards_drift, audit_card_drift
from tools.cards_refactor import _parser, execute

ROOT = Path(__file__).resolve().parent.parent


class TestCardDriftAudit(unittest.TestCase):
    def setUp(self):
        self.runtime_root = ROOT / ".local" / "dual_pipeline"
        self.active_pair = read_json(self.runtime_root / "active_pair.json")
        self.graph_path = self.runtime_root / self.active_pair["graph_path"]
        self.con = sqlite3.connect(f"file:{self.graph_path.as_posix()}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()

    def test_audit_single_card_drift(self):
        report = audit_card_drift(
            self.con,
            card_id="NEE_01",
            generation_id=self.active_pair["generation_id"],
            graph_sha256=self.active_pair["graph_sha256"],
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["card_id"], "NEE_01")
        self.assertIn("active_graph_assertions_count", report)
        self.assertIn("legacy_markdown_propositions_count", report)
        self.assertIn("orphan_legacy_propositions_count", report)

    def test_audit_all_cards_drift(self):
        report = audit_all_cards_drift(self.runtime_root)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["cards_audited_count"], 19)
        self.assertGreater(report["total_active_graph_assertions"], 0)
        self.assertIn("fleet_parity_ratio", report)

    def test_cli_drift_audit(self):
        parser = _parser()
        args = parser.parse_args([
            "--runtime-root", str(self.runtime_root),
            "drift-audit",
            "--card-id", "NEE_01",
        ])
        res = execute(args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["card_id"], "NEE_01")


if __name__ == "__main__":
    unittest.main()
