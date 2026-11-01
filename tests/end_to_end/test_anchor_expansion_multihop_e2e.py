"""End-to-End multi-hop retrieval and leak validation on live graph (Wave 3)."""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from dual_pipeline.index_core.anchor_expansion import expand_retrieval_anchors

ROOT = Path(__file__).resolve().parents[2]


class TestAnchorExpansionMultihopE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = ROOT / ".local" / "dual_pipeline"
        if not self.runtime_root.is_dir():
            self.skipTest(".local/dual_pipeline directory not present")

        active_pair = read_json(self.runtime_root / "active_pair.json")
        self.graph_path = self.runtime_root / active_pair["graph_path"]
        self.sidecar_path = self.runtime_root / active_pair["sidecar_path"]
        self.qrels_path = ROOT / "reports" / "edge_qrels_dataset.json"

        if not self.graph_path.is_file() or not self.sidecar_path.is_file():
            self.skipTest("Active graph or sidecar sqlite file missing")
        if not self.qrels_path.is_file():
            self.skipTest("Edge QRELS dataset file missing")

        with open(self.qrels_path) as f:
            self.qrels = json.load(f)

    def test_wave2_hop2_slice_recall_floor(self) -> None:
        """Hop-2 recall@10 on the Wave 2 hop-2 slice must be >= 0.80."""
        hop2_cases = [c for c in self.qrels.get("edge_cases", []) if c.get("query_form") == "hop_2"]
        self.assertGreaterEqual(len(hop2_cases), 20, f"Insufficient hop-2 cases: {len(hop2_cases)}")

        s_conn = sqlite3.connect(f"file:{self.sidecar_path}?mode=ro", uri=True)
        hits_10 = 0
        total_eval = 0
        all_expanded_evidence_ids: list[str] = []

        # Robust graph-backed subject node resolution
        g_conn = sqlite3.connect(f"file:{self.graph_path}?mode=ro", uri=True)
        try:
            from dual_pipeline.core import stable_id
            aid_rows = g_conn.execute("SELECT assertion_id, subject_node_id FROM assertion").fetchall()
            rid_to_subj = {stable_id("retrieval-record", aid, length=28): sn for aid, sn in aid_rows if sn}
        finally:
            g_conn.close()

        try:
            for case in hop2_cases:
                query = case["query"]
                exp_ids = set(case.get("expected_record_ids", []))
                first_exp = case["expected_record_ids"][0]
                art_row = s_conn.execute(
                    "SELECT artifact_json FROM retrieval_record WHERE record_id=?", (first_exp,)
                ).fetchone()
                subj_node = None
                if art_row:
                    first_art = json.loads(art_row[0])
                    subj_node = first_art.get("canonical_entities", [None])[0]
                if not subj_node:
                    subj_node = rid_to_subj.get(first_exp)
                if not subj_node:
                    continue

                anchor_item = {
                    "record_id": f"profile_{subj_node}",
                    "lane": "approved",
                    "fused_score": 1.0,
                    "record": {
                        "record_type": "entity_facet",
                        "retrieval_kind": "entity_facet",
                        "classification": "internal",
                        "audiences": ["internal_consulting"],
                        "extensions": {"com.partner_advisory.nee.graph-entity-ids": [subj_node]},
                    },
                }
                expanded = expand_retrieval_anchors(
                    [anchor_item],
                    self.graph_path,
                    lane="approved",
                    classification="internal",
                    audiences=["internal_consulting"],
                    max_facts_per_anchor=15,
                    hop_budget=2,
                    query=query,
                )
                retrieved_ids = [it.get("canonical_record_id") or it.get("record_id") for it in expanded]

                total_eval += 1
                if any(r in exp_ids for r in retrieved_ids[:10]):
                    hits_10 += 1

                for it in expanded:
                    if it.get("is_anchor_expanded"):
                        eids = it.get("record", {}).get("evidence_ids", [])
                        all_expanded_evidence_ids.extend(eids)

        finally:
            s_conn.close()

        measured_recall_10 = hits_10 / total_eval if total_eval > 0 else 0.0
        self.assertGreaterEqual(
            measured_recall_10, 0.80,
            f"Wave 3 Acceptance Failure: hop-2 recall@10 is {measured_recall_10:.4f}, expected >= 0.80",
        )

        # Acceptance Criteria Check: 100% real evidence ID provenance
        self.assertTrue(len(all_expanded_evidence_ids) > 0)
        synthetic_eids = [eid for eid in all_expanded_evidence_ids if "evidence-ref" in str(eid)]
        self.assertEqual(
            len(synthetic_eids), 0,
            f"Wave 3 Acceptance Failure: found {len(synthetic_eids)} synthetic evidence IDs!",
        )

    def test_competency_rb015_multihop_reporting_chain(self) -> None:
        """RB-015 reporting chain (Mark McCarthy -> Chakra -> Richard) reaches both hops."""
        rb15 = next(
            (c for c in self.qrels.get("competency_cases", []) if c.get("case_id") == "RB-015"),
            None,
        )
        self.assertIsNotNone(rb15, "RB-015 not found in competency cases")

        mark_node = "person_mark_mccarthy"
        anchor_item = {
            "record_id": f"profile_{mark_node}",
            "lane": "approved",
            "fused_score": 1.0,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": [mark_node]},
            },
        }
        expanded = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            max_facts_per_anchor=15,
            hop_budget=2,
            query=rb15["question"],
        )
        retrieved_ids = [it.get("canonical_record_id") or it.get("record_id") for it in expanded]
        expected_ids = rb15["expected_record_ids"]

        for exp_id in expected_ids:
            self.assertIn(
                exp_id, retrieved_ids[:10],
                f"Competency RB-015 failure: expected record {exp_id} not in top-10 retrieved!",
            )

    def test_zero_ineligible_leaks_on_real_graph(self) -> None:
        """Expansion on real graph must return 0 ineligible or needs_review edges in approved lane."""
        anchor_item = {
            "record_id": "profile_partner_advisory",
            "lane": "approved",
            "fused_score": 1.0,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["org_partner_advisory"]},
            },
        }
        expanded = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            max_facts_per_anchor=50,
            hop_budget=2,
        )
        g_conn = sqlite3.connect(f"file:{self.graph_path}?mode=ro", uri=True)
        try:
            for it in expanded:
                if not it.get("is_anchor_expanded"):
                    continue
                aid = it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
                row = g_conn.execute(
                    "SELECT canonical_status, validation_state, retrieval_eligible, currentness_state FROM assertion WHERE assertion_id=?",
                    (aid,),
                ).fetchone()
                self.assertIsNotNone(row, f"Assertion {aid} not found in graph!")
                c_status, v_state, r_elig, curr = row
                self.assertEqual(c_status, "canonical", f"Non-canonical assertion {aid} in approved lane!")
                self.assertIn(v_state, ["approved", "accepted_legacy"], f"Unapproved assertion {aid} with status {v_state} in approved lane!")
                self.assertEqual(r_elig, 1, f"Ineligible assertion {aid} in approved lane!")
                self.assertIn(curr, ["current", "unknown"], f"Historical assertion {aid} in approved lane!")
        finally:
            g_conn.close()


if __name__ == "__main__":
    unittest.main()
